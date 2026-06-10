"""CASA transition construction for service phases."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List

from capplan.data.schemas import AccessibilityGraph, CandidateTransition, PUDOAnchor, ResourceEvidence, VehicleInterface


@dataclass
class TransitionGeneratorConfig:
    include_replan: bool = True
    max_pudo: int = 4
    deterministic_wait_s: float = 120.0
    ride_time_s: float = 600.0
    ride_distance_m: float = 4000.0


class TransitionGenerator:
    def __init__(self, config: TransitionGeneratorConfig | None = None) -> None:
        self.config = config or TransitionGeneratorConfig()

    def generate(
        self,
        episode_id: str,
        graph: AccessibilityGraph,
        pudo_anchors: List[PUDOAnchor],
        vehicle: VehicleInterface,
        origin_anchor: str = "origin",
        destination_anchor: str = "destination",
    ) -> List[CandidateTransition]:
        anchors = pudo_anchors[: self.config.max_pudo]
        out: List[CandidateTransition] = []
        for i, pu in enumerate(anchors):
            path = self._path_stats(graph, origin_anchor, pu.anchor_id, fallback_distance=80.0 + 40.0 * i)
            out.append(self._make_transition(
                episode_id, f"{episode_id}:access:{pu.anchor_id}", origin_anchor, pu.anchor_id, "origin", "access", "access",
                [
                    ResourceEvidence("access_distance_m", "cumulative", path["distance"], sigma=path["distance"] * 0.03, confidence=path["confidence"], source="pedestrian_graph"),
                    ResourceEvidence("slope", "upper", path["slope"], sigma=0.005, confidence=path["confidence"], source="accessibility_map"),
                    ResourceEvidence("cross_slope", "upper", path["cross_slope"], sigma=0.003, confidence=path["confidence"], source="accessibility_map"),
                    ResourceEvidence("path_width_m", "lower", path["width"], sigma=0.05, confidence=path["confidence"], source="accessibility_map"),
                    ResourceEvidence("curb_ramp", "categorical", path["curb_ramp"], confidence=path["confidence"], source="accessibility_map"),
                    ResourceEvidence("step_free", "categorical", path["step_free"], confidence=path["confidence"], source="accessibility_map"),
                    ResourceEvidence("map_confidence", "lower", path["confidence"], sigma=0.02, confidence=path["confidence"], source="accessibility_map"),
                    ResourceEvidence("blockage_risk", "probabilistic", 0.02 + (0.3 if path["obstacle"] else 0.0), sigma=0.02, confidence=path["confidence"], source="perception"),
                ], cost=path["distance"], completion_value=0.75))
            out.append(self._make_transition(
                episode_id, f"{episode_id}:wait:{pu.anchor_id}", pu.anchor_id, pu.anchor_id, "access", "wait", "wait",
                [
                    ResourceEvidence("wait_exposure_s", "cumulative", self.config.deterministic_wait_s, sigma=20.0, confidence=pu.map_confidence, source="service_trace"),
                    ResourceEvidence("identification_modality", "categorical", vehicle.notification_modes, confidence=1.0, source="vehicle_spec"),
                    ResourceEvidence("map_confidence", "lower", pu.map_confidence, sigma=0.02, confidence=pu.map_confidence, source="curbside_map"),
                    ResourceEvidence("blockage_risk", "probabilistic", pu.blockage_risk, sigma=0.03, confidence=pu.map_confidence, source="prediction"),
                ], cost=self.config.deterministic_wait_s / 10.0, completion_value=0.8))
            out.append(self._make_transition(
                episode_id, f"{episode_id}:board:{pu.anchor_id}", pu.anchor_id, f"veh:{vehicle.vehicle_id}:board", "wait", "board", "board",
                [
                    ResourceEvidence("door_side", "categorical", vehicle.door_side == pu.side or vehicle.door_side == "both", confidence=1.0, source="vehicle_spec+curbside_map"),
                    ResourceEvidence("ramp", "categorical", vehicle.ramp, confidence=1.0, source="vehicle_spec"),
                    ResourceEvidence("lift", "categorical", vehicle.lift, confidence=1.0, source="vehicle_spec"),
                    ResourceEvidence("low_floor", "categorical", vehicle.low_floor, confidence=1.0, source="vehicle_spec"),
                    ResourceEvidence("door_side_clearance_m", "lower", min(vehicle.deployment_clearance_m, pu.deployment_clearance_m), sigma=0.05, confidence=pu.map_confidence, source="vehicle_spec+curbside_map"),
                    ResourceEvidence("ramp_clearance_m", "lower", min(vehicle.deployment_clearance_m, pu.deployment_clearance_m), sigma=0.05, confidence=pu.map_confidence, source="vehicle_spec+curbside_map"),
                    ResourceEvidence("curb_height_m", "upper", pu.curb_height_m, sigma=0.01, confidence=pu.map_confidence, source="curbside_map"),
                    ResourceEvidence("deployment_risk", "probabilistic", 0.03 if (vehicle.ramp or vehicle.lift) else 0.15, sigma=0.02, confidence=1.0, source="fleet_audit"),
                ], interface={"door_side": vehicle.door_side, "anchor_side": pu.side, "ramp": vehicle.ramp, "lift": vehicle.lift}, cost=vehicle.dwell_time_s / 10.0, completion_value=0.85))

        for i, pu in enumerate(anchors):
            for j, do in enumerate(anchors):
                if pu.anchor_id == do.anchor_id:
                    continue
                ride_id = f"{episode_id}:ride:{pu.anchor_id}:{do.anchor_id}"
                out.append(self._make_transition(
                    episode_id, ride_id, f"veh:{vehicle.vehicle_id}:board", f"veh:{vehicle.vehicle_id}:alight:{do.anchor_id}", "board", "ride", "ride",
                    [
                        ResourceEvidence("ride_time_s", "cumulative", self.config.ride_time_s * (1.0 + 0.05 * abs(i - j)), sigma=30.0, confidence=0.95, source="trajectory"),
                        ResourceEvidence("motion_exposure", "cumulative", 1.0 + 0.4 * abs(i - j), sigma=0.2, confidence=0.9, source="trajectory"),
                        ResourceEvidence("peak_accel_mps2", "upper", 1.3 + 0.1 * i, sigma=0.15, confidence=0.9, source="trajectory"),
                        ResourceEvidence("peak_jerk_mps3", "upper", 1.8 + 0.2 * j, sigma=0.2, confidence=0.9, source="trajectory"),
                        ResourceEvidence("availability_risk", "probabilistic", 0.04, sigma=0.02, confidence=0.9, source="prediction"),
                    ], cost=self.config.ride_time_s / 20.0 + 10.0 * abs(i - j), completion_value=0.9))
                out.append(self._make_transition(
                    episode_id, f"{episode_id}:alight:{do.anchor_id}:{pu.anchor_id}", f"veh:{vehicle.vehicle_id}:alight:{do.anchor_id}", do.anchor_id, "ride", "alight", "alight",
                    [
                        ResourceEvidence("door_side", "categorical", vehicle.door_side == do.side or vehicle.door_side == "both", confidence=1.0, source="vehicle_spec+curbside_map"),
                        ResourceEvidence("ramp", "categorical", vehicle.ramp, confidence=1.0, source="vehicle_spec"),
                        ResourceEvidence("lift", "categorical", vehicle.lift, confidence=1.0, source="vehicle_spec"),
                        ResourceEvidence("low_floor", "categorical", vehicle.low_floor, confidence=1.0, source="vehicle_spec"),
                        ResourceEvidence("door_side_clearance_m", "lower", min(vehicle.deployment_clearance_m, do.deployment_clearance_m), sigma=0.05, confidence=do.map_confidence, source="vehicle_spec+curbside_map"),
                        ResourceEvidence("curb_height_m", "upper", do.curb_height_m, sigma=0.01, confidence=do.map_confidence, source="curbside_map"),
                        ResourceEvidence("deployment_risk", "probabilistic", 0.03 if (vehicle.ramp or vehicle.lift) else 0.15, sigma=0.02, confidence=1.0, source="fleet_audit"),
                    ], interface={"door_side": vehicle.door_side, "anchor_side": do.side, "ramp": vehicle.ramp, "lift": vehicle.lift}, cost=vehicle.dwell_time_s / 10.0, completion_value=0.82))
                epath = self._path_stats(graph, do.anchor_id, destination_anchor, fallback_distance=70.0 + 45.0 * j)
                out.append(self._make_transition(
                    episode_id, f"{episode_id}:egress:{do.anchor_id}:{pu.anchor_id}", do.anchor_id, destination_anchor, "alight", "egress", "egress",
                    [
                        ResourceEvidence("egress_distance_m", "cumulative", epath["distance"], sigma=epath["distance"] * 0.03, confidence=epath["confidence"], source="pedestrian_graph"),
                        ResourceEvidence("slope", "upper", epath["slope"], sigma=0.005, confidence=epath["confidence"], source="accessibility_map"),
                        ResourceEvidence("cross_slope", "upper", epath["cross_slope"], sigma=0.003, confidence=epath["confidence"], source="accessibility_map"),
                        ResourceEvidence("path_width_m", "lower", epath["width"], sigma=0.05, confidence=epath["confidence"], source="accessibility_map"),
                        ResourceEvidence("curb_ramp", "categorical", epath["curb_ramp"], confidence=epath["confidence"], source="accessibility_map"),
                        ResourceEvidence("step_free", "categorical", epath["step_free"], confidence=epath["confidence"], source="accessibility_map"),
                        ResourceEvidence("map_confidence", "lower", epath["confidence"], sigma=0.02, confidence=epath["confidence"], source="accessibility_map"),
                        ResourceEvidence("blockage_risk", "probabilistic", 0.02 + (0.3 if epath["obstacle"] else 0.0), sigma=0.02, confidence=epath["confidence"], source="perception"),
                    ], cost=epath["distance"], completion_value=0.9))
                out.append(self._make_transition(
                    episode_id, f"{episode_id}:dest:{do.anchor_id}:{pu.anchor_id}", destination_anchor, destination_anchor, "egress", "destination", "egress",
                    [], cost=0.0, completion_value=1.0))
        if self.config.include_replan:
            for phase in ["access", "wait", "board", "ride", "alight", "egress"]:
                out.append(self._make_transition(
                    episode_id, f"{episode_id}:replan:{phase}", f"replan:{phase}", f"replan:{phase}", phase, phase, "replan",
                    [ResourceEvidence("availability_risk", "probabilistic", 0.01, sigma=0.01, confidence=0.9, source="planner")], cost=5.0, completion_value=0.2))
        return out

    @staticmethod
    def _make_transition(episode_id: str, tid: str, u: str, v: str, q: str, q2: str, action: str, evidence: List[ResourceEvidence], availability: float = 1.0, map_confidence: float = 1.0, interface: Dict[str, Any] | None = None, dynamic: Dict[str, Any] | None = None, cost: float = 1.0, completion_value: float = 0.5) -> CandidateTransition:
        confs = [e.confidence for e in evidence] or [map_confidence]
        return CandidateTransition(tid, episode_id, u, v, q, q2, action, evidence, availability, min(confs), interface or {}, dynamic or {"blocked": False}, cost, completion_value)

    @staticmethod
    def _path_stats(graph: AccessibilityGraph, start: str, end: str, fallback_distance: float) -> Dict[str, Any]:
        # For the synthetic layer, PUDO ids can be encoded directly in edge endpoints.
        relevant = [e for e in graph.edges if (e.from_node == start and e.to_node == end) or (e.from_node == end and e.to_node == start)]
        if not relevant:
            relevant = [e for e in graph.edges if e.from_node == start or e.to_node == end][:1]
        if not relevant:
            return dict(distance=fallback_distance, width=1.2, slope=0.04, cross_slope=0.02, curb_ramp=True, step_free=True, obstacle=False, confidence=0.9)
        dist = min(sum(e.length_m for e in relevant), fallback_distance * max(1, len(relevant)))
        return dict(
            distance=dist,
            width=min(e.width_m for e in relevant),
            slope=max(e.slope for e in relevant),
            cross_slope=max(e.cross_slope for e in relevant),
            curb_ramp=all(e.curb_ramp for e in relevant),
            step_free=all(e.step_free for e in relevant),
            obstacle=any(e.obstacle for e in relevant),
            confidence=min(e.confidence for e in relevant),
        )
