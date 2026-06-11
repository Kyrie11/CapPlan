"""Canonical CapPlan data schemas.

The dataclasses in this module are intentionally JSON-friendly and preserve the
paper's distinction between traffic scenes, passenger-service anchors,
capability contracts, typed evidence, symbolic transition tests, labels, and
failure certificates.  Deserializers accept the older scaffold records where
practical, but new code writes the canonical layout documented in the README.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Dict, List, Literal, Optional, Union

Scalar = Union[float, int, str, bool]
Threshold = Union[float, str, bool, List[Any], Dict[str, Any], None]

PHASES = ["origin", "access", "wait", "board", "ride", "alight", "egress", "destination"]
ACTIONS = ["access", "wait", "board", "ride", "alight", "egress", "replan"]
RESOURCE_KINDS = ["cumulative", "upper", "lower", "categorical", "probabilistic"]
MISSING_POLICIES = ["fail_closed", "allow_if_optional", "inconclusive_if_low_confidence"]


@dataclass(frozen=True)
class Pose2D:
    x: float
    y: float
    heading: float = 0.0
    frame: str = "local"  # local, map, wgs84


@dataclass(frozen=True)
class SceneRecord:
    episode_id: str
    source: str  # nuplan, carla, synthetic
    split: str
    scenario_token: str | None = None
    log_name: str | None = None
    scenario_type: str | None = None
    map_name: str | None = None
    map_version: str | None = None
    initial_ego_pose: Pose2D = field(default_factory=lambda: Pose2D(0.0, 0.0, 0.0))
    mission_goal: Pose2D | None = None
    route_roadblock_ids: List[str] = field(default_factory=list)
    ego_history: List[dict] = field(default_factory=list)
    agent_history: List[dict] = field(default_factory=list)
    traffic_light_history: List[dict] = field(default_factory=list)
    route_corridor: Dict[str, Any] = field(default_factory=dict)
    timestamps_s: List[float] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EntranceAnchor:
    anchor_id: str
    episode_id: str
    kind: Literal["origin_entrance", "destination_entrance"]
    pose: Pose2D
    nearest_ped_node_id: str | None = None
    confidence: float = 1.0
    source: str = "synthetic_service_overlay"


@dataclass(frozen=True)
class RequirementGroup:
    group_id: str
    phase_scope: List[str]
    logic: Literal["all_of", "any_of", "not"]
    clause_ids: List[str]
    hard: bool = True

    def __post_init__(self) -> None:
        bad = [p for p in self.phase_scope if p not in PHASES and p != "all"]
        if bad:
            raise ValueError(f"unknown phase(s) {bad}")


@dataclass(frozen=True)
class CapabilityClause:
    """One executable passenger capability clause.

    Capability clauses encode functional trip-planning requirements.  They are
    not medical or demographic labels.  ``hard`` clauses are feasibility
    constraints unless the explicit soft-only ablation is selected.
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
    clause_id: str | None = None
    hard: bool = True
    beta_tau: float = 1.0
    missing_policy: str = "fail_closed"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.kind not in RESOURCE_KINDS:
            raise ValueError(f"unsupported resource kind {self.kind}")
        bad = [p for p in self.phase_scope if p not in PHASES and p != "all"]
        if bad:
            raise ValueError(f"unknown phase(s) {bad}")
        if self.missing_policy not in MISSING_POLICIES:
            raise ValueError(f"unsupported missing evidence policy {self.missing_policy}")
        if self.resource_name == "door_side" and isinstance(self.threshold, bool):
            raise ValueError("door_side threshold must be a required side/policy, not a boolean")

    @property
    def id(self) -> str:
        return self.clause_id or f"{self.resource_name}:{','.join(self.phase_scope)}:{self.operator}:{self.threshold}"


@dataclass(frozen=True)
class CapabilityContract:
    passenger_id: str
    clauses: List[CapabilityClause]
    metadata: Dict[str, Any] = field(default_factory=dict)
    groups: List[RequirementGroup] = field(default_factory=list)
    profile: Dict[str, Any] = field(default_factory=dict)


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
    scene_source: str = "synthetic"
    map_name: str | None = None
    map_version: str | None = None
    scenario_token: str | None = None
    log_name: str | None = None
    route_roadblock_ids: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AccessibilityNode:
    node_id: str
    x: float
    y: float
    kind: str
    confidence: float = 1.0
    timestamp_s: float | None = None
    source: str = "synthetic_local"
    pose: Pose2D | None = None

    def __post_init__(self) -> None:
        if self.pose is None:
            self.pose = Pose2D(self.x, self.y)
        else:
            self.x = float(self.pose.x)
            self.y = float(self.pose.y)


@dataclass
class AccessibilityEdge:
    edge_id: str
    from_node: str
    to_node: str
    length_m: float
    width_m: Optional[float] = None
    slope: Optional[float] = None
    cross_slope: Optional[float] = None
    surface: Optional[str] = None
    curb_ramp: Optional[bool] = None
    step_free: Optional[bool] = None
    obstacle: bool = False
    lighting: Optional[str] = None
    shelter: Optional[bool] = None
    confidence: float = 1.0
    geometry: List[List[float]] = field(default_factory=list)
    crossing_type: Optional[str] = None
    obstacle_state: Optional[str] = None
    timestamp_s: float | None = None
    source: str = "synthetic_local"


@dataclass
class AccessibilityGraph:
    episode_id: str
    nodes: List[AccessibilityNode]
    edges: List[AccessibilityEdge]
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PUDOAnchor:
    anchor_id: str
    episode_id: str
    kind: Literal["pickup", "dropoff", "pickup_dropoff"]
    curb_pose: Pose2D
    stop_pose: Pose2D
    side: Literal["left", "right", "both", "unknown"]
    legal_stop: bool
    legal_stop_source: str = "synthetic_map"
    roadblock_id: str | None = None
    lane_id: str | None = None
    lane_connector_id: str | None = None
    adjacent_ped_node_id: str | None = None
    curb_height_m: float | None = None
    sidewalk_width_m: float | None = None
    deployment_clearance_m: float | None = None
    blockage_risk: float = 0.0
    map_confidence: float = 1.0
    dynamic_confidence: float = 1.0
    lighting: str | None = "day"
    shelter: bool | None = False
    timestamp_s: float | None = None
    source: str = "synthetic_local"

    @property
    def x(self) -> float:
        return self.curb_pose.x

    @property
    def y(self) -> float:
        return self.curb_pose.y


@dataclass(frozen=True)
class VehicleInterface:
    vehicle_id: str
    episode_id: str
    door_side: str = "right"
    ramp: bool = False
    lift: bool = False
    low_floor: bool = False
    door_width_m: float = 0.78
    deployment_clearance_m: float = 0.8
    notification_modes: List[str] = field(default_factory=lambda: ["visual"])
    dwell_time_s: float = 30.0
    kneeling: bool = False
    fleet_type: str = "standard"
    boarding_sides: List[str] = field(default_factory=list)
    ramp_length_m: float | None = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.boarding_sides:
            object.__setattr__(self, "boarding_sides", [self.door_side] if self.door_side != "both" else ["left", "right"])


@dataclass
class ResourceEvidence:
    resource_name: str
    kind: str
    value: Any
    sigma: float = 0.0
    confidence: float = 1.0
    source: str = "synthetic"
    observed: Any = None
    required: Any = None
    missing: bool = False
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.observed is None:
            self.observed = self.value
        if self.value is None:
            self.missing = True
            if self.reason is None:
                self.reason = "not_observed"


@dataclass(frozen=True)
class TransitionTests:
    legal_lifecycle: bool = True
    spatially_anchored: bool = True
    topologically_valid: bool = True
    physically_valid: bool = True
    interface_valid: bool = True
    dynamically_available: bool = True
    reasons: List[str] = field(default_factory=list)

    @property
    def z_e(self) -> bool:
        return bool(self.legal_lifecycle and self.spatially_anchored and self.topologically_valid and self.physically_valid and self.interface_valid and self.dynamically_available)


@dataclass
class ServiceNode:
    node_id: str
    node_type: str
    phase: str
    pose: Pose2D | None = None
    metadata: Dict[str, Any] = field(default_factory=dict)


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
    tests: TransitionTests = field(default_factory=TransitionTests)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.from_phase not in PHASES or self.to_phase not in PHASES:
            raise ValueError(f"bad phases: {self.from_phase}->{self.to_phase}")
        if self.action not in ACTIONS:
            raise ValueError(f"bad action {self.action}")


@dataclass(frozen=True)
class TransitionLabel:
    episode_id: str
    transition_id: str
    legal_lifecycle: bool
    spatially_anchored: bool
    physically_valid: bool
    topologically_valid: bool
    interface_valid: bool
    dynamically_available: bool
    z_e: bool
    evidence: Dict[str, Any]


@dataclass(frozen=True)
class PassengerEdgeLabel:
    episode_id: str
    passenger_id: str
    transition_id: str
    z_e: bool
    resource_ok: bool
    uncertainty_ok: bool
    y_e_p: bool
    margins: Dict[str, float]
    failed_resources: List[str]


@dataclass(frozen=True)
class CounterfactualPair:
    pair_id: str
    episode_id: str
    weak_passenger_id: str
    strict_passenger_id: str
    relation: Literal["stricter_or_equal", "different_modality", "different_interface"]
    expected_monotonic: bool


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


# ---------- JSON serialization helpers ----------

def _strip_none(d: Dict[str, Any]) -> Dict[str, Any]:
    return d


def to_dict(obj: Any) -> Any:
    if is_dataclass(obj):
        return {k: to_dict(v) for k, v in asdict(obj).items()}
    if isinstance(obj, list):
        return [to_dict(x) for x in obj]
    if isinstance(obj, dict):
        return {k: to_dict(v) for k, v in obj.items()}
    return obj


def pose_from_dict(d: Dict[str, Any] | Pose2D | None, default_x: float = 0.0, default_y: float = 0.0) -> Pose2D:
    if isinstance(d, Pose2D):
        return d
    if d is None:
        return Pose2D(default_x, default_y)
    return Pose2D(**d)


def scene_from_dict(d: Dict[str, Any]) -> SceneRecord:
    d = dict(d)
    d["initial_ego_pose"] = pose_from_dict(d.get("initial_ego_pose"))
    d["mission_goal"] = pose_from_dict(d.get("mission_goal")) if d.get("mission_goal") is not None else None
    return SceneRecord(**d)


def entrance_from_dict(d: Dict[str, Any]) -> EntranceAnchor:
    d = dict(d)
    d["pose"] = pose_from_dict(d.get("pose"), d.get("x", 0.0), d.get("y", 0.0))
    d.pop("x", None); d.pop("y", None)
    return EntranceAnchor(**d)


def group_from_dict(d: Dict[str, Any]) -> RequirementGroup:
    return RequirementGroup(**d)


def evidence_from_dict(d: Dict[str, Any]) -> ResourceEvidence:
    return d if isinstance(d, ResourceEvidence) else ResourceEvidence(**d)


def transition_tests_from_dict(d: Dict[str, Any] | TransitionTests | None) -> TransitionTests:
    if isinstance(d, TransitionTests):
        return d
    if d is None:
        return TransitionTests()
    return TransitionTests(**d)


def transition_from_dict(d: Dict[str, Any]) -> CandidateTransition:
    d = dict(d)
    d["resource_evidence"] = [evidence_from_dict(e) for e in d.get("resource_evidence", [])]
    d["tests"] = transition_tests_from_dict(d.get("tests"))
    return CandidateTransition(**d)


def clause_from_dict(d: Dict[str, Any]) -> CapabilityClause:
    return d if isinstance(d, CapabilityClause) else CapabilityClause(**d)


def contract_from_dict(d: Dict[str, Any]) -> CapabilityContract:
    if isinstance(d, CapabilityContract):
        return d
    return CapabilityContract(
        passenger_id=d["passenger_id"],
        clauses=[clause_from_dict(c) for c in d.get("clauses", [])],
        metadata=d.get("metadata", {}),
        groups=[group_from_dict(g) for g in d.get("groups", [])],
        profile=d.get("profile", {}),
    )


def node_from_dict(d: Dict[str, Any]) -> AccessibilityNode:
    d = dict(d)
    if "pose" in d and d["pose"] is not None:
        d["pose"] = pose_from_dict(d["pose"])
    return AccessibilityNode(**d)


def edge_from_dict(d: Dict[str, Any]) -> AccessibilityEdge:
    return AccessibilityEdge(**d)


def graph_from_records(episode_id: str, nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]], metadata: Dict[str, Any] | None = None) -> AccessibilityGraph:
    return AccessibilityGraph(episode_id, [node_from_dict(n) for n in nodes], [edge_from_dict(e) for e in edges], metadata or {})


def pudo_from_dict(d: Dict[str, Any]) -> PUDOAnchor:
    d = dict(d)
    if "curb_pose" not in d:
        x = float(d.pop("x", 0.0)); y = float(d.pop("y", 0.0))
        d["curb_pose"] = {"x": x, "y": y, "heading": 0.0, "frame": "local"}
        d["stop_pose"] = {"x": x, "y": y, "heading": 0.0, "frame": "local"}
        d.setdefault("kind", "pickup_dropoff")
        d.setdefault("legal_stop_source", "legacy")
        d.setdefault("dynamic_confidence", d.get("map_confidence", 1.0))
    d["curb_pose"] = pose_from_dict(d.get("curb_pose"))
    d["stop_pose"] = pose_from_dict(d.get("stop_pose"))
    return PUDOAnchor(**d)


def vehicle_from_dict(d: Dict[str, Any]) -> VehicleInterface:
    return d if isinstance(d, VehicleInterface) else VehicleInterface(**d)


def transition_label_from_transition(e: CandidateTransition) -> TransitionLabel:
    t = e.tests
    return TransitionLabel(
        episode_id=e.episode_id,
        transition_id=e.transition_id,
        legal_lifecycle=t.legal_lifecycle,
        spatially_anchored=t.spatially_anchored,
        physically_valid=t.physically_valid,
        topologically_valid=t.topologically_valid,
        interface_valid=t.interface_valid,
        dynamically_available=t.dynamically_available,
        z_e=bool(t.z_e and e.availability > 0.0),
        evidence={"availability": e.availability, "map_confidence": e.map_confidence, "reasons": t.reasons},
    )
