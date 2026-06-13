"""CASA-Net interface.

The interface exposes two explicitly named modes: a deterministic
``heuristic_oracle_baseline`` and a separate trainable ``learned`` mode.  The
planner never describes the heuristic baseline as a learned CASA-Net model.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Dict, List

from capplan.data.schemas import CandidateTransition
from capplan.models.predictors import HeuristicTransitionPredictor, LearnedLinearTransitionPredictor, TransitionPrediction


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
    def __init__(self, mode: str = "heuristic_oracle_baseline", disabled: bool = False, checkpoint: Dict[str, Any] | str | Path | None = None) -> None:
        if mode not in {"heuristic_oracle_baseline", "learned", "heuristic"}:
            raise ValueError(f"unsupported CASA mode {mode}")
        self.mode = "heuristic_oracle_baseline" if mode == "heuristic" else mode
        self.disabled = disabled
        loaded_checkpoint = self._load_checkpoint(checkpoint)
        self.predictor = (
            HeuristicTransitionPredictor()
            if self.mode == "heuristic_oracle_baseline"
            else LearnedLinearTransitionPredictor(checkpoint=loaded_checkpoint)
        )

    @staticmethod
    def _load_checkpoint(checkpoint: Dict[str, Any] | str | Path | None) -> Dict[str, Any] | None:
        if checkpoint is None or isinstance(checkpoint, dict):
            return checkpoint
        path = Path(checkpoint)
        if not path.exists():
            raise FileNotFoundError(f"CASA checkpoint not found: {path}")
        try:
            import torch  # type: ignore
            payload = torch.load(path, map_location="cpu")
            if isinstance(payload, dict):
                return payload
        except Exception:
            pass
        return json.loads(path.read_text(encoding="utf-8"))

    def forward(self, inputs: CASAInput) -> CASAOutput:
        preds = self.predictor.predict(inputs.transitions, context={
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
