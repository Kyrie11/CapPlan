"""CASA-Net interface.

A lightweight deterministic implementation is provided so the full pipeline runs
without GPU or PyTorch.  If PyTorch is installed, the class can be extended with
an MLP while preserving the input/output interface required by the planner.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from capplan.data.schemas import CandidateTransition
from capplan.models.predictors import HeuristicTransitionPredictor, TransitionPrediction


@dataclass
class CASAInput:
    service_graph: Dict[str, Any]
    active_capability_tokens: List[Dict[str, Any]]
    phase_belief: Dict[str, float]
    ego_agent_map_features: Dict[str, Any]
    transitions: List[CandidateTransition]


@dataclass
class CASAOutput:
    phase_belief: Dict[str, float]
    transition_predictions: Dict[str, TransitionPrediction]
    audit_history: List[Dict[str, Any]]


class CASANet:
    def __init__(self, mode: str = "heuristic", disabled: bool = False) -> None:
        self.mode = mode
        self.disabled = disabled
        self.heuristic = HeuristicTransitionPredictor()

    def forward(self, inputs: CASAInput) -> CASAOutput:
        preds = self.heuristic.predict(inputs.transitions, context={
            "tokens": inputs.active_capability_tokens,
            "phase_belief": inputs.phase_belief,
            "features": inputs.ego_agent_map_features,
        })
        if self.disabled:
            # Ablation: deterministic geometric evidence is kept but transition
            # value guidance is removed and availability is not learned.
            for p in preds.values():
                p.completion_value = 0.5
        return CASAOutput(
            phase_belief=inputs.phase_belief or {"origin": 1.0},
            transition_predictions=preds,
            audit_history=[{"mode": self.mode, "disabled": self.disabled, "n_transitions": len(inputs.transitions)}],
        )

    __call__ = forward
