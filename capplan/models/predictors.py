"""Prediction interfaces used by CASA-Net and fallback predictors."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List
import math

from capplan.data.schemas import CandidateTransition, ResourceEvidence
from capplan.models.casa_features import FeatureVocab, encode_transition_with_capability


@dataclass
class TransitionPrediction:
    transition_id: str
    typed_evidence: List[ResourceEvidence]
    uncertainty: Dict[str, float]
    dynamic_availability: float
    completion_value: float
    phase_belief: Dict[str, float]


class BaseTransitionPredictor:
    def predict(self, transitions: List[CandidateTransition], context: Dict[str, Any] | None = None) -> Dict[str, TransitionPrediction]:
        raise NotImplementedError


class HeuristicTransitionPredictor(BaseTransitionPredictor):
    """Deterministic baseline with the same CASA-Net output contract."""

    def predict(self, transitions: List[CandidateTransition], context: Dict[str, Any] | None = None) -> Dict[str, TransitionPrediction]:
        out: Dict[str, TransitionPrediction] = {}
        for e in transitions:
            uncert = {ev.resource_name: ev.sigma for ev in e.resource_evidence}
            belief = {e.from_phase: 0.25, e.to_phase: 0.75}
            out[e.transition_id] = TransitionPrediction(
                transition_id=e.transition_id,
                typed_evidence=e.resource_evidence,
                uncertainty=uncert,
                dynamic_availability=e.availability,
                completion_value=max(1e-4, min(1.0, e.completion_value)),
                phase_belief=belief,
            )
        return out


class LearnedLinearTransitionPredictor(BaseTransitionPredictor):
    """Trainable-mode predictor interface.

    The smoke implementation consumes the same transition features as the
    training script and can optionally be wired to a saved checkpoint by a
    caller.  It is intentionally separate from ``HeuristicTransitionPredictor``
    so learned-mode audits are never mislabeled as the heuristic oracle
    baseline.  Without an external checkpoint, it emits conservative symbolic
    evidence and neutral value/availability priors; hard feasibility is still
    enforced by the planner.
    """

    def __init__(self, checkpoint: Dict[str, Any] | None = None) -> None:
        self.checkpoint = checkpoint or {}
        vocab_payload = self.checkpoint.get("vocab", {}) if isinstance(self.checkpoint, dict) else {}
        self.vocab = FeatureVocab(**vocab_payload) if isinstance(vocab_payload, dict) and vocab_payload else FeatureVocab()
        weights = self.checkpoint.get("weights", {}) if isinstance(self.checkpoint, dict) else {}
        self.weights = weights if isinstance(weights, dict) else {}
        self._torch_model = None
        if isinstance(self.checkpoint, dict) and self.checkpoint.get("torch_state_dict") is not None:
            self._init_torch_model()

    def _init_torch_model(self) -> None:
        try:  # pragma: no cover - depends on torch
            import torch
            from capplan.models.casa_torch import CASAHetGraphNet
            input_dim = int(self.checkpoint.get("input_dim", 0) or 0)
            num_phases = int(self.checkpoint.get("num_phases", len(self.vocab.phases)) or len(self.vocab.phases))
            num_resources = int(self.checkpoint.get("num_resources", len(self.vocab.resources)) or len(self.vocab.resources))
            model_type = str(self.checkpoint.get("config", {}).get("model_type", "hgt"))
            model = CASAHetGraphNet(input_dim, num_phases, num_resources, model_type=model_type)
            model.load_state_dict(self.checkpoint["torch_state_dict"], strict=False)
            model.eval()
            self._torch_model = model
        except Exception:
            self._torch_model = None

    @staticmethod
    def _sigmoid(x: float) -> float:
        # Stable scalar sigmoid; no numpy/torch dependency at inference time.
        if x >= 0:
            z = math.exp(-x)
            return 1.0 / (1.0 + z)
        z = math.exp(x)
        return z / (1.0 + z)

    @staticmethod
    def _dot(w: Any, x: List[float]) -> float | None:
        if not isinstance(w, list) or len(w) != len(x):
            return None
        try:
            return float(sum(float(a) * float(b) for a, b in zip(w, x)))
        except Exception:
            return None

    def _normalized_features(self, transition: CandidateTransition, context: Dict[str, Any] | None = None) -> List[float]:
        tokens = []
        if isinstance(context, dict):
            tokens = context.get("tokens") or []
        x = [float(v) for v in encode_transition_with_capability(transition, tokens, self.vocab)]
        mean = self.weights.get("mean")
        std = self.weights.get("std")
        if isinstance(mean, list) and isinstance(std, list) and len(mean) == len(x) and len(std) == len(x):
            return [(xi - float(mu)) / max(float(si), 1e-6) for xi, mu, si in zip(x, mean, std)]
        return x

    def _predict_heads(self, transition: CandidateTransition, context: Dict[str, Any] | None = None) -> tuple[float | None, float | None, Dict[str, float] | None, Dict[str, float] | None]:
        x = self._normalized_features(transition, context)
        if self._torch_model is not None:
            try:  # pragma: no cover - depends on torch
                import torch
                with torch.no_grad():
                    pred = self._torch_model(torch.tensor([x], dtype=torch.float32))
                    edge_prob = float(torch.sigmoid(pred["edge_logits"])[0].cpu())
                    value_prob = float(pred["value"][0].cpu())
                    demand = {r: float(pred["typed_demand"][0, i].cpu()) for i, r in enumerate(self.vocab.resources[: pred["typed_demand"].shape[1]])}
                    unc = {r: float(pred["uncertainty"][0, i].cpu()) for i, r in enumerate(self.vocab.resources[: pred["uncertainty"].shape[1]])}
                    return edge_prob, value_prob, demand, unc
            except Exception:
                pass
        edge_logit = self._dot(self.weights.get("W_edge"), x)
        value_logit = self._dot(self.weights.get("W_value"), x)
        if edge_logit is not None:
            edge_logit += float(self.weights.get("b_edge", 0.0))
        if value_logit is not None:
            value_logit += float(self.weights.get("b_value", 0.0))
        edge_prob = self._sigmoid(edge_logit) if edge_logit is not None else None
        value_prob = self._sigmoid(value_logit) if value_logit is not None else None
        return edge_prob, value_prob, None, None

    def predict(self, transitions: List[CandidateTransition], context: Dict[str, Any] | None = None) -> Dict[str, TransitionPrediction]:
        out: Dict[str, TransitionPrediction] = {}
        for e in transitions:
            uncert = {ev.resource_name: max(ev.sigma, 0.01) for ev in e.resource_evidence}
            # Conservative learned-mode prior: use explicit transition tests and
            # saved availability as inputs, but do not invent symbolic validity.
            test_ok = all([
                e.tests.legal_lifecycle,
                e.tests.spatially_anchored,
                e.tests.topologically_valid,
                e.tests.physically_valid,
                e.tests.interface_valid,
                e.tests.dynamically_available,
            ])
            edge_prob, value_prob, demand_pred, unc_pred = self._predict_heads(e, context)
            typed_evidence = e.resource_evidence
            if demand_pred:
                # Replace numeric evidence values with learned demand predictions
                # only for already-observed resources; missing evidence remains missing.
                from dataclasses import replace as _replace
                typed_evidence = [_replace(ev, value=demand_pred.get(ev.resource_name, ev.value), sigma=(unc_pred or {}).get(ev.resource_name, ev.sigma)) if (not ev.missing and isinstance(ev.value, (int, float, bool))) else ev for ev in e.resource_evidence]
            if edge_prob is None:
                edge_prob = 1.0 if test_ok else 0.05
            if value_prob is None:
                value_prob = e.completion_value
            # Learned edge validity is a soft availability prior.  Symbolic tests
            # remain hard gates in the searcher, so the model cannot make an
            # invalid edge valid, but it can deprioritize/close a low-probability
            # edge when a checkpoint is actually supplied.
            availability = e.availability * max(0.0, min(1.0, float(edge_prob))) if test_ok else min(e.availability, 0.1)
            value = max(1e-4, min(1.0, float(value_prob)))
            out[e.transition_id] = TransitionPrediction(
                transition_id=e.transition_id,
                typed_evidence=typed_evidence,
                uncertainty=unc_pred or uncert,
                dynamic_availability=availability,
                completion_value=value,
                phase_belief={e.from_phase: 0.4, e.to_phase: 0.6},
            )
        return out
