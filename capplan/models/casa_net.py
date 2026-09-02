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
    def __init__(
        self, mode: str = "heuristic_oracle_baseline", disabled: bool = False,
        checkpoint: Dict[str, Any] | str | Path | None = None, device: str = "auto",
        *, no_learned_demand: bool = False, no_learned_uncertainty: bool = False,
        no_learned_availability: bool = False, evidence_grounded_runtime: bool = False,
    ) -> None:
        if mode not in {"heuristic_oracle_baseline", "learned", "heuristic"}:
            raise ValueError(f"unsupported CASA mode {mode}")
        self.mode = "heuristic_oracle_baseline" if mode == "heuristic" else mode
        self.disabled = disabled
        loaded_checkpoint = self._load_checkpoint(checkpoint)
        # ``no_casa_net_transitions`` is an algorithmic ablation: it must
        # replace learned CASA transition/demand/uncertainty/availability outputs
        # with deterministic geometric/service evidence.  Historically the flag
        # kept the learned predictor and only set completion_value=0.5, making the
        # ablation nearly identical to ``no_completion_value_guidance``.
        self.predictor = (
            HeuristicTransitionPredictor()
            if (self.disabled or self.mode == "heuristic_oracle_baseline")
            else LearnedLinearTransitionPredictor(
                checkpoint=loaded_checkpoint, device=device,
                no_learned_demand=no_learned_demand,
                no_learned_uncertainty=no_learned_uncertainty,
                no_learned_availability=no_learned_availability,
                evidence_grounded_runtime=evidence_grounded_runtime,
            )
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
            # Deterministic ablation.  Retain the transition generator's geometric
            # phase heuristic but remove learned completion guidance so this branch
            # cannot accidentally benefit from the learned value head.
            for p in preds.values():
                p.completion_value = 0.5
        return CASAOutput(
            phase_belief=inputs.phase_belief or {"origin": 1.0},
            transition_predictions=preds,
            audit_history=[{
                "mode": self.mode,
                "disabled": self.disabled,
                "predictor": self.predictor.__class__.__name__,
                "n_transitions": len(inputs.transitions),
                "evidence_policy": ("evidence_grounded_v2" if getattr(self.predictor, "evidence_grounded_runtime", False) else "learned_overwrite_v1"),
            }],
        )

    __call__ = forward
