"""PUDO anchor generation and vehicle interface metadata."""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Sequence

from capplan.data.schemas import AccessibilityGraph, PUDOAnchor, Pose2D, VehicleInterface


@dataclass
class PUDOGeneratorConfig:
    n_candidates: int = 4
    search_radius_m: float = 80.0
    source: str = "synthetic_local"


class PUDOGenerator:
    """Generate curbside anchors bound to both road stop poses and pedestrian nodes."""

    def __init__(self, config: PUDOGeneratorConfig | None = None) -> None:
        self.config = config or PUDOGeneratorConfig()

    def generate(self, scene: Any, accessibility_graph: AccessibilityGraph, vehicle: VehicleInterface | None = None, config: Dict[str, Any] | None = None) -> List[PUDOAnchor]:
        config = config or {}
        seed = int(getattr(scene, "metadata", {}).get("seed", 0) if not isinstance(scene, dict) else scene.get("seed", scene.get("metadata", {}).get("seed", 0)))
        episode_id = getattr(scene, "episode_id", None) if not isinstance(scene, dict) else scene.get("episode_id")
        if not episode_id and isinstance(scene, dict):
            episode_id = scene.get("episode", {}).get("episode_id")
        if not episode_id:
            episode_id = accessibility_graph.episode_id
        n = int(config.get("n_candidates", self.config.n_candidates))
        source_policy = str(config.get("pudo_source", "auto"))
        map_context = scene.get("map_context", {}) if isinstance(scene, dict) else getattr(scene, "map_context", {})
        map_api = map_context.get("map_api") if isinstance(map_context, dict) else None
        route_ids = scene.get("route_roadblock_ids", []) if isinstance(scene, dict) else getattr(scene, "route_roadblock_ids", [])
        agent_history = scene.get("agent_history", []) if isinstance(scene, dict) else getattr(scene, "agent_history", [])
        if map_api is not None and route_ids and source_policy in {"auto", "nuplan_route"}:
            anchors = nuplan_route_pudo_anchors(
                episode_id,
                map_api=map_api,
                route_roadblock_ids=route_ids,
                graph=accessibility_graph,
                n=n,
                agent_history=agent_history,
                search_radius_m=float(config.get("search_radius_m", self.config.search_radius_m)),
            )
            if anchors:
                return anchors
            if source_policy == "nuplan_route" or config.get("strict_nuplan_pudo", False):
                raise RuntimeError("Unable to generate PUDO anchors from nuPlan route/map API; check route roadblocks and map layers")
        return synthetic_pudo_anchors(episode_id, seed=seed, n=n, graph=accessibility_graph)


def _nearest_node(graph: AccessibilityGraph, x: float, y: float, kind_filter: set[str] | None = None) -> str | None:
    best = None
    best_d = float("inf")
    for n in graph.nodes:
        if kind_filter and n.kind not in kind_filter:
            continue
        d = math.hypot(n.x - x, n.y - y)
        if d < best_d:
            best = n.node_id
            best_d = d
    return best



def _safe_attr(obj: Any, names: Sequence[str], default: Any = None) -> Any:
    for name in names:
        if hasattr(obj, name):
            value = getattr(obj, name)
            try:
                return value() if callable(value) else value
            except TypeError:
                return value
            except Exception:
                continue
    return default


def _semantic_layer(name: str) -> Any | None:
    try:  # pragma: no cover - requires nuPlan
        from nuplan.common.maps.maps_datatypes import SemanticMapLayer  # type: ignore
        return getattr(SemanticMapLayer, name, None)
    except Exception:
        return None


def _point2d(x: float, y: float) -> Any | None:
    try:  # pragma: no cover - requires nuPlan
        from nuplan.common.actor_state.state_representation import Point2D  # type: ignore
        return Point2D(float(x), float(y))
    except Exception:
        return None


def _pose_points_from_baseline(obj: Any) -> List[Pose2D]:
    baseline = _safe_attr(obj, ["baseline_path"], None)
    discrete = _safe_attr(baseline, ["discrete_path"], None) if baseline is not None else None
    points: List[Pose2D] = []
    for p in discrete or []:
        x = float(_safe_attr(p, ["x"], 0.0) or 0.0)
        y = float(_safe_attr(p, ["y"], 0.0) or 0.0)
        heading = float(_safe_attr(p, ["heading"], 0.0) or 0.0)
        points.append(Pose2D(x, y, heading, "map"))
    if points:
        return points
    polygon = _safe_attr(obj, ["polygon"], None)
    exterior = _safe_attr(polygon, ["exterior"], None) if polygon is not None else None
    coords = _safe_attr(exterior, ["coords"], None) if exterior is not None else None
    raw = list(coords or [])
    for i, xy in enumerate(raw):
        try:
            x, y = float(xy[0]), float(xy[1])
            if i + 1 < len(raw):
                nx, ny = float(raw[i + 1][0]), float(raw[i + 1][1])
                heading = math.atan2(ny - y, nx - x)
            else:
                heading = 0.0
            points.append(Pose2D(x, y, heading, "map"))
        except Exception:
            continue
    return points


def _collect_route_lane_objects(map_api: Any, route_roadblock_ids: Sequence[str], origin: Pose2D, destination: Pose2D, search_radius_m: float) -> List[Any]:
    layers = [layer for layer in (_semantic_layer("LANE"), _semantic_layer("LANE_CONNECTOR")) if layer is not None]
    route_ids = {str(x) for x in route_roadblock_ids}
    out: List[Any] = []
    seen: set[str] = set()

    # Prefer exact route roadblock expansion when the devkit exposes interior edges.
    for rid in route_ids:
        for rb_layer_name in ["ROADBLOCK", "ROADBLOCK_CONNECTOR"]:
            layer = _semantic_layer(rb_layer_name)
            if layer is None:
                continue
            try:
                rb = map_api.get_map_object(rid, layer)
            except Exception:
                rb = None
            for lane in _safe_attr(rb, ["interior_edges"], []) or []:
                lid = str(_safe_attr(lane, ["id"], id(lane)))
                if lid not in seen:
                    out.append(lane); seen.add(lid)

    # Fallback: query around the service endpoints and keep lanes belonging to route roadblocks.
    for center in [origin, destination]:
        p = _point2d(center.x, center.y)
        if p is None or not layers:
            continue
        try:
            proximal = map_api.get_proximal_map_objects(p, search_radius_m, layers)
        except Exception:
            proximal = {}
        for layer, objs in (proximal or {}).items():
            for obj in objs or []:
                rb_id = _safe_attr(obj, ["get_roadblock_id", "roadblock_id"], None)
                if route_ids and rb_id is not None and str(rb_id) not in route_ids:
                    continue
                lid = str(_safe_attr(obj, ["id"], id(obj)))
                if lid not in seen:
                    out.append(obj); seen.add(lid)
    return out


def _nearest_graph_pose(graph: AccessibilityGraph, node_id: str, fallback: Pose2D) -> Pose2D:
    for node in graph.nodes:
        if node.node_id == node_id:
            return Pose2D(node.x, node.y, node.pose.heading if node.pose else 0.0, node.pose.frame if node.pose else fallback.frame)
    return fallback


def _dynamic_blockage_risk(stop_pose: Pose2D, agent_history: Sequence[Dict[str, Any]]) -> tuple[float, float]:
    min_dist = float("inf")
    for frame in agent_history or []:
        for obj in frame.get("objects", []) or []:
            try:
                d = math.hypot(float(obj.get("x", 1e9)) - stop_pose.x, float(obj.get("y", 1e9)) - stop_pose.y)
            except Exception:
                continue
            min_dist = min(min_dist, d)
    if min_dist <= 2.0:
        return 0.95, 0.80
    if min_dist <= 5.0:
        return 0.35, 0.85
    return 0.08, 0.90


def _select_spaced(points: List[tuple[float, Pose2D, Any]], count: int, min_spacing_m: float = 12.0) -> List[tuple[Pose2D, Any]]:
    selected: List[tuple[Pose2D, Any]] = []
    for _, pose, obj in sorted(points, key=lambda x: x[0]):
        if all(math.hypot(pose.x - prev.x, pose.y - prev.y) >= min_spacing_m for prev, _ in selected):
            selected.append((pose, obj))
            if len(selected) >= count:
                break
    return selected


def _near_walkway(map_api: Any, pose: Pose2D) -> bool:
    layer = _semantic_layer("WALKWAYS")
    p = _point2d(pose.x, pose.y)
    if layer is None or p is None:
        return False
    try:
        if bool(map_api.is_in_layer(p, layer)):
            return True
    except Exception:
        pass
    try:
        _, dist = map_api.get_distance_to_nearest_map_object(p, layer)
        return dist is not None and float(dist) <= 3.0
    except Exception:
        return False


def nuplan_route_pudo_anchors(
    episode_id: str,
    map_api: Any,
    route_roadblock_ids: Sequence[str],
    graph: AccessibilityGraph,
    n: int = 4,
    agent_history: Sequence[Dict[str, Any]] | None = None,
    search_radius_m: float = 80.0,
) -> List[PUDOAnchor]:
    """Generate PUDO anchors from nuPlan route lane geometry when available.

    This is a geometry-level integration: it uses nuPlan's route roadblocks and
    lane baseline paths to place stop poses in the same map frame as the scene.
    Accessibility-specific fields such as curb ramp, slope, and audited sidewalk
    width still require an external pedestrian layer; when not available the
    confidence is intentionally moderate and the source records the heuristic.
    """
    origin = _nearest_graph_pose(graph, "origin", Pose2D(0.0, 0.0, 0.0, "map"))
    destination = _nearest_graph_pose(graph, "destination", origin)
    lane_objs = _collect_route_lane_objects(map_api, route_roadblock_ids, origin, destination, search_radius_m)
    candidate_points: List[tuple[float, Pose2D, Any]] = []
    for obj in lane_objs:
        for pose in _pose_points_from_baseline(obj):
            d_origin = math.hypot(pose.x - origin.x, pose.y - origin.y)
            d_dest = math.hypot(pose.x - destination.x, pose.y - destination.y)
            candidate_points.append((min(d_origin, d_dest), pose, obj))
    if not candidate_points:
        return []

    pickup_count = max(1, n // 2)
    dropoff_count = max(1, n - pickup_count)
    pickup_ranked = [(math.hypot(p.x - origin.x, p.y - origin.y), p, o) for _, p, o in candidate_points]
    dropoff_ranked = [(math.hypot(p.x - destination.x, p.y - destination.y), p, o) for _, p, o in candidate_points]
    selected = [("pickup", p, o) for p, o in _select_spaced(pickup_ranked, pickup_count)]
    selected += [("dropoff", p, o) for p, o in _select_spaced(dropoff_ranked, dropoff_count)]

    anchors: List[PUDOAnchor] = []
    for idx, (kind, stop, obj) in enumerate(selected[:n]):
        side = "right" if idx % 2 == 0 else "left"
        # Unit normal from lane-center stop pose to curb-side proxy.
        if side == "right":
            nx, ny = math.sin(stop.heading), -math.cos(stop.heading)
        else:
            nx, ny = -math.sin(stop.heading), math.cos(stop.heading)
        curb = Pose2D(stop.x + 3.2 * nx, stop.y + 3.2 * ny, stop.heading, "map")
        near_walkway = _near_walkway(map_api, curb)
        risk, dyn_conf = _dynamic_blockage_risk(stop, agent_history or [])
        lane_id = str(_safe_attr(obj, ["id"], "")) or None
        rb_id = _safe_attr(obj, ["get_roadblock_id", "roadblock_id"], None)
        map_conf = 0.75 if near_walkway else 0.62
        anchors.append(PUDOAnchor(
            anchor_id=f"nuplan_{kind}_{idx}",
            episode_id=episode_id,
            kind=kind,
            curb_pose=curb,
            stop_pose=stop,
            side=side,
            legal_stop=True,
            legal_stop_source="nuplan_route_lane_heuristic",
            roadblock_id=str(rb_id) if rb_id is not None else None,
            lane_id=lane_id,
            lane_connector_id=None,
            adjacent_ped_node_id=f"nuplan_{kind}_{idx}",
            curb_height_m=None,
            sidewalk_width_m=1.20 if near_walkway else None,
            deployment_clearance_m=1.20 if near_walkway else 0.85,
            blockage_risk=risk,
            map_confidence=map_conf,
            dynamic_confidence=dyn_conf,
            lighting=None,
            shelter=None,
            timestamp_s=0.0,
            source="nuplan_route_map",
        ))
    return anchors

def synthetic_pudo_anchors(episode_id: str, seed: int = 0, n: int = 4, graph: AccessibilityGraph | None = None) -> List[PUDOAnchor]:
    rng = random.Random(seed + 11)
    anchors: List[PUDOAnchor] = []
    for i in range(n):
        ped_node = f"pudo_{i}"
        if graph and any(node.node_id == ped_node for node in graph.nodes):
            node = next(node for node in graph.nodes if node.node_id == ped_node)
            x, y = node.x, node.y
        else:
            x = 30.0 + 25.0 * i
            y = rng.uniform(-2, 2)
            ped_node = _nearest_node(graph, x, y, {"pudo", "curb", "sidewalk"}) if graph else f"pudo_{i}"
        side = "right" if i % 2 == 0 else "left"
        kind = "pickup_dropoff" if i < n - 1 else "dropoff"
        legal = not (i == n - 1 and seed % 5 == 1)
        curb_pose = Pose2D(x, y, 0.0, "local")
        stop_pose = Pose2D(x, y - (2.8 if side == "right" else -2.8), 0.0, "local")
        anchors.append(PUDOAnchor(
            anchor_id=f"pudo_{i}",
            episode_id=episode_id,
            kind=kind,
            curb_pose=curb_pose,
            stop_pose=stop_pose,
            side=side,
            legal_stop=legal,
            legal_stop_source="synthetic_map_rule",
            roadblock_id=f"rb_{i // 2}",
            lane_id=f"lane_{i}",
            lane_connector_id=None,
            adjacent_ped_node_id=ped_node,
            curb_height_m=round(0.035 + 0.018 * i, 3),
            sidewalk_width_m=round(max(0.72, 1.55 - 0.12 * i), 3),
            deployment_clearance_m=round(max(0.65, 1.8 - 0.18 * i), 3),
            blockage_risk=round(0.03 + 0.045 * i, 3),
            map_confidence=round(0.95 - 0.035 * i, 3),
            dynamic_confidence=round(0.96 - 0.03 * i, 3),
            lighting="day" if i < 3 else "lit",
            shelter=i % 2 == 0,
            timestamp_s=0.0,
            source="synthetic_local",
        ))
    return anchors


def vehicle_interface_profiles(episode_id: str) -> List[VehicleInterface]:
    """Return diverse interface records for the episode."""
    return [
        VehicleInterface(
            vehicle_id="standard_vehicle",
            episode_id=episode_id,
            fleet_type="standard_sedan",
            door_side="right",
            boarding_sides=["right"],
            ramp=False,
            lift=False,
            low_floor=False,
            door_width_m=0.76,
            deployment_clearance_m=0.80,
            notification_modes=["visual", "app"],
            dwell_time_s=35.0,
            kneeling=False,
        ),
        VehicleInterface(
            vehicle_id="wav_ramp_right",
            episode_id=episode_id,
            fleet_type="wheelchair_accessible_van",
            door_side="right",
            boarding_sides=["right"],
            ramp=True,
            lift=False,
            low_floor=True,
            door_width_m=0.95,
            deployment_clearance_m=1.60,
            notification_modes=["visual", "audio", "app", "haptic"],
            dwell_time_s=60.0,
            kneeling=True,
            ramp_length_m=1.2,
        ),
        VehicleInterface(
            vehicle_id="lift_van_left",
            episode_id=episode_id,
            fleet_type="wheelchair_lift_van",
            door_side="left",
            boarding_sides=["left"],
            ramp=False,
            lift=True,
            low_floor=False,
            door_width_m=0.92,
            deployment_clearance_m=1.45,
            notification_modes=["visual", "audio", "app"],
            dwell_time_s=85.0,
            kneeling=False,
        ),
        VehicleInterface(
            vehicle_id="lift_van_right",
            episode_id=episode_id,
            fleet_type="wheelchair_lift_van",
            door_side="right",
            boarding_sides=["right"],
            ramp=False,
            lift=True,
            low_floor=False,
            door_width_m=0.92,
            deployment_clearance_m=1.55,
            notification_modes=["visual", "audio", "app", "haptic"],
            dwell_time_s=75.0,
            kneeling=False,
        ),
        VehicleInterface(
            vehicle_id="low_floor_kneeling",
            episode_id=episode_id,
            fleet_type="low_floor_shuttle",
            door_side="both",
            boarding_sides=["left", "right"],
            ramp=False,
            lift=False,
            low_floor=True,
            door_width_m=0.88,
            deployment_clearance_m=1.20,
            notification_modes=["visual", "app", "haptic"],
            dwell_time_s=45.0,
            kneeling=True,
        ),
        VehicleInterface(
            vehicle_id="limited_notifications",
            episode_id=episode_id,
            fleet_type="limited_ui_vehicle",
            door_side="right",
            boarding_sides=["right"],
            ramp=False,
            lift=False,
            low_floor=True,
            door_width_m=0.80,
            deployment_clearance_m=1.0,
            notification_modes=["visual"],
            dwell_time_s=40.0,
            kneeling=True,
        ),
    ]


def synthetic_vehicle_interface(episode_id: str, vehicle_id: str = "wav_ramp_right", accessible: bool = True) -> VehicleInterface:
    profiles = vehicle_interface_profiles(episode_id)
    if not accessible:
        vehicle_id = "standard_vehicle"
    return next((v for v in profiles if v.vehicle_id == vehicle_id), profiles[1])
