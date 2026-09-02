"""Capability-conditioned frontier ranking for passenger-complete search.

V3 deliberately separates *acceptance* from *search guidance*:

* TSBS and the compiled capability contract remain the only authority for hard
  feasibility.
* The frontier ranker sees only information available after a candidate has
  passed the symbolic one-step checks and scores which feasible successor should
  be expanded first.
* The learned score therefore cannot turn an infeasible passenger service into
  a feasible one.  It changes search order only.

The feature vector is state-dependent: it contains the successor ledger's typed
residuals under the concrete passenger contract, lifecycle progress, candidate
cost/availability/confidence, and compact contract context.  It never uses
oracle skeleton membership or completion labels as an input feature.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from capplan.data.schemas import ACTIONS, CandidateTransition
from capplan.models.casa_features import encode_capability_tokens
from capplan.semantics.capability_compiler import CompiledContract, PHASE_VOCAB, RESOURCE_VOCAB
from capplan.semantics.resource_registry import DEFAULT_REGISTRY, ResourceRegistry
from capplan.semantics.typed_resource_algebra import is_missing, signed_margin

FRONTIER_FEATURE_VERSION = "executable_capability_frontier_v1"


@dataclass(frozen=True)
class FrontierFeatureSpec:
    version: str = FRONTIER_FEATURE_VERSION
    mode: str = "full"  # full | structural


def _clamp(x: float, lo: float = -4.0, hi: float = 4.0) -> float:
    return max(lo, min(hi, float(x)))


def frontier_feature_names() -> List[str]:
    names: List[str] = []
    names += [f"phase::{q}" for q in PHASE_VOCAB]
    names += [f"action::{a}" for a in ACTIONS]
    names += [
        "history_frac",
        "remaining_phase_frac",
        "log1p_transition_cost",
        "availability",
        "map_confidence",
        "symbolic_edge_validity",
        "edge_missing_fraction",
        "edge_confidence_min",
        "edge_confidence_mean",
        "hard_clause_count_scaled",
        "requirement_group_count_scaled",
    ]
    for r in RESOURCE_VOCAB:
        names += [f"ledger_observed::{r}", f"ledger_margin::{r}", f"future_requirement::{r}"]
    return names


def _phase_indices(scopes: Sequence[str]) -> List[int]:
    if "all" in scopes:
        return list(range(len(PHASE_VOCAB)))
    return [PHASE_VOCAB.index(q) for q in scopes if q in PHASE_VOCAB]


def build_frontier_features(
    *,
    successor_label: Any,
    transition: CandidateTransition,
    compiled: CompiledContract,
    registry: ResourceRegistry = DEFAULT_REGISTRY,
    feature_mode: str = "full",
) -> List[float]:
    """Build the V3 state-dependent frontier feature vector.

    ``successor_label`` is the label *after* the symbolic one-step expansion.
    Consequently the ranker compares only successors that have already passed
    hard lifecycle/interface/resource/uncertainty tests.
    """
    if feature_mode not in {"full", "structural"}:
        raise ValueError(f"unknown frontier feature mode: {feature_mode}")

    phase = str(getattr(successor_label, "phase", transition.to_phase))
    phase_idx = PHASE_VOCAB.index(phase) if phase in PHASE_VOCAB else 0
    action = str(transition.action)
    history = list(getattr(successor_label, "history", []) or [])
    ledger = dict(getattr(successor_label, "resource_ledger", {}) or {})

    x: List[float] = []
    x += [1.0 if q == phase else 0.0 for q in PHASE_VOCAB]
    x += [1.0 if a == action else 0.0 for a in ACTIONS]

    evidence = list(transition.resource_evidence or [])
    confidences = [float(ev.confidence) for ev in evidence if math.isfinite(float(ev.confidence))]
    missing = sum(1 for ev in evidence if ev.missing or ev.value is None)
    hard_clauses = [c for c in compiled.clauses if c.hard]
    x += [
        min(1.0, len(history) / max(1.0, float(len(PHASE_VOCAB) - 1))),
        max(0.0, (len(PHASE_VOCAB) - 1 - phase_idx) / max(1.0, float(len(PHASE_VOCAB) - 1))),
        math.log1p(max(0.0, float(transition.cost))),
        max(0.0, min(1.0, float(transition.availability))),
        max(0.0, min(1.0, float(transition.map_confidence))),
        1.0 if transition.tests.z_e else 0.0,
        float(missing) / max(1.0, float(len(evidence))),
        min(confidences) if confidences else 0.0,
        sum(confidences) / len(confidences) if confidences else 0.0,
        min(1.0, len(hard_clauses) / 24.0),
        min(1.0, len(compiled.groups) / 8.0),
    ]

    clauses_by_resource: Dict[str, List[Any]] = {}
    for c in hard_clauses:
        clauses_by_resource.setdefault(c.resource_name, []).append(c)

    for r in RESOURCE_VOCAB:
        state = ledger.get(r)
        observed = state is not None and not is_missing(state)
        margin = 0.0
        if observed and r in clauses_by_resource:
            ms: List[float] = []
            for c in clauses_by_resource[r]:
                try:
                    m = float(signed_margin(ledger, c, registry))
                    if math.isfinite(m):
                        ms.append(_clamp(m))
                except Exception:
                    continue
            if ms:
                margin = min(ms) / 4.0
        future = 0.0
        for c in clauses_by_resource.get(r, []):
            inds = _phase_indices(c.phase_scope)
            if any(i > phase_idx for i in inds):
                future = 1.0
                break
        if feature_mode == "structural":
            observed = False
            margin = 0.0
            future = 0.0
        x += [1.0 if observed else 0.0, float(margin), float(future)]

    names = frontier_feature_names()
    if len(x) != len(names):
        raise AssertionError(f"frontier feature length mismatch: {len(x)} != {len(names)}")
    return x


class FrontierRanker:
    """Small pairwise-trained MLP used only for TSBS frontier ordering."""

    def __init__(self, checkpoint: str | Path | Dict[str, Any] | None, device: str = "auto") -> None:
        self.enabled = checkpoint is not None
        self.feature_mode = "full"
        self.feature_version = FRONTIER_FEATURE_VERSION
        self.mean: List[float] = []
        self.std: List[float] = []
        self.device = "cpu"
        self._model = None
        self._numpy_weights = None
        if checkpoint is None:
            return
        payload = self._load(checkpoint)
        self.feature_mode = str(payload.get("feature_mode", "full"))
        self.feature_version = str(payload.get("feature_version", ""))
        if self.feature_version != FRONTIER_FEATURE_VERSION:
            raise RuntimeError(
                f"frontier checkpoint feature version {self.feature_version!r} != {FRONTIER_FEATURE_VERSION!r}"
            )
        self.mean = [float(v) for v in payload.get("mean", [])]
        self.std = [max(float(v), 1e-6) for v in payload.get("std", [])]
        self._init_torch(payload, device)

    @staticmethod
    def _load(checkpoint: str | Path | Dict[str, Any]) -> Dict[str, Any]:
        if isinstance(checkpoint, dict):
            return checkpoint
        path = Path(checkpoint)
        if not path.exists():
            raise FileNotFoundError(f"frontier ranker checkpoint not found: {path}")
        try:
            import torch
            obj = torch.load(path, map_location="cpu")
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
        return json.loads(path.read_text(encoding="utf-8"))

    def _init_torch(self, payload: Dict[str, Any], requested_device: str) -> None:
        import torch
        from torch import nn

        input_dim = int(payload.get("input_dim", len(frontier_feature_names())))
        hidden = int(payload.get("hidden_dim", 128))
        model = nn.Sequential(
            nn.Linear(input_dim, hidden),
            nn.ReLU(),
            nn.LayerNorm(hidden),
            nn.Linear(hidden, hidden // 2),
            nn.ReLU(),
            nn.Linear(hidden // 2, 1),
        )
        model.load_state_dict(payload["state_dict"], strict=True)
        if requested_device == "auto":
            # Frontier batches are usually tiny (siblings of one TSBS label).
            # CPU avoids a GPU kernel launch per search expansion; CASA can still
            # occupy the A30.
            resolved = "cpu"
        else:
            resolved = requested_device
        if str(resolved).startswith("cuda") and not torch.cuda.is_available():
            raise RuntimeError(f"frontier ranker requested {resolved} but CUDA is unavailable")
        if str(resolved) == "cpu":
            # Pure NumPy forward is much cheaper than thousands of tiny PyTorch CPU
            # calls during search while being exactly the same affine/ReLU/LN MLP.
            import numpy as np
            sd = payload["state_dict"]
            self._numpy_weights = {k: v.detach().cpu().numpy().astype(np.float32) for k, v in sd.items()}
            self.device = "cpu_numpy"
            self._model = None
            return
        model.to(resolved)
        model.eval()
        self.device = str(resolved)
        self._model = model

    def score_feature_rows(self, rows: Sequence[Sequence[float]]) -> List[float]:
        if not rows:
            return []
        if self._numpy_weights is not None:
            import numpy as np
            x = np.asarray(rows, dtype=np.float32)
            if self.mean and self.std and len(self.mean) == x.shape[1]:
                x = (x - np.asarray(self.mean, dtype=np.float32)) / np.asarray(self.std, dtype=np.float32)
            w = self._numpy_weights
            h = x @ w["0.weight"].T + w["0.bias"]
            h = np.maximum(h, 0.0)
            mu = h.mean(axis=1, keepdims=True); var = h.var(axis=1, keepdims=True)
            h = (h - mu) / np.sqrt(var + 1e-5)
            h = h * w["2.weight"] + w["2.bias"]
            h = h @ w["3.weight"].T + w["3.bias"]
            h = np.maximum(h, 0.0)
            y = h @ w["5.weight"].T + w["5.bias"]
            return [float(v) for v in y.reshape(-1)]
        if self._model is None:
            return [0.0 for _ in rows]
        import torch
        x = torch.tensor(rows, dtype=torch.float32, device=self.device)
        if self.mean and self.std and len(self.mean) == x.shape[1]:
            mean = torch.tensor(self.mean, dtype=torch.float32, device=self.device)
            std = torch.tensor(self.std, dtype=torch.float32, device=self.device)
            x = (x - mean) / std
        with torch.inference_mode():
            scores = self._model(x).squeeze(-1)
        return [float(v) for v in scores.float().cpu().tolist()]

    def score_successors(
        self,
        rows: Sequence[tuple[Any, CandidateTransition]],
        compiled: CompiledContract,
        registry: ResourceRegistry = DEFAULT_REGISTRY,
    ) -> List[float]:
        feats = [
            build_frontier_features(
                successor_label=label,
                transition=e,
                compiled=compiled,
                registry=registry,
                feature_mode=self.feature_mode,
            )
            for label, e in rows
        ]
        return self.score_feature_rows(feats)
