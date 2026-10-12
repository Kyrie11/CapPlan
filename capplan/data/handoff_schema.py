"""Temporal handoff records for recourse-preserving passenger service.

These records are sidecars to the static AbilityBench-AV files.  They do not
turn counterfactual events into observed ground truth.  Every event and
observation therefore carries an explicit evidence class and validity time.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal


@dataclass(frozen=True)
class HandoffObservation:
    observation_id: str
    timestamp_s: float
    observer: Literal["vehicle", "passenger", "infrastructure", "remote_support"]
    target: str
    value: Any
    valid_from_s: float
    valid_until_s: float
    confidence: float
    source: str
    evidence_class: str


@dataclass(frozen=True)
class GuidanceCommitment:
    instruction_id: str
    issued_at_s: float
    acknowledged_at_s: float | None
    from_anchor: str
    to_anchor: str
    planned_distance_m: float
    status: Literal["issued", "acknowledged", "executing", "completed", "cancelled"]
    irrevocable_after_ack: bool = True


@dataclass
class HandoffExecutionState:
    timestamp_s: float
    passenger_anchor: str
    vehicle_anchor: str
    walked_or_rolled_m: float = 0.0
    waited_s: float = 0.0
    redirections: int = 0
    observations: List[HandoffObservation] = field(default_factory=list)
    commitments: List[GuidanceCommitment] = field(default_factory=list)
    handoff_status: Literal[
        "not_started", "approaching", "co_located", "confirmed", "failed", "safe_continuation"
    ] = "not_started"
    metadata: Dict[str, Any] = field(default_factory=dict)


REQUIRED_OVERLAY_KEYS = {
    "overlay_id",
    "episode_id",
    "phase",
    "initial_execution_state",
    "capability_budget",
    "candidate_handoffs",
    "world_support",
    "observation_model",
    "handoff_confirmation",
    "provenance",
}


def validate_temporal_overlay(row: Dict[str, Any]) -> None:
    missing = REQUIRED_OVERLAY_KEYS.difference(row)
    if missing:
        raise ValueError(f"temporal overlay missing keys: {sorted(missing)}")
    if row["phase"] not in {"pickup", "dropoff"}:
        raise ValueError("phase must be pickup or dropoff")
    worlds = row["world_support"]
    if not worlds:
        raise ValueError("world_support must be non-empty")
    mass = sum(float(world["probability"]) for world in worlds)
    if abs(mass - 1.0) > 1e-6:
        raise ValueError(f"world probabilities must sum to one, got {mass}")
    for world in worlds:
        if not world.get("evidence_class"):
            raise ValueError("every world requires an evidence_class")
    confirmation = row["handoff_confirmation"]
    if not confirmation.get("requires_explicit_confirmation", False):
        raise ValueError("handoff completion must require explicit confirmation")

