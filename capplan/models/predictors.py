"""Prediction interfaces used by CASA-Net and fallback predictors."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List
import math

from capplan.data.schemas import CandidateTransition, ResourceEvidence
from capplan.models.casa_features import FeatureVocab, encode_transition_with_capability
from capplan.semantics.resource_registry import DEFAULT_REGISTRY


@dataclass
class TransitionPrediction:
    transition_id: str
    typed_evidence: List[ResourceEvidence]
    uncertainty: Dict[str, float]
    dynamic_availability: float
    completion_value: float
    phase_belief: Dict[str, float]
    # Learned passenger-independent transition-validity prior.  This is a
    # search-ordering signal, not dynamic availability and never overrides the
    # symbolic transition tests.
    edge_validity: float = 1.0


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
                edge_validity=1.0 if e.tests.z_e else 0.0,
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

    def __init__(
        self, checkpoint: Dict[str, Any] | None = None, device: str = "auto",
        *, no_learned_demand: bool = False, no_learned_uncertainty: bool = False,
        no_learned_availability: bool = False,
    ) -> None:
        self.checkpoint = checkpoint or {}
        vocab_payload = self.checkpoint.get("vocab", {}) if isinstance(self.checkpoint, dict) else {}
        self.vocab = FeatureVocab(**vocab_payload) if isinstance(vocab_payload, dict) and vocab_payload else FeatureVocab()
        weights = self.checkpoint.get("weights", {}) if isinstance(self.checkpoint, dict) else {}
        self.weights = weights if isinstance(weights, dict) else {}
        cfg = self.checkpoint.get("config", {}) if isinstance(self.checkpoint, dict) else {}
        self.feature_policy = str(cfg.get("feature_policy", "legacy")) if isinstance(cfg, dict) else "legacy"
        if self.feature_policy not in {"legacy", "paper_safe", "paper_safe_v2"}:
            self.feature_policy = "legacy"
        self._torch_model = None
        self._torch_device = "cpu"
        self.requested_device = str(device or "auto")
        self.no_learned_demand = bool(no_learned_demand)
        self.no_learned_uncertainty = bool(no_learned_uncertainty)
        self.no_learned_availability = bool(no_learned_availability)
        if isinstance(self.checkpoint, dict) and self.checkpoint.get("torch_state_dict") is not None:
            self._init_torch_model()

    def _init_torch_model(self) -> None:
        try:  # pragma: no cover - depends on torch
            import torch
            from capplan.models.casa_torch import CASAHetGraphNet
            input_dim = int(self.checkpoint.get("input_dim", 0) or 0)
            num_phases = int(self.checkpoint.get("num_phases", len(self.vocab.phases)) or len(self.vocab.phases))
            num_resources = int(self.checkpoint.get("num_resources", len(self.vocab.resources)) or len(self.vocab.resources))
            model_type = str(self.checkpoint.get("config", {}).get("model_type", "relation_mlp"))
            model = CASAHetGraphNet(input_dim, num_phases, num_resources, model_type=model_type)
            model.load_state_dict(self.checkpoint["torch_state_dict"], strict=False)
            if self.requested_device == "auto":
                resolved = "cuda" if torch.cuda.is_available() else "cpu"
            else:
                resolved = self.requested_device
            if resolved.startswith("cuda") and not torch.cuda.is_available():
                raise RuntimeError(f"CASA inference requested {resolved} but CUDA is unavailable")
            model.to(resolved)
            model.eval()
            self._torch_device = resolved
            self._torch_model = model
        except Exception:
            self._torch_model = None
            if self.requested_device != "auto":
                raise

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
        x = [float(v) for v in encode_transition_with_capability(transition, tokens, self.vocab, feature_policy=self.feature_policy)]
        mean = self.weights.get("mean")
        std = self.weights.get("std")
        if isinstance(mean, list) and isinstance(std, list) and len(mean) == len(x) and len(std) == len(x):
            return [(xi - float(mu)) / max(float(si), 1e-6) for xi, mu, si in zip(x, mean, std)]
        return x

    def _predict_heads(self, transition: CandidateTransition, context: Dict[str, Any] | None = None) -> tuple[float | None, float | None, float | None, Dict[str, float] | None, Dict[str, float] | None]:
        x = self._normalized_features(transition, context)
        if self._torch_model is not None:
            try:  # pragma: no cover - depends on torch
                import torch
                with torch.no_grad():
                    pred = self._torch_model(torch.tensor([x], dtype=torch.float32, device=self._torch_device))
                    edge_prob = float(torch.sigmoid(pred["edge_logits"])[0].cpu())
                    value_prob = float(pred["value"][0].cpu())
                    availability_prob = float(pred.get("availability", torch.ones_like(pred["value"]))[0].cpu())
                    demand = {r: float(pred["typed_demand"][0, i].cpu()) for i, r in enumerate(self.vocab.resources[: pred["typed_demand"].shape[1]])}
                    unc = {r: float(pred["uncertainty"][0, i].cpu()) for i, r in enumerate(self.vocab.resources[: pred["uncertainty"].shape[1]])}
                    return edge_prob, value_prob, max(0.0, min(1.0, availability_prob)), demand, unc
            except Exception:
                pass
        edge_logit = self._dot(self.weights.get("W_edge"), x)
        value_logit = self._dot(self.weights.get("W_value"), x)
        availability_logit = self._dot(self.weights.get("W_availability"), x)
        if edge_logit is not None:
            edge_logit += float(self.weights.get("b_edge", 0.0))
        if value_logit is not None:
            value_logit += float(self.weights.get("b_value", 0.0))
        if availability_logit is not None:
            availability_logit += float(self.weights.get("b_availability", 0.0))
        edge_prob = self._sigmoid(edge_logit) if edge_logit is not None else None
        value_prob = self._sigmoid(value_logit) if value_logit is not None else None
        availability_prob = self._sigmoid(availability_logit) if availability_logit is not None else None
        return edge_prob, value_prob, availability_prob, None, None

    def _predict_heads_batch(self, transitions: List[CandidateTransition], context: Dict[str, Any] | None = None):
        """Predict all transitions for one passenger in one model call.

        reviewfix9 executed batch=1 inference once per transition and kept the
        checkpoint on CPU. Batching preserves per-transition semantics for the
        current feed-forward model while eliminating thousands of tiny model
        calls during evaluation.
        """
        if not transitions:
            return []
        xs = [self._normalized_features(e, context) for e in transitions]
        if self._torch_model is not None:
            try:  # pragma: no cover - depends on torch
                import torch
                x = torch.tensor(xs, dtype=torch.float32, device=self._torch_device)
                with torch.inference_mode():
                    pred = self._torch_model(x)
                    edge = torch.sigmoid(pred["edge_logits"]).float().cpu().numpy()
                    value = pred["value"].float().cpu().numpy()
                    avail = pred.get("availability", torch.ones_like(pred["value"])).float().cpu().numpy()
                    demand = pred["typed_demand"].float().cpu().numpy()
                    unc = pred["uncertainty"].float().cpu().numpy()
                return [
                    (
                        float(edge[i]), float(value[i]), max(0.0, min(1.0, float(avail[i]))),
                        {r: float(demand[i, j]) for j, r in enumerate(self.vocab.resources[: demand.shape[1]])},
                        {r: float(unc[i, j]) for j, r in enumerate(self.vocab.resources[: unc.shape[1]])},
                    )
                    for i in range(len(transitions))
                ]
            except Exception:
                pass
        # Backward-compatible linear/numpy fallback.
        rows = []
        for x in xs:
            edge_logit = self._dot(self.weights.get("W_edge"), x)
            value_logit = self._dot(self.weights.get("W_value"), x)
            availability_logit = self._dot(self.weights.get("W_availability"), x)
            if edge_logit is not None:
                edge_logit += float(self.weights.get("b_edge", 0.0))
            if value_logit is not None:
                value_logit += float(self.weights.get("b_value", 0.0))
            if availability_logit is not None:
                availability_logit += float(self.weights.get("b_availability", 0.0))
            rows.append((
                self._sigmoid(edge_logit) if edge_logit is not None else None,
                self._sigmoid(value_logit) if value_logit is not None else None,
                self._sigmoid(availability_logit) if availability_logit is not None else None,
                None, None,
            ))
        return rows

    def predict(self, transitions: List[CandidateTransition], context: Dict[str, Any] | None = None) -> Dict[str, TransitionPrediction]:
        out: Dict[str, TransitionPrediction] = {}
        head_rows = self._predict_heads_batch(transitions, context)
        for e, head in zip(transitions, head_rows):
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
            edge_prob, value_prob, availability_prob, demand_pred, unc_pred = head
            typed_evidence = e.resource_evidence
            if demand_pred:
                # Demand mean and uncertainty are independent learned heads and must
                # be independently ablatable.  reviewfix11 accidentally put both
                # replacements behind ``not no_learned_demand``; consequently the
                # historical ``no_learned_demand`` diagnostic disabled *both* the
                # learned mean and learned sigma.  That made the strongest V1
                # diagnostic attribution-confounded.  Repair each field separately.
                #
                # Only numerical typed resources are regression targets. Python bool
                # is an int subclass, so categorical ramp/lift/step-free predicates
                # stay authoritative and are never overwritten by continuous heads.
                from dataclasses import replace as _replace
                repaired = []
                for ev in e.resource_evidence:
                    if ev.missing or ev.value is None or not DEFAULT_REGISTRY.has(ev.resource_name):
                        repaired.append(ev)
                        continue
                    rt = DEFAULT_REGISTRY.get(ev.resource_name)
                    if rt.kind == "categorical" or isinstance(ev.value, bool):
                        repaired.append(ev)
                        continue
                    try:
                        pred_value = float(
                            ev.value
                            if self.no_learned_demand
                            else demand_pred.get(ev.resource_name, ev.value)
                        )
                        pred_sigma = float(
                            ev.sigma
                            if self.no_learned_uncertainty
                            else (unc_pred or {}).get(ev.resource_name, ev.sigma)
                        )
                    except Exception:
                        repaired.append(ev)
                        continue
                    if not (math.isfinite(pred_value) and math.isfinite(pred_sigma)):
                        repaired.append(ev)
                        continue
                    repaired.append(_replace(ev, value=pred_value, sigma=max(0.0, pred_sigma)))
                typed_evidence = repaired
            if edge_prob is None:
                edge_prob = 1.0 if test_ok else 0.05
            if value_prob is None:
                value_prob = e.completion_value
            if availability_prob is None:
                availability_prob = 1.0
            # ``z_e``/edge validity and dynamic availability are distinct CASA
            # heads.  The old code took min(edge_prob, availability_prob) and fed
            # that result into the hard availability gate, so a classification
            # error was misinterpreted as a dynamic blockage.  Keep edge validity
            # as a soft ordering prior and use only the availability head for the
            # availability estimate; symbolic tests remain authoritative.
            edge_validity = max(1e-4, min(1.0, float(edge_prob)))
            availability = (
                e.availability
                if self.no_learned_availability
                else e.availability * max(0.0, min(1.0, float(availability_prob)))
            )
            value = max(1e-4, min(1.0, float(value_prob)))
            out[e.transition_id] = TransitionPrediction(
                transition_id=e.transition_id,
                typed_evidence=typed_evidence,
                uncertainty=uncert if self.no_learned_uncertainty else (unc_pred or uncert),
                dynamic_availability=availability,
                completion_value=value,
                # The current relation-MLP checkpoint does not implement the
                # paper's runtime service-phase recognizer. Keep a local
                # transition prior rather than mislabeling the auxiliary phase
                # head as a global phase belief.
                phase_belief={e.from_phase: 0.4, e.to_phase: 0.6},
                edge_validity=edge_validity,
            )
        return out
