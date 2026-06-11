"""Implementation-oriented typed resource registry.

Resources remain typed throughout planning.  They may only be collapsed into a
scalar burden in the explicit ``no_typed_resource_ledger`` ablation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple, Union

RangeType = Union[Tuple[float, float], Tuple[str, str], Tuple[bool, bool]]


@dataclass(frozen=True)
class ResourceType:
    name: str
    unit: str
    kind: str  # cumulative, upper, lower, categorical, probabilistic
    feasibility_order: str  # smaller, larger, predicate
    active_phases: Tuple[str, ...]
    evidence_source: str
    default_threshold_range: RangeType
    description: str = ""


class ResourceRegistry:
    def __init__(self) -> None:
        self._resources: Dict[str, ResourceType] = {}
        self._install_defaults()

    def register(self, resource: ResourceType) -> None:
        self._resources[resource.name] = resource

    def get(self, name: str) -> ResourceType:
        if name not in self._resources:
            raise KeyError(f"unknown resource {name}")
        return self._resources[name]

    def has(self, name: str) -> bool:
        return name in self._resources

    def values(self) -> Iterable[ResourceType]:
        return self._resources.values()

    def names(self) -> List[str]:
        return sorted(self._resources)

    def by_phase(self, phase: str) -> List[ResourceType]:
        return [r for r in self._resources.values() if phase in r.active_phases or "all" in r.active_phases]

    def _install_defaults(self) -> None:
        add = self.register
        # Cumulative burdens.
        add(ResourceType("access_distance_m", "m", "cumulative", "smaller", ("access",), "pedestrian_graph", (30.0, 500.0), "Walking/rolling distance from origin to pickup."))
        add(ResourceType("egress_distance_m", "m", "cumulative", "smaller", ("egress",), "pedestrian_graph", (30.0, 500.0), "Walking/rolling distance from drop-off to destination."))
        add(ResourceType("crossing_count", "count", "cumulative", "smaller", ("access", "egress"), "pedestrian_graph", (0.0, 8.0), "Number of pedestrian crossings."))
        add(ResourceType("wait_exposure_s", "s", "cumulative", "smaller", ("wait",), "service_trace", (30.0, 1200.0), "Curbside waiting exposure."))
        add(ResourceType("motion_exposure", "score", "cumulative", "smaller", ("ride",), "trajectory", (0.5, 8.0), "Accumulated ride-motion burden."))
        add(ResourceType("ride_time_s", "s", "cumulative", "smaller", ("ride",), "trajectory", (180.0, 7200.0), "In-vehicle ride time."))
        add(ResourceType("dwell_time_s", "s", "cumulative", "smaller", ("board", "alight"), "vehicle_spec", (15.0, 300.0), "Boarding/alighting dwell time."))

        # Upper-bounded bottleneck burdens.
        add(ResourceType("slope", "ratio", "upper", "smaller", ("access", "egress"), "accessibility_map", (0.02, 0.12), "Maximum path slope."))
        add(ResourceType("cross_slope", "ratio", "upper", "smaller", ("access", "egress"), "accessibility_map", (0.01, 0.06), "Maximum cross-slope."))
        add(ResourceType("curb_height_m", "m", "upper", "smaller", ("board", "alight"), "curbside_map", (0.0, 0.18), "Curb height at interface."))
        add(ResourceType("peak_accel_mps2", "m/s^2", "upper", "smaller", ("ride",), "trajectory", (0.8, 3.0), "Peak acceleration."))
        add(ResourceType("peak_jerk_mps3", "m/s^3", "upper", "smaller", ("ride",), "trajectory", (0.8, 5.0), "Peak jerk."))

        # Lower-bounded affordances/confidence.
        add(ResourceType("path_width_m", "m", "lower", "larger", ("access", "egress"), "accessibility_map", (0.8, 2.5), "Minimum sidewalk/path width."))
        add(ResourceType("door_width_m", "m", "lower", "larger", ("board", "alight"), "vehicle_spec", (0.7, 1.2), "Vehicle doorway clear width."))
        add(ResourceType("door_side_clearance_m", "m", "lower", "larger", ("board", "alight"), "vehicle_spec+curbside_map", (0.6, 2.5), "Minimum deployment clearance at door side."))
        add(ResourceType("deployment_clearance_m", "m", "lower", "larger", ("board", "alight"), "vehicle_spec+curbside_map", (0.6, 3.0), "Physical deployment clearance."))
        add(ResourceType("ramp_clearance_m", "m", "lower", "larger", ("board", "alight"), "vehicle_spec+curbside_map", (0.8, 3.0), "Ramp/lift deployment clearance."))
        add(ResourceType("map_confidence", "prob", "lower", "larger", ("access", "wait", "board", "ride", "alight", "egress"), "map/perception", (0.5, 0.99), "Minimum map or perception confidence."))
        add(ResourceType("dynamic_confidence", "prob", "lower", "larger", ("wait", "board", "alight"), "perception", (0.5, 0.99), "Minimum dynamic-object confidence."))

        # Categorical predicates.
        add(ResourceType("step_free", "bool", "categorical", "predicate", ("access", "board", "alight", "egress"), "accessibility_map+vehicle_spec", (True, True), "Step-free continuity requirement."))
        add(ResourceType("curb_ramp", "bool", "categorical", "predicate", ("access", "egress"), "accessibility_map", (True, True), "Curb-ramp presence."))
        add(ResourceType("surface", "category", "categorical", "predicate", ("access", "egress"), "accessibility_map", ("paved", "concrete"), "Path surface category."))
        add(ResourceType("lighting", "category", "categorical", "predicate", ("access", "wait", "egress"), "map/perception", ("day", "lit"), "Lighting quality."))
        add(ResourceType("shelter", "bool", "categorical", "predicate", ("wait",), "curbside_map", (True, True), "Shelter at wait anchor."))
        add(ResourceType("ramp", "bool", "categorical", "predicate", ("board", "alight"), "vehicle_spec", (True, True), "Ramp availability."))
        add(ResourceType("lift", "bool", "categorical", "predicate", ("board", "alight"), "vehicle_spec", (True, True), "Lift availability."))
        add(ResourceType("low_floor", "bool", "categorical", "predicate", ("board", "alight"), "vehicle_spec", (True, True), "Low-floor interface."))
        add(ResourceType("kneeling", "bool", "categorical", "predicate", ("board", "alight"), "vehicle_spec", (True, True), "Kneeling interface."))
        add(ResourceType("low_floor_kneeling", "bool", "categorical", "predicate", ("board", "alight"), "vehicle_spec+curbside_map", (True, True), "Combined low-floor, kneeling, low-curb option."))
        add(ResourceType("door_side", "category", "categorical", "predicate", ("board", "alight"), "vehicle_spec+curbside_map", ("left", "right"), "Required or compatible door side."))
        add(ResourceType("identification_modality", "category", "categorical", "predicate", ("wait", "board"), "vehicle_spec+app", ("audio", "haptic"), "Vehicle identification channel."))
        add(ResourceType("assistance", "bool", "categorical", "predicate", ("board", "alight", "wait"), "service_policy", (True, True), "Temporary assistance availability."))

        # Probabilistic risks.
        add(ResourceType("blockage_risk", "prob", "probabilistic", "smaller", ("access", "wait", "board", "alight", "egress"), "perception/prediction", (0.01, 0.5), "Temporary blockage failure risk."))
        add(ResourceType("deployment_risk", "prob", "probabilistic", "smaller", ("board", "alight"), "fleet_audit", (0.01, 0.25), "Ramp/lift deployment failure risk."))
        add(ResourceType("availability_risk", "prob", "probabilistic", "smaller", ("all",), "prediction", (0.01, 0.4), "General transition unavailability risk."))


DEFAULT_REGISTRY = ResourceRegistry()
