"""Passenger-service transition generation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, List

from capplan.data.accessibility_layer import NoAccessiblePathError, shortest_accessible_path_stats
from capplan.data.schemas import AccessibilityGraph, CandidateTransition, PUDOAnchor, ResourceEvidence, TransitionTests, VehicleInterface


@dataclass
class TransitionGeneratorConfig:
    include_replan: bool = True
    max_pudo: int = 4
    deterministic_wait_s: float = 120.0
    default_speed_mps: float = 8.0
    min_dynamic_availability: float = 0.05


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
        scene_context: Dict[str, Any] | None = None,
    ) -> List[CandidateTransition]:
        scene_context = scene_context or {}
        pickups = [p for p in pudo_anchors if p.kind in ("pickup", "pickup_dropoff")][: self.config.max_pudo]
        dropoffs = [p for p in pudo_anchors if p.kind in ("dropoff", "pickup_dropoff")][: self.config.max_pudo]
        if not pickups:
            pickups = pudo_anchors[: self.config.max_pudo]
        if not dropoffs:
            dropoffs = pudo_anchors[: self.config.max_pudo]
        out: List[CandidateTransition] = []

        for pu in pickups:
            out.append(self._access_transition(episode_id, graph, origin_anchor, pu))
            out.append(self._wait_transition(episode_id, pu, vehicle))
            out.append(self._board_transition(episode_id, pu, vehicle))

        for pu in pickups:
            for do in dropoffs:
                if pu.anchor_id == do.anchor_id and len(dropoffs) > 1:
                    continue
                out.append(self._ride_transition(episode_id, pu, do, vehicle, scene_context))
                out.append(self._alight_transition(episode_id, pu, do, vehicle))
                out.append(self._egress_transition(episode_id, graph, do, destination_anchor, pu.anchor_id))
                out.append(self._destination_transition(episode_id, do, pu.anchor_id, destination_anchor))

        if self.config.include_replan:
            out.extend(self._replan_transitions(episode_id, pickups, dropoffs))
        return out

    def _access_transition(self, episode_id: str, graph: AccessibilityGraph, origin_anchor: str, pu: PUDOAnchor) -> CandidateTransition:
        start = origin_anchor
        end = pu.adjacent_ped_node_id or pu.anchor_id
        try:
            path = shortest_accessible_path_stats(graph, start, end)
            tests = TransitionTests(True, True, True, True, True, not path.get("obstacle", False), ["obstacle_on_path"] if path.get("obstacle") else [])
        except NoAccessiblePathError as e:
            path = {"distance": None, "width": None, "slope": None, "cross_slope": None, "curb_ramp": None, "step_free": None, "surface": None, "obstacle": False, "blockage_risk": 1.0, "confidence": 0.0, "missing_fields": ["path"], "lighting": None, "shelter": None, "path_edge_ids": []}
            tests = TransitionTests(True, False, False, False, True, False, [str(e)])
        evidence = self._path_evidence("access", path)
        return self._make_transition(
            episode_id,
            f"{episode_id}:access:{origin_anchor}->{pu.anchor_id}",
            origin_anchor,
            pu.anchor_id,
            "origin",
            "access",
            "access",
            evidence,
            availability=0.0 if not tests.dynamically_available else 1.0,
            map_confidence=path.get("confidence", 0.0) or 0.0,
            tests=tests,
            cost=float(path.get("distance") or 1e6),
            completion_value=0.75,
            metadata={"path_edge_ids": path.get("path_edge_ids", []), "adjacent_ped_node_id": end},
        )

    def _wait_transition(self, episode_id: str, pu: PUDOAnchor, vehicle: VehicleInterface) -> CandidateTransition:
        blocked = pu.blockage_risk >= 0.85
        evidence = [
            ResourceEvidence("wait_exposure_s", "cumulative", self.config.deterministic_wait_s, sigma=20.0, confidence=pu.dynamic_confidence, source="service_trace"),
            ResourceEvidence("identification_modality", "categorical", list(vehicle.notification_modes), confidence=1.0, source="vehicle_spec"),
            ResourceEvidence("lighting", "categorical", pu.lighting, confidence=pu.map_confidence, source="curbside_map", missing=pu.lighting is None),
            ResourceEvidence("shelter", "categorical", pu.shelter, confidence=pu.map_confidence, source="curbside_map", missing=pu.shelter is None),
            ResourceEvidence("map_confidence", "lower", pu.map_confidence, sigma=0.02, confidence=pu.map_confidence, source="curbside_map"),
            ResourceEvidence("dynamic_confidence", "lower", pu.dynamic_confidence, sigma=0.02, confidence=pu.dynamic_confidence, source="perception"),
            ResourceEvidence("blockage_risk", "probabilistic", pu.blockage_risk, sigma=0.03, confidence=pu.dynamic_confidence, source="prediction"),
        ]
        tests = TransitionTests(True, bool(pu.adjacent_ped_node_id), True, True, True, not blocked, ["dynamic_blockage_risk_high"] if blocked else [])
        return self._make_transition(episode_id, f"{episode_id}:wait:{pu.anchor_id}", pu.anchor_id, pu.anchor_id, "access", "wait", "wait", evidence, availability=max(0.0, 1.0 - pu.blockage_risk), map_confidence=pu.map_confidence, tests=tests, cost=self.config.deterministic_wait_s / 10.0, completion_value=0.80)

    def _board_transition(self, episode_id: str, pu: PUDOAnchor, vehicle: VehicleInterface) -> CandidateTransition:
        interface_valid, reasons = self._interface_valid(pu, vehicle)
        evidence = self._interface_evidence(pu, vehicle, "board")
        tests = TransitionTests(True, bool(pu.adjacent_ped_node_id), True, bool(pu.legal_stop), interface_valid, pu.blockage_risk < 0.85, reasons + ([] if pu.legal_stop else ["illegal_stop"]))
        return self._make_transition(
            episode_id,
            f"{episode_id}:board:{pu.anchor_id}:{vehicle.vehicle_id}",
            pu.anchor_id,
            f"veh:{vehicle.vehicle_id}:board:{pu.anchor_id}",
            "wait",
            "board",
            "board",
            evidence,
            availability=max(0.0, 1.0 - pu.blockage_risk),
            map_confidence=pu.map_confidence,
            interface={"vehicle_id": vehicle.vehicle_id, "door_side": vehicle.door_side, "boarding_sides": vehicle.boarding_sides, "anchor_side": pu.side, "legal_stop": pu.legal_stop},
            dynamic={"blocked": pu.blockage_risk >= 0.85},
            tests=tests,
            cost=vehicle.dwell_time_s / 10.0,
            completion_value=0.85,
        )

    def _ride_transition(self, episode_id: str, pu: PUDOAnchor, do: PUDOAnchor, vehicle: VehicleInterface, scene_context: Dict[str, Any]) -> CandidateTransition:
        route_length = float(scene_context.get("route_length_m") or scene_context.get("route_corridor", {}).get("length_m") or 1800.0)
        route_factor = 1.0 + 0.03 * abs(_pudo_index(pu.anchor_id) - _pudo_index(do.anchor_id))
        ride_time = route_length * route_factor / max(1.0, self.config.default_speed_mps)
        peak_accel = float(scene_context.get("peak_accel_mps2", 1.25 + 0.06 * _pudo_index(pu.anchor_id)))
        peak_jerk = float(scene_context.get("peak_jerk_mps3", 1.8 + 0.08 * _pudo_index(do.anchor_id)))
        motion = float(scene_context.get("motion_exposure", max(0.8, route_length / 2000.0) + 0.1 * abs(_pudo_index(pu.anchor_id) - _pudo_index(do.anchor_id))))
        traffic_risk = float(scene_context.get("availability_risk", 0.04))
        evidence = [
            ResourceEvidence("ride_time_s", "cumulative", ride_time, sigma=max(10.0, 0.05 * ride_time), confidence=0.93, source="route_corridor"),
            ResourceEvidence("motion_exposure", "cumulative", motion, sigma=0.15, confidence=0.90, source="trajectory"),
            ResourceEvidence("peak_accel_mps2", "upper", peak_accel, sigma=0.12, confidence=0.90, source="trajectory"),
            ResourceEvidence("peak_jerk_mps3", "upper", peak_jerk, sigma=0.18, confidence=0.90, source="trajectory"),
            ResourceEvidence("availability_risk", "probabilistic", traffic_risk, sigma=0.02, confidence=0.90, source="prediction"),
        ]
        tests = TransitionTests(True, True, True, True, True, traffic_risk < 0.85, ["traffic_unavailable"] if traffic_risk >= 0.85 else [])
        return self._make_transition(
            episode_id,
            f"{episode_id}:ride:{pu.anchor_id}->{do.anchor_id}:route0:{vehicle.vehicle_id}",
            f"veh:{vehicle.vehicle_id}:board:{pu.anchor_id}",
            f"veh:{vehicle.vehicle_id}:alight:{do.anchor_id}",
            "board",
            "ride",
            "ride",
            evidence,
            availability=max(0.0, 1.0 - traffic_risk),
            map_confidence=0.93,
            tests=tests,
            cost=ride_time / 20.0,
            completion_value=0.90,
            metadata={"route_length_m": route_length, "pickup_anchor": pu.anchor_id, "dropoff_anchor": do.anchor_id},
        )

    def _alight_transition(self, episode_id: str, pu: PUDOAnchor, do: PUDOAnchor, vehicle: VehicleInterface) -> CandidateTransition:
        interface_valid, reasons = self._interface_valid(do, vehicle)
        evidence = self._interface_evidence(do, vehicle, "alight")
        tests = TransitionTests(True, bool(do.adjacent_ped_node_id), True, bool(do.legal_stop), interface_valid, do.blockage_risk < 0.85, reasons + ([] if do.legal_stop else ["illegal_stop"]))
        return self._make_transition(
            episode_id,
            f"{episode_id}:alight:{do.anchor_id}:{vehicle.vehicle_id}:from_{pu.anchor_id}",
            f"veh:{vehicle.vehicle_id}:alight:{do.anchor_id}",
            do.anchor_id,
            "ride",
            "alight",
            "alight",
            evidence,
            availability=max(0.0, 1.0 - do.blockage_risk),
            map_confidence=do.map_confidence,
            interface={"vehicle_id": vehicle.vehicle_id, "door_side": vehicle.door_side, "boarding_sides": vehicle.boarding_sides, "anchor_side": do.side, "legal_stop": do.legal_stop},
            dynamic={"blocked": do.blockage_risk >= 0.85},
            tests=tests,
            cost=vehicle.dwell_time_s / 10.0,
            completion_value=0.82,
        )

    def _egress_transition(self, episode_id: str, graph: AccessibilityGraph, do: PUDOAnchor, destination_anchor: str, pickup_id: str) -> CandidateTransition:
        start = do.adjacent_ped_node_id or do.anchor_id
        end = destination_anchor
        try:
            path = shortest_accessible_path_stats(graph, start, end)
            tests = TransitionTests(True, True, True, True, True, not path.get("obstacle", False), ["obstacle_on_path"] if path.get("obstacle") else [])
        except NoAccessiblePathError as e:
            path = {"distance": None, "width": None, "slope": None, "cross_slope": None, "curb_ramp": None, "step_free": None, "surface": None, "obstacle": False, "blockage_risk": 1.0, "confidence": 0.0, "missing_fields": ["path"], "lighting": None, "shelter": None, "path_edge_ids": []}
            tests = TransitionTests(True, False, False, False, True, False, [str(e)])
        evidence = self._path_evidence("egress", path)
        return self._make_transition(
            episode_id,
            f"{episode_id}:egress:{do.anchor_id}->{destination_anchor}:from_{pickup_id}",
            do.anchor_id,
            destination_anchor,
            "alight",
            "egress",
            "egress",
            evidence,
            availability=0.0 if not tests.dynamically_available else 1.0,
            map_confidence=path.get("confidence", 0.0) or 0.0,
            tests=tests,
            cost=float(path.get("distance") or 1e6),
            completion_value=0.90,
            metadata={"path_edge_ids": path.get("path_edge_ids", []), "adjacent_ped_node_id": start},
        )

    def _destination_transition(self, episode_id: str, do: PUDOAnchor, pickup_id: str, destination_anchor: str) -> CandidateTransition:
        return self._make_transition(
            episode_id,
            f"{episode_id}:destination:{do.anchor_id}:from_{pickup_id}",
            destination_anchor,
            destination_anchor,
            "egress",
            "destination",
            "egress",
            [],
            availability=1.0,
            map_confidence=1.0,
            tests=TransitionTests(),
            cost=0.0,
            completion_value=1.0,
        )

    def _replan_transitions(self, episode_id: str, pickups: List[PUDOAnchor], dropoffs: List[PUDOAnchor]) -> List[CandidateTransition]:
        out: List[CandidateTransition] = []
        for anchors, phase in [(pickups, "access"), (pickups, "wait"), (dropoffs, "alight")]:
            for i, a in enumerate(anchors):
                if len(anchors) < 2:
                    continue
                b = anchors[(i + 1) % len(anchors)]
                if a.anchor_id == b.anchor_id:
                    continue
                out.append(self._make_transition(
                    episode_id,
                    f"{episode_id}:replan:{phase}:{a.anchor_id}->{b.anchor_id}",
                    a.anchor_id,
                    b.anchor_id,
                    phase,
                    phase,
                    "replan",
                    [ResourceEvidence("availability_risk", "probabilistic", 0.02, sigma=0.01, confidence=min(a.dynamic_confidence, b.dynamic_confidence), source="planner")],
                    availability=0.9,
                    map_confidence=min(a.map_confidence, b.map_confidence),
                    tests=TransitionTests(True, True, True, True, True, True, []),
                    cost=8.0,
                    completion_value=0.3,
                    metadata={"from_real_anchor": a.anchor_id, "to_real_anchor": b.anchor_id},
                ))
        return out

    def _path_evidence(self, phase: str, path: Dict[str, Any]) -> List[ResourceEvidence]:
        prefix = "access" if phase == "access" else "egress"
        missing = set(path.get("missing_fields") or [])
        return [
            ResourceEvidence(f"{prefix}_distance_m", "cumulative", path.get("distance"), sigma=(path.get("distance") or 0.0) * 0.03, confidence=path.get("confidence", 0.0), source="pedestrian_graph", missing=path.get("distance") is None),
            ResourceEvidence("slope", "upper", path.get("slope"), sigma=0.005, confidence=path.get("confidence", 0.0), source="accessibility_map", missing="slope" in missing or path.get("slope") is None),
            ResourceEvidence("cross_slope", "upper", path.get("cross_slope"), sigma=0.003, confidence=path.get("confidence", 0.0), source="accessibility_map", missing="cross_slope" in missing or path.get("cross_slope") is None),
            ResourceEvidence("path_width_m", "lower", path.get("width"), sigma=0.05, confidence=path.get("confidence", 0.0), source="accessibility_map", missing="path_width_m" in missing or path.get("width") is None),
            ResourceEvidence("curb_ramp", "categorical", path.get("curb_ramp"), confidence=path.get("confidence", 0.0), source="accessibility_map", missing="curb_ramp" in missing or path.get("curb_ramp") is None),
            ResourceEvidence("step_free", "categorical", path.get("step_free"), confidence=path.get("confidence", 0.0), source="accessibility_map", missing="step_free" in missing or path.get("step_free") is None),
            ResourceEvidence("surface", "categorical", path.get("surface"), confidence=path.get("confidence", 0.0), source="accessibility_map", missing="surface" in missing or path.get("surface") is None),
            ResourceEvidence("lighting", "categorical", path.get("lighting"), confidence=path.get("confidence", 0.0), source="accessibility_map", missing=path.get("lighting") is None),
            ResourceEvidence("map_confidence", "lower", path.get("confidence"), sigma=0.02, confidence=path.get("confidence", 0.0), source="accessibility_map", missing=path.get("confidence") is None),
            ResourceEvidence("blockage_risk", "probabilistic", path.get("blockage_risk", 0.02), sigma=0.02, confidence=path.get("confidence", 0.0), source="perception"),
        ]

    def _interface_evidence(self, anchor: PUDOAnchor, vehicle: VehicleInterface, phase: str) -> List[ResourceEvidence]:
        clearance = min(vehicle.deployment_clearance_m, anchor.deployment_clearance_m if anchor.deployment_clearance_m is not None else 0.0)
        low_floor_kneeling = bool(vehicle.low_floor and vehicle.kneeling and (anchor.curb_height_m is not None and anchor.curb_height_m <= 0.06))
        side_obs = {"vehicle_side": vehicle.door_side, "curb_side": anchor.side, "observed": vehicle.door_side}
        return [
            ResourceEvidence("door_side", "categorical", side_obs, confidence=min(1.0, anchor.map_confidence), source="vehicle_spec+curbside_map", observed=side_obs, required=None, missing=anchor.side == "unknown"),
            ResourceEvidence("ramp", "categorical", vehicle.ramp, confidence=1.0, source="vehicle_spec"),
            ResourceEvidence("lift", "categorical", vehicle.lift, confidence=1.0, source="vehicle_spec"),
            ResourceEvidence("low_floor", "categorical", vehicle.low_floor, confidence=1.0, source="vehicle_spec"),
            ResourceEvidence("kneeling", "categorical", vehicle.kneeling, confidence=1.0, source="vehicle_spec"),
            ResourceEvidence("low_floor_kneeling", "categorical", low_floor_kneeling, confidence=min(1.0, anchor.map_confidence), source="vehicle_spec+curbside_map"),
            ResourceEvidence("door_width_m", "lower", vehicle.door_width_m, sigma=0.01, confidence=1.0, source="vehicle_spec"),
            ResourceEvidence("door_side_clearance_m", "lower", clearance, sigma=0.05, confidence=anchor.map_confidence, source="vehicle_spec+curbside_map", missing=anchor.deployment_clearance_m is None),
            ResourceEvidence("deployment_clearance_m", "lower", clearance, sigma=0.05, confidence=anchor.map_confidence, source="vehicle_spec+curbside_map", missing=anchor.deployment_clearance_m is None),
            ResourceEvidence("ramp_clearance_m", "lower", clearance, sigma=0.05, confidence=anchor.map_confidence, source="vehicle_spec+curbside_map", missing=anchor.deployment_clearance_m is None),
            ResourceEvidence("curb_height_m", "upper", anchor.curb_height_m, sigma=0.01, confidence=anchor.map_confidence, source="curbside_map", missing=anchor.curb_height_m is None),
            ResourceEvidence("dwell_time_s", "cumulative", vehicle.dwell_time_s, sigma=5.0, confidence=1.0, source="vehicle_spec"),
            ResourceEvidence("map_confidence", "lower", anchor.map_confidence, sigma=0.02, confidence=anchor.map_confidence, source="curbside_map"),
            ResourceEvidence("blockage_risk", "probabilistic", anchor.blockage_risk, sigma=0.03, confidence=anchor.dynamic_confidence, source="prediction"),
            ResourceEvidence("deployment_risk", "probabilistic", 0.03 if (vehicle.ramp or vehicle.lift or low_floor_kneeling) else 0.15, sigma=0.02, confidence=1.0, source="fleet_audit"),
        ]

    def _interface_valid(self, anchor: PUDOAnchor, vehicle: VehicleInterface) -> tuple[bool, List[str]]:
        reasons: List[str] = []
        if not anchor.legal_stop:
            reasons.append("illegal_stop")
        if anchor.side == "unknown":
            reasons.append("unknown_curb_side")
        if vehicle.door_side != "both" and anchor.side not in (vehicle.door_side, "both"):
            reasons.append("vehicle_door_side_incompatible_with_curb")
        if anchor.deployment_clearance_m is None or anchor.deployment_clearance_m <= 0:
            reasons.append("missing_or_zero_deployment_clearance")
        if vehicle.door_width_m <= 0:
            reasons.append("invalid_door_width")
        return len(reasons) == 0, reasons

    @staticmethod
    def _make_transition(episode_id: str, tid: str, u: str, v: str, q: str, q2: str, action: str, evidence: List[ResourceEvidence], availability: float = 1.0, map_confidence: float = 1.0, interface: Dict[str, Any] | None = None, dynamic: Dict[str, Any] | None = None, cost: float = 1.0, completion_value: float = 0.5, tests: TransitionTests | None = None, metadata: Dict[str, Any] | None = None) -> CandidateTransition:
        confs = [ev.confidence for ev in evidence if ev.confidence is not None] or [map_confidence]
        tests = tests or TransitionTests()
        if dynamic is None:
            dynamic = {"blocked": not tests.dynamically_available}
        return CandidateTransition(tid, episode_id, u, v, q, q2, action, evidence, max(0.0, min(1.0, float(availability))), min(confs + [map_confidence]), interface or {}, dynamic, float(cost), max(0.0, min(1.0, float(completion_value))), tests, metadata or {})


def _pudo_index(anchor_id: str) -> int:
    try:
        return int(anchor_id.split("_")[-1])
    except Exception:
        return 0
