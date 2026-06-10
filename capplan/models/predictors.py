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
