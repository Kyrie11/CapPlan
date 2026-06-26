"""Vehicle trajectory refinement/evaluation modes."""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Tuple

from capplan.data.schemas import FailureCertificate, PassengerCompleteSkeleton, ViolationRecord


def _segments(points: List[Tuple[float, float, float]]):
    for a, b in zip(points, points[1:]):
        yield a, b


def _point_in_poly(x: float, y: float, poly: List[List[float]]) -> bool:
    inside = False
    if not poly:
        return True
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i][0], poly[i][1]
        xj, yj = poly[j][0], poly[j][1]
        intersect = ((yi > y) != (yj > y)) and (x < (xj - xi) * (y - yi) / ((yj - yi) or 1e-12) + xi)
        if intersect:
            inside = not inside
        j = i
    return inside


def _dist_point_segment(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy)


def _route_intersects_objects(points: List[Tuple[float, float, float]], objects: List[Dict[str, Any]]) -> bool:
    for obj in objects:
        ox, oy = float(obj.get("x", 0.0)), float(obj.get("y", 0.0))
        radius = float(obj.get("radius", obj.get("half_extent_m", 1.5)))
        for a, b in _segments(points):
            if _dist_point_segment(ox, oy, a[0], a[1], b[0], b[1]) <= radius:
                return True
    return False


def _motion_stats(points: List[Tuple[float, float, float]]) -> Dict[str, float]:
    if len(points) < 3:
        return {"peak_accel_mps2": 0.0, "peak_jerk_mps3": 0.0}
    speeds = []
    for a, b in _segments(points):
        dt = max(1e-6, b[2] - a[2])
        speeds.append(math.hypot(b[0] - a[0], b[1] - a[1]) / dt)
    accels = []
    for v0, v1 in zip(speeds, speeds[1:]):
        accels.append(abs(v1 - v0) / 10.0)
    jerks = []
    for a0, a1 in zip(accels, accels[1:]):
        jerks.append(abs(a1 - a0) / 10.0)
    return {"peak_accel_mps2": max(accels or [0.0]), "peak_jerk_mps3": max(jerks or [0.0])}


def _normalise_external_vehicle_metrics(metrics: Dict[str, Any], route_length_m: float) -> Dict[str, Any]:
    """Convert imported nuPlan closed-loop metrics to the trajectory schema.

    This function intentionally does not simulate in-process.  It consumes
    metrics materialized by a nuPlan simulation/evaluation runner, which keeps
    CapPlan's passenger-complete evaluation independent from the heavy nuPlan
    runtime while still preventing paper runs from silently using mock motion.
    """
    route_completion = float(metrics.get("route_completion", metrics.get("rc", metrics.get("RC", 0.0))) or 0.0)
    route_completion = max(0.0, min(1.0, route_completion))
    collision = bool(metrics.get("collision", metrics.get("collisions", 0)))
    drivable_area = bool(metrics.get("drivable_area", metrics.get("drivable_area_compliance", True)))
    rule_violation = bool(metrics.get("rule_violation", metrics.get("traffic_rule_violation", metrics.get("trv", False))))
    distance = float(metrics.get("distance_m", metrics.get("vehicle_distance_m", route_length_m * route_completion)) or 0.0)
    travel_time = float(metrics.get("travel_time_s", metrics.get("tt_s", metrics.get("TT", max(1.0, route_length_m / 8.0)))) or 0.0)
    out = {
        "available": True,
        "vehicle_evaluated": True,
        "external_nuplan_metrics": True,
        "points": metrics.get("trajectory_points", []),
        "collision": collision,
        "drivable_area": drivable_area,
        "rule_compliance": not rule_violation,
        "rule_violation": rule_violation,
        "route_completion": route_completion,
        "route_completion_baseline": float(metrics.get("route_completion_baseline", route_completion)),
        "distance_m": distance,
        "travel_time_s": travel_time,
    }
    for k in ["motion_exposure", "peak_accel_mps2", "peak_jerk_mps3"]:
        if k in metrics:
            out[k] = float(metrics[k])
    return out


def refine_trajectory(skeleton: PassengerCompleteSkeleton | None, route_length_m: float = 4000.0, mode: str = "mock_strict", scene_context: Dict[str, Any] | None = None) -> Dict[str, Any]:
    scene_context = scene_context or {}
    if mode == "placeholder":
        raise RuntimeError("placeholder trajectory mode cannot be used for paper metrics; use mock_strict or nuplan_closed_loop")
    if mode == "nuplan_closed_loop":
        metrics = scene_context.get("nuplan_vehicle_metrics") or scene_context.get("external_vehicle_metrics")
        if isinstance(metrics, dict) and metrics:
            return _normalise_external_vehicle_metrics(metrics, route_length_m)
        try:
            import nuplan  # type: ignore  # noqa:F401
        except Exception as e:
            raise RuntimeError("trajectory_mode=nuplan_closed_loop requested, but no imported nuPlan vehicle metrics were found and nuPlan is unavailable; vehicle_evaluated=False") from e
        raise RuntimeError("trajectory_mode=nuplan_closed_loop requires imported nuPlan vehicle metrics in dataset_dir/nuplan_vehicle_metrics.jsonl or an integrated nuPlan simulation runner")
    if mode != "mock_strict":
        raise ValueError(f"unknown trajectory mode {mode}")

    # Passenger planning may fail before a service skeleton exists.  We still
    # expose a passenger-agnostic vehicle route baseline for TSPIR.
    baseline_rc = float(scene_context.get("route_completion", 1.0))
    if skeleton is None:
        return {
            "available": False,
            "vehicle_evaluated": True,
            "points": [],
            "collision": bool(scene_context.get("collision", False)),
            "drivable_area": bool(scene_context.get("drivable_area", True)),
            "rule_compliance": not bool(scene_context.get("rule_violation", False)),
            "rule_violation": bool(scene_context.get("rule_violation", False)),
            "route_completion": baseline_rc,
            "route_completion_baseline": baseline_rc,
            "distance_m": route_length_m * baseline_rc,
            "travel_time_s": float(scene_context.get("baseline_travel_time_s", max(1.0, route_length_m / 8.0))),
        }

    n = max(8, len(skeleton.transitions) + 2)
    points = [(float(i) * route_length_m / (n - 1), 0.0, float(i) * 10.0) for i in range(n)]
    corridor = scene_context.get("route_corridor", {}) if isinstance(scene_context.get("route_corridor"), dict) else {}
    drivable_poly = scene_context.get("drivable_polygon") or corridor.get("drivable_polygon") or [[-10, -15], [route_length_m + 10, -15], [route_length_m + 10, 15], [-10, 15]]
    drivable_area = all(_point_in_poly(x, y, drivable_poly) for x, y, _ in points)
    objects = list(scene_context.get("collision_objects", []) or [])
    collision = bool(scene_context.get("collision", False)) or _route_intersects_objects(points, objects)
    rule_violation = bool(scene_context.get("rule_violation", False)) or any("illegal_stop" in (step.margins or {}) for step in skeleton.steps)
    route_completion = max(0.0, min(1.0, float(scene_context.get("route_completion", 1.0))))
    motion = _motion_stats(points)
    motion["peak_accel_mps2"] = max(float(skeleton.final_ledger.get("peak_accel_mps2", 0.0) or 0.0), motion["peak_accel_mps2"])
    motion["peak_jerk_mps3"] = max(float(skeleton.final_ledger.get("peak_jerk_mps3", 0.0) or 0.0), motion["peak_jerk_mps3"])
    return {
        "available": True,
        "vehicle_evaluated": True,
        "points": points,
        "collision": collision,
        "drivable_area": drivable_area,
        "rule_compliance": not rule_violation,
        "rule_violation": rule_violation,
        "route_completion": route_completion,
        "route_completion_baseline": baseline_rc,
        "distance_m": route_length_m * route_completion,
        "travel_time_s": max(1.0, skeleton.cost),
        "motion_exposure": skeleton.final_ledger.get("motion_exposure", 0.0),
        **motion,
    }


class CapPlanNuPlanPlanner:  # pragma: no cover - import wrapper depends on nuPlan
    """Thin nuPlan planner adapter.

    The wrapper keeps the repository importable without nuPlan while allowing a
    real CapPlan planner implementation to be injected by nuPlan simulation
    scripts.  It does not silently fabricate ego trajectories: when no planner
    implementation is provided, it fails with an explicit integration error.
    """

    def __init__(self, capplan_planner: Any | None = None) -> None:
        try:
            from nuplan.planning.simulation.planner.abstract_planner import AbstractPlanner  # type: ignore  # noqa:F401
        except Exception as e:
            raise RuntimeError("CapPlanNuPlanPlanner requires the nuPlan devkit") from e
        self.capplan_planner = capplan_planner
        self.map_api = None
        self.route_roadblock_ids = []
        self.mission_goal = None

    def initialize(self, initialization: Any) -> None:
        self.map_api = getattr(initialization, "map_api", None)
        self.route_roadblock_ids = getattr(initialization, "route_roadblock_ids", [])
        self.mission_goal = getattr(initialization, "mission_goal", None)
        if self.capplan_planner is not None and hasattr(self.capplan_planner, "initialize"):
            self.capplan_planner.initialize(initialization)

    def compute_planner_trajectory(self, current_input: Any) -> Any:
        if self.capplan_planner is None:
            raise RuntimeError(
                "CapPlanNuPlanPlanner requires an injected CapPlan planner that returns "
                "a nuPlan AbstractTrajectory/InterpolatedTrajectory. The repository's "
                "paper-mode evaluation can import nuPlan vehicle metrics, but direct "
                "ego-control simulation still needs the concrete planner implementation."
            )
        if hasattr(self.capplan_planner, "compute_planner_trajectory"):
            return self.capplan_planner.compute_planner_trajectory(current_input)
        if hasattr(self.capplan_planner, "plan"):
            planned = self.capplan_planner.plan(
                current_input=current_input,
                map_api=self.map_api,
                route_roadblock_ids=self.route_roadblock_ids,
                mission_goal=self.mission_goal,
            )
            return getattr(planned, "trajectory", planned)
        raise RuntimeError(
            "Injected CapPlan planner must implement compute_planner_trajectory(current_input) "
            "or plan(current_input=..., map_api=..., route_roadblock_ids=..., mission_goal=...)."
        )
