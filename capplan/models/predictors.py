"""Prediction interfaces used by CASA-Net and fallback predictors."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from capplan.data.schemas import CandidateTransition, ResourceEvidence


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

    def predict(self, transitions: List[CandidateTransition], context: Dict[str, Any] | None = None) -> Dict[str, TransitionPrediction]:
        out: Dict[str, TransitionPrediction] = {}
        weights = self.checkpoint.get("weights", {}) if isinstance(self.checkpoint, dict) else {}
        bias = float(weights.get("bias", 0.0)) if isinstance(weights, dict) else 0.0
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
            availability = e.availability if test_ok else min(e.availability, 0.1)
            value = max(1e-4, min(1.0, 0.5 * e.completion_value + 0.5 / (1.0 + abs(bias))))
            out[e.transition_id] = TransitionPrediction(
                transition_id=e.transition_id,
                typed_evidence=e.resource_evidence,
                uncertainty=uncert,
                dynamic_availability=availability,
                completion_value=value,
                phase_belief={e.from_phase: 0.4, e.to_phase: 0.6},
            )
        return out
