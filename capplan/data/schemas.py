"""Shared dataclasses and JSON schemas for CapPlan.

The repository intentionally uses small dataclass models instead of requiring a
server-side framework.  They are strict enough for tests and experiment scripts,
and can be converted to/from JSONL deterministically.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Dict, List, Optional, Union

Scalar = Union[float, int, str, bool]
Threshold = Union[float, str, bool, List[Any]]

PHASES = ["origin", "access", "wait", "board", "ride", "alight", "egress", "destination"]
ACTIONS = ["access", "wait", "board", "ride", "alight", "egress", "replan"]
RESOURCE_KINDS = ["cumulative", "upper", "lower", "categorical", "probabilistic"]


@dataclass(frozen=True)
class CapabilityClause:
    """One executable passenger capability clause.

    kind values follow the paper: cumulative access burden, upper bottleneck
    burden, lower bottleneck affordance, categorical interface, or
    probabilistic availability.  The source and consent_scope fields preserve
    evidence provenance and data-minimization semantics.
    """

    resource_name: str
    phase_scope: List[str]
    operator: str
    threshold: Threshold
    kind: str
    confidence: float = 1.0
    risk_tolerance: Optional[float] = None
    source: str = "synthetic"
    consent_scope: str = "trip_planning"

    def __post_init__(self) -> None:
        if self.kind not in RESOURCE_KINDS:
            raise ValueError(f"unsupported resource kind {self.kind}")
        bad = [p for p in self.phase_scope if p not in PHASES]
        if bad:
            raise ValueError(f"unknown phase(s) {bad}")


@dataclass(frozen=True)
class CapabilityContract:
    passenger_id: str
    clauses: List[CapabilityClause]
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class EpisodeMetadata:
    episode_id: str
    scenario_id: str
    split: str
    origin_anchor: str
    destination_anchor: str
    request_time_s: float
    route_length_m: float
    shortest_route_length_m: float
    seed: int
    nuplan_available: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AccessibilityNode:
    node_id: str
    x: float
    y: float
    kind: str
    confidence: float = 1.0


@dataclass
class AccessibilityEdge:
    edge_id: str
    from_node: str
    to_node: str
    length_m: float
    width_m: float
    slope: float
    cross_slope: float
    surface: str
    curb_ramp: bool
    step_free: bool
    obstacle: bool
    lighting: str
    shelter: bool
    confidence: float


@dataclass
class AccessibilityGraph:
    episode_id: str
    nodes: List[AccessibilityNode]
    edges: List[AccessibilityEdge]


@dataclass
class PUDOAnchor:
    anchor_id: str
    episode_id: str
    x: float
    y: float
    side: str
    legal_stop: bool
    curb_height_m: float
    sidewalk_width_m: float
    deployment_clearance_m: float
    blockage_risk: float
    map_confidence: float
    lighting: str = "day"
    shelter: bool = False


@dataclass
class VehicleInterface:
    vehicle_id: str
    episode_id: str
    door_side: str
    ramp: bool
    lift: bool
    low_floor: bool
    door_width_m: float
    deployment_clearance_m: float
    notification_modes: List[str]
    dwell_time_s: float
    kneeling: bool = False


@dataclass
class ResourceEvidence:
    resource_name: str
    kind: str
    value: Any
    sigma: float = 0.0
    confidence: float = 1.0
    source: str = "synthetic"


@dataclass
class CandidateTransition:
    transition_id: str
    episode_id: str
    from_anchor: str
    to_anchor: str
    from_phase: str
    to_phase: str
    action: str
    resource_evidence: List[ResourceEvidence]
    availability: float
    map_confidence: float
    interface: Dict[str, Any]
    dynamic: Dict[str, Any]
    cost: float = 1.0
    completion_value: float = 0.5

    def __post_init__(self) -> None:
        if self.from_phase not in PHASES or self.to_phase not in PHASES:
            raise ValueError(f"bad phases: {self.from_phase}->{self.to_phase}")
        if self.action not in ACTIONS:
            raise ValueError(f"bad action {self.action}")


@dataclass
class LedgerStep:
    transition_id: str
    phase: str
    action: str
    resource_state: Dict[str, Any]
    margins: Dict[str, float]
    evidence: List[Dict[str, Any]]


@dataclass
class PassengerCompleteSkeleton:
    episode_id: str
    passenger_id: str
    accepted: bool
    transitions: List[str]
    steps: List[LedgerStep]
    final_ledger: Dict[str, Any]
    cost: float


@dataclass
class ViolationRecord:
    phase: str
    transition_id: str
    resource_type: str
    signed_margin: float
    evidence_source: str
    confidence: float
    reason: str = "resource"


@dataclass
class FailureCertificate:
    episode_id: str
    passenger_id: str
    phase: str
    transition_id: str
    resource_type: str
    signed_margin: float
    evidence_source: str
    confidence: float
    reason: str = "resource"
    violations: List[ViolationRecord] = field(default_factory=list)


@dataclass
class PlannerResult:
    success: bool
    skeleton: Optional[PassengerCompleteSkeleton]
    certificate: Optional[FailureCertificate]
    diagnostics: Dict[str, Any] = field(default_factory=dict)


def to_dict(obj: Any) -> Any:
    if is_dataclass(obj):
        return {k: to_dict(v) for k, v in asdict(obj).items()}
    if isinstance(obj, list):
        return [to_dict(x) for x in obj]
    if isinstance(obj, dict):
        return {k: to_dict(v) for k, v in obj.items()}
    return obj


def evidence_from_dict(d: Dict[str, Any]) -> ResourceEvidence:
    return ResourceEvidence(**d)


def transition_from_dict(d: Dict[str, Any]) -> CandidateTransition:
    d = dict(d)
    d["resource_evidence"] = [e if isinstance(e, ResourceEvidence) else evidence_from_dict(e) for e in d.get("resource_evidence", [])]
    return CandidateTransition(**d)


def clause_from_dict(d: Dict[str, Any]) -> CapabilityClause:
    return CapabilityClause(**d)


def contract_from_dict(d: Dict[str, Any]) -> CapabilityContract:
    return CapabilityContract(
        passenger_id=d["passenger_id"],
        clauses=[clause_from_dict(c) for c in d.get("clauses", [])],
        metadata=d.get("metadata", {}),
    )
