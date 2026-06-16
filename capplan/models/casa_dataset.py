"""Dataset loader for CASA training."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

from capplan.data.schemas import contract_from_dict, transition_from_dict
from capplan.models.casa_features import FeatureVocab, encode_transition_with_capability
from capplan.semantics.resource_registry import DEFAULT_REGISTRY
from capplan.semantics.capability_compiler import CapabilityCompiler
from capplan.utils.serialization import read_jsonl


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


class CASADataset:
    """Passenger-conditioned CASA samples.

    One sample is emitted per ``(transition, passenger contract)`` pair.  The edge
    target is the passenger-specific oracle label ``y_e,p`` when available, not
    the passenger-independent transition label ``z_e``.  This is required by the
    paper idea: CASA should learn capability-conditioned service feasibility.
    """

    def __init__(self, dataset_dir: str | Path, split: str = "train", vocab: FeatureVocab | None = None) -> None:
        self.dataset_dir = Path(dataset_dir)
        self.vocab = vocab or FeatureVocab()
        self.compiler = CapabilityCompiler()
        split_ids = self._read_split(split)
        transition_labels = {d["transition_id"]: d for d in read_jsonl(self.dataset_dir / "transition_labels.jsonl")}
        passenger_path = self.dataset_dir / "passenger_edge_labels.jsonl"
        passenger_labels = {(d.get("transition_id"), d.get("passenger_id")): d for d in read_jsonl(passenger_path)} if passenger_path.exists() else {}
        contracts_by_episode: Dict[str, List] = {}
        contracts_path = self.dataset_dir / "capability_contracts.jsonl"
        if contracts_path.exists():
            for d in read_jsonl(contracts_path):
                c = contract_from_dict(d)
                eid = c.passenger_id.split(":p")[0] if ":p" in c.passenger_id else c.metadata.get("episode_id", "")
                if split_ids and eid not in split_ids:
                    continue
                contracts_by_episode.setdefault(eid, []).append(c)
        skeleton_edges: Dict[Tuple[str, str], set[str]] = {}
        skeleton_path = self.dataset_dir / "skeleton_labels.jsonl"
        if skeleton_path.exists():
            for row in read_jsonl(skeleton_path):
                skeleton_edges[(row.get("episode_id"), row.get("passenger_id"))] = set(row.get("transitions") or [])
        self.samples: List[CASASample] = []
        for d in read_jsonl(self.dataset_dir / "candidate_transitions.jsonl"):
            t = transition_from_dict(d)
            if split_ids and t.episode_id not in split_ids:
                continue
            contracts = contracts_by_episode.get(t.episode_id, [])
            if not contracts:
                # Backward-compatible fallback for legacy transition-only data.
                lab = transition_labels.get(t.transition_id, {})
                y_edge = 1.0 if lab.get("z_e", t.tests.z_e) else 0.0
                y_value = max(0.0, min(1.0, t.completion_value))
                y_phase = self.vocab.phases.index(t.to_phase) if t.to_phase in self.vocab.phases else 0
                yd, ym = self._demand_target(t)
                self.samples.append(CASASample(t.transition_id, t.episode_id, "__transition_only__", encode_transition_with_capability(t, [], self.vocab), y_edge, y_value, y_phase, yd, ym))
                continue
            for contract in contracts:
                compiled = self.compiler.compile(contract, trip_context=contract.metadata.get("trip_modifiers", {}))
                plab = passenger_labels.get((t.transition_id, contract.passenger_id))
                if plab is not None:
                    y_edge = 1.0 if plab.get("y_e_p") else 0.0
                else:
                    lab = transition_labels.get(t.transition_id, {})
                    y_edge = 1.0 if lab.get("z_e", t.tests.z_e) else 0.0
                in_skeleton = t.transition_id in skeleton_edges.get((t.episode_id, contract.passenger_id), set())
                if in_skeleton:
                    y_value = 1.0
                elif y_edge > 0.5:
                    y_value = max(0.05, min(0.95, float(t.completion_value)))
                else:
                    y_value = 0.0
                y_phase = self.vocab.phases.index(t.to_phase) if t.to_phase in self.vocab.phases else 0
                yd, ym = self._demand_target(t)
                self.samples.append(CASASample(t.transition_id, t.episode_id, contract.passenger_id, encode_transition_with_capability(t, compiled.tokens, self.vocab), y_edge, y_value, y_phase, yd, ym))

    def _read_split(self, split: str) -> set[str]:
        p = self.dataset_dir / "splits" / f"{split}_episodes.txt"
        if not p.exists():
            return set()
        return {line.strip() for line in p.read_text(encoding="utf-8").splitlines() if line.strip()}


    def _demand_target(self, t) -> Tuple[List[float], List[float]]:
        values = [0.0 for _ in self.vocab.resources]
        masks = [0.0 for _ in self.vocab.resources]
        for ev in t.resource_evidence:
            if ev.resource_name not in self.vocab.resources or ev.missing or ev.value is None:
                continue
            idx = self.vocab.resources.index(ev.resource_name)
            try:
                values[idx] = float(ev.value) if not isinstance(ev.value, bool) else float(bool(ev.value))
                masks[idx] = 1.0
            except Exception:
                # Categorical string demand is supervised through edge/interface labels.
                continue
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
