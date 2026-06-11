"""PUDO anchor generation and vehicle interface metadata."""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List

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
        seed = int(getattr(scene, "metadata", {}).get("seed", 0) if not isinstance(scene, dict) else scene.get("seed", scene.get("metadata", {}).get("seed", 0)))
        episode_id = getattr(scene, "episode_id", None) if not isinstance(scene, dict) else scene.get("episode_id")
        if not episode_id and isinstance(scene, dict):
            episode_id = scene.get("episode", {}).get("episode_id")
        if not episode_id:
            episode_id = accessibility_graph.episode_id
        return synthetic_pudo_anchors(episode_id, seed=seed, n=(config or {}).get("n_candidates", self.config.n_candidates), graph=accessibility_graph)


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
