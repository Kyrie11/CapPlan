"""Dataset loader for CASA training."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

from capplan.data.capability_contracts import contract_episode_id
from capplan.data.schemas import contract_from_dict, transition_from_dict
from capplan.models.casa_features import FeatureVocab, encode_transition_with_capability
from capplan.semantics.resource_registry import DEFAULT_REGISTRY
from capplan.semantics.capability_compiler import CapabilityCompiler
from capplan.utils.serialization import read_jsonl


# Resource-specific normalizers used by the paper-facing typed-demand loss.
# Values are representative service scales (not feasibility thresholds): they
# put metres, seconds, ratios, acceleration and probabilities on comparable
# optimization scales while keeping model outputs in the original physical
# units consumed by TSBS.
DEMAND_NORMALIZERS: Dict[str, float] = {
    "access_distance_m": 100.0,
    "egress_distance_m": 100.0,
    "crossing_count": 1.0,
    "wait_exposure_s": 300.0,
    "motion_exposure": 1.0,
    "ride_time_s": 600.0,
    "dwell_time_s": 60.0,
    "slope": 0.10,
    "cross_slope": 0.05,
    "curb_height_m": 0.10,
    "peak_accel_mps2": 2.0,
    "peak_jerk_mps3": 3.0,
    "path_width_m": 1.0,
    "door_width_m": 1.0,
    "door_side_clearance_m": 1.0,
    "deployment_clearance_m": 1.0,
    "ramp_clearance_m": 1.0,
    "map_confidence": 1.0,
    "dynamic_confidence": 1.0,
    "blockage_risk": 1.0,
    "deployment_risk": 1.0,
    "availability_risk": 1.0,
}


def demand_scale_vector(resources: List[str]) -> List[float]:
    """Return stable per-resource scales for normalized Huber/calibration losses.

    Categorical resources are not numerical demand-regression targets. They
    remain represented through capability/interface predicates and edge labels.
    Unknown future numeric resources default to unit scale rather than silently
    introducing a zero divisor.
    """
    return [max(float(DEMAND_NORMALIZERS.get(str(name), 1.0)), 1e-6) for name in resources]


@dataclass
class CASASample:
    transition_id: str
    episode_id: str
    passenger_id: str
    x: List[float]
    y_edge: float
    y_value: float
    y_phase: int
    y_demand: List[float]
    demand_mask: List[float]
    uncertainty_beta: List[float] = field(default_factory=list)
    y_availability: float = 1.0


class CASADataset:
    """Passenger-conditioned CASA samples.

    One sample is emitted per ``(transition, passenger contract)`` pair.  The edge
    target is the passenger-specific oracle label ``y_e,p`` when available, not
    the passenger-independent transition label ``z_e``.  This is required by the
    paper idea: CASA should learn capability-conditioned service feasibility.
    """

    def __init__(
        self,
        dataset_dir: str | Path,
        split: str = "train",
        vocab: FeatureVocab | None = None,
        *,
        value_target: str = "skeleton",
        feature_policy: str = "legacy",
    ) -> None:
        self.dataset_dir = Path(dataset_dir)
        self.vocab = vocab or FeatureVocab()
        self.compiler = CapabilityCompiler()
        self.value_target = value_target
        self.feature_policy = feature_policy
        if value_target not in {"skeleton", "offline_tsbs", "rollout", "legacy"}:
            raise ValueError(f"unknown CASA completion-value target: {value_target}")
        if feature_policy not in {"legacy", "paper_safe", "paper_safe_v2"}:
            raise ValueError(f"unknown CASA feature policy: {feature_policy}")
        self.split_file = self.dataset_dir / "splits" / f"{split}_episodes.txt"
        split_ids = self._read_split(split)
        transition_labels = {d["transition_id"]: d for d in read_jsonl(self.dataset_dir / "transition_labels.jsonl")}
        passenger_path = self.dataset_dir / "passenger_edge_labels.jsonl"
        passenger_labels: Dict[Tuple[str, str], Dict] = {}
        if passenger_path.exists():
            for row in read_jsonl(passenger_path):
                key = (str(row.get("transition_id") or ""), str(row.get("passenger_id") or ""))
                if not all(key):
                    raise RuntimeError(f"invalid passenger edge label row in {passenger_path}: transition_id and passenger_id are required")
                if key in passenger_labels:
                    raise RuntimeError(f"duplicate passenger edge label for {key} in {passenger_path}")
                passenger_labels[key] = row
        elif self.feature_policy in {"paper_safe", "paper_safe_v2"}:
            raise RuntimeError(f"paper_safe CASA loading requires passenger-specific labels: {passenger_path}")
        contracts_by_episode: Dict[str, List] = {}
        contracts_path = self.dataset_dir / "capability_contracts.jsonl"
        if contracts_path.exists():
            for d in read_jsonl(contracts_path):
                c = contract_from_dict(d)
                eid = contract_episode_id(c)
                if split_ids and eid not in split_ids:
                    continue
                contracts_by_episode.setdefault(eid, []).append(c)
        skeleton_edges: Dict[Tuple[str, str], set[str]] = {}
        skeleton_path = self.dataset_dir / "skeleton_labels.jsonl"
        if skeleton_path.exists():
            for row in read_jsonl(skeleton_path):
                skeleton_edges[(row.get("episode_id"), row.get("passenger_id"))] = set(row.get("transitions") or [])
        explicit_value_targets = self._read_explicit_value_targets(value_target)
        self.samples: List[CASASample] = []
        for d in read_jsonl(self.dataset_dir / "candidate_transitions.jsonl"):
            t = transition_from_dict(d)
            if split_ids and t.episode_id not in split_ids:
                continue
            contracts = contracts_by_episode.get(t.episode_id, [])
            if not contracts:
                if self.feature_policy in {"paper_safe", "paper_safe_v2"}:
                    raise RuntimeError(
                        f"paper_safe CASA loading found transition {t.transition_id} in episode {t.episode_id} "
                        "without any passenger capability contract"
                    )
                # Backward-compatible fallback for legacy transition-only data.
                lab = transition_labels.get(t.transition_id, {})
                y_edge = 1.0 if lab.get("z_e", t.tests.z_e) else 0.0
                if value_target == "legacy":
                    y_value = max(0.0, min(1.0, t.completion_value))
                elif value_target == "skeleton":
                    y_value = 0.0
                else:
                    key = (t.transition_id, "__transition_only__")
                    if key not in explicit_value_targets:
                        raise RuntimeError(f"missing {value_target} completion-value label for transition-only sample {key}")
                    y_value = explicit_value_targets[key]
                y_phase = self.vocab.phases.index(t.to_phase) if t.to_phase in self.vocab.phases else 0
                yd, ym = self._demand_target(t)
                ub = [1.0 for _ in self.vocab.resources]
                self.samples.append(CASASample(t.transition_id, t.episode_id, "__transition_only__", encode_transition_with_capability(t, [], self.vocab, feature_policy=self.feature_policy), y_edge, y_value, y_phase, yd, ym, ub, max(0.0, min(1.0, float(t.availability)))))
                continue
            for contract in contracts:
                compiled = self.compiler.compile(contract, trip_context=contract.metadata.get("trip_modifiers", {}))
                plab = passenger_labels.get((t.transition_id, contract.passenger_id))
                if plab is not None:
                    y_edge = 1.0 if plab.get("y_e_p") else 0.0
                else:
                    if self.feature_policy in {"paper_safe", "paper_safe_v2"}:
                        raise RuntimeError(
                            "paper_safe CASA loading requires a passenger-conditioned edge label for every "
                            f"(transition, passenger) pair; missing {(t.transition_id, contract.passenger_id)}"
                        )
                    lab = transition_labels.get(t.transition_id, {})
                    y_edge = 1.0 if lab.get("z_e", t.tests.z_e) else 0.0
                in_skeleton = t.transition_id in skeleton_edges.get((t.episode_id, contract.passenger_id), set())
                if value_target == "skeleton":
                    # Paper Eq. L_value explicitly allows expert/audited skeleton
                    # supervision.  Use a pure binary target; do not blend in the
                    # transition's hand-authored completion_value prior.
                    y_value = 1.0 if in_skeleton else 0.0
                elif value_target == "legacy":
                    if in_skeleton:
                        y_value = 1.0
                    elif y_edge > 0.5:
                        y_value = max(0.05, min(0.95, float(t.completion_value)))
                    else:
                        y_value = 0.0
                else:
                    key = (t.transition_id, contract.passenger_id)
                    if key not in explicit_value_targets:
                        raise RuntimeError(f"missing {value_target} completion-value label for {key}")
                    y_value = explicit_value_targets[key]
                y_phase = self.vocab.phases.index(t.to_phase) if t.to_phase in self.vocab.phases else 0
                yd, ym = self._demand_target(t)
                ub = self._uncertainty_beta(compiled.tokens)
                self.samples.append(CASASample(t.transition_id, t.episode_id, contract.passenger_id, encode_transition_with_capability(t, compiled.tokens, self.vocab, feature_policy=self.feature_policy), y_edge, y_value, y_phase, yd, ym, ub, max(0.0, min(1.0, float(t.availability)))))

    def _read_split(self, split: str) -> set[str]:
        p = self.dataset_dir / "splits" / f"{split}_episodes.txt"
        if not p.exists():
            return set()
        return {line.strip() for line in p.read_text(encoding="utf-8").splitlines() if line.strip()}

    def _read_explicit_value_targets(self, value_target: str) -> Dict[Tuple[str, str], float]:
        if value_target not in {"offline_tsbs", "rollout"}:
            return {}
        path = self.dataset_dir / f"completion_value_labels.{value_target}.jsonl"
        if not path.exists():
            raise RuntimeError(
                f"--value_target {value_target} requires explicit labels at {path}; "
                "the previous implementation only checked the CLI flag and silently reused skeleton/heuristic priors"
            )
        out: Dict[Tuple[str, str], float] = {}
        for row in read_jsonl(path):
            tid = str(row.get("transition_id") or "")
            pid = str(row.get("passenger_id") or "")
            if not tid or not pid:
                raise RuntimeError(f"invalid completion-value label row in {path}: transition_id and passenger_id are required")
            raw = row.get("target", row.get("completion_value_target"))
            if raw is None:
                raise RuntimeError(f"invalid completion-value label row in {path}: target is required")
            value = float(raw)
            if not 0.0 <= value <= 1.0:
                raise RuntimeError(f"invalid completion-value target {value} for {(tid, pid)}; expected [0,1]")
            out[(tid, pid)] = value
        return out


    def _uncertainty_beta(self, tokens) -> List[float]:
        """Return per-resource conservative uncertainty multipliers from Psi_p.

        The paper calibration term is resource-wise: beta_tau * sigma_e^tau must
        cover the residual of the corresponding typed demand.  Multiple active
        clauses for one resource are combined conservatively with max(beta_tau).
        """
        beta = [1.0 for _ in self.vocab.resources]
        for tok in tokens or []:
            try:
                rid = int(tok.get("resource_id", -1))
                b = max(0.0, float(tok.get("beta_tau", 1.0)))
            except Exception:
                continue
            if 0 <= rid < len(beta):
                beta[rid] = max(beta[rid], b)
        return beta


    def _demand_target(self, t) -> Tuple[List[float], List[float]]:
        values = [0.0 for _ in self.vocab.resources]
        masks = [0.0 for _ in self.vocab.resources]
        resource_index = {name: i for i, name in enumerate(self.vocab.resources)}
        for ev in t.resource_evidence:
            if ev.resource_name not in resource_index or ev.missing or ev.value is None:
                continue
            # Paper typed-demand regression is numerical. Boolean/category
            # compatibility is a symbolic/interface predicate and must not be
            # coerced into a pseudo-continuous 0/1 regression target.
            if DEFAULT_REGISTRY.has(ev.resource_name) and DEFAULT_REGISTRY.get(ev.resource_name).kind == "categorical":
                continue
            idx = resource_index[ev.resource_name]
            try:
                value = float(ev.value)
            except Exception:
                continue
            if not np.isfinite(value):
                continue
            values[idx] = value
            masks[idx] = 1.0
        return values, masks

    def arrays(self) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        x = np.array([s.x for s in self.samples], dtype=np.float32)
        y_edge = np.array([s.y_edge for s in self.samples], dtype=np.float32)
        y_value = np.array([s.y_value for s in self.samples], dtype=np.float32)
        y_phase = np.array([s.y_phase for s in self.samples], dtype=np.int64)
        return x, y_edge, y_value, y_phase

    def arrays_full(self) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        x, y_edge, y_value, y_phase = self.arrays()
        y_demand = np.array([s.y_demand for s in self.samples], dtype=np.float32)
        demand_mask = np.array([s.demand_mask for s in self.samples], dtype=np.float32)
        return x, y_edge, y_value, y_phase, y_demand, demand_mask

    def arrays_with_availability(self) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        x, y_edge, y_value, y_phase, y_demand, demand_mask = self.arrays_full()
        y_availability = np.array([s.y_availability for s in self.samples], dtype=np.float32)
        return x, y_edge, y_value, y_phase, y_demand, demand_mask, y_availability

    def arrays_for_training(self) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Return all CASA targets, including per-resource beta_tau calibration scales."""
        x, y_edge, y_value, y_phase, y_demand, demand_mask, y_availability = self.arrays_with_availability()
        uncertainty_beta = np.array([s.uncertainty_beta or [1.0 for _ in self.vocab.resources] for s in self.samples], dtype=np.float32)
        return x, y_edge, y_value, y_phase, y_demand, demand_mask, y_availability, uncertainty_beta
