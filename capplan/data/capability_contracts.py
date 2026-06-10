"""Passenger capability contract generation."""
from __future__ import annotations

import random
from typing import List, Tuple

from capplan.data.schemas import CapabilityClause, CapabilityContract


def default_contract(passenger_id: str = "p0") -> CapabilityContract:
    return CapabilityContract(passenger_id=passenger_id, clauses=[
        CapabilityClause("access_distance_m", ["access"], "<=", 220.0, "cumulative", 0.95, None, "onboarding", "trip_planning"),
        CapabilityClause("egress_distance_m", ["egress"], "<=", 240.0, "cumulative", 0.95, None, "onboarding", "trip_planning"),
        CapabilityClause("slope", ["access", "egress"], "<=", 0.08, "upper", 0.9, None, "accessibility_map", "trip_planning"),
        CapabilityClause("cross_slope", ["access", "egress"], "<=", 0.04, "upper", 0.9, None, "accessibility_map", "trip_planning"),
        CapabilityClause("path_width_m", ["access", "egress"], ">=", 1.0, "lower", 0.9, None, "accessibility_map", "trip_planning"),
        CapabilityClause("step_free", ["access", "board", "alight", "egress"], "requires", True, "categorical", 1.0, None, "onboarding", "trip_planning"),
        CapabilityClause("curb_ramp", ["access", "egress"], "requires", True, "categorical", 0.95, None, "accessibility_map", "trip_planning"),
        CapabilityClause("ramp", ["board", "alight"], "requires", True, "categorical", 1.0, None, "onboarding", "trip_planning"),
        CapabilityClause("door_side", ["board", "alight"], "requires", True, "categorical", 1.0, None, "vehicle_spec", "trip_planning"),
        CapabilityClause("door_side_clearance_m", ["board", "alight"], ">=", 1.0, "lower", 0.95, None, "curbside_map", "trip_planning"),
        CapabilityClause("wait_exposure_s", ["wait"], "<=", 420.0, "cumulative", 0.9, None, "service_trace", "trip_planning"),
        CapabilityClause("identification_modality", ["wait", "board"], "in", ["audio", "haptic", "app"], "categorical", 1.0, None, "onboarding", "trip_planning"),
        CapabilityClause("peak_accel_mps2", ["ride"], "<=", 2.0, "upper", 0.9, None, "trajectory", "trip_planning"),
        CapabilityClause("peak_jerk_mps3", ["ride"], "<=", 3.0, "upper", 0.9, None, "trajectory", "trip_planning"),
        CapabilityClause("motion_exposure", ["ride"], "<=", 3.0, "cumulative", 0.9, None, "trajectory", "trip_planning"),
        CapabilityClause("map_confidence", ["access", "wait", "board", "alight", "egress"], ">=", 0.70, "lower", 0.9, None, "map/perception", "trip_planning"),
        CapabilityClause("blockage_risk", ["access", "wait", "board", "alight", "egress"], "<=", 0.35, "probabilistic", 0.9, 0.35, "prediction", "trip_planning"),
    ], metadata={"profile": "default_accessible"})


def sample_contracts(episode_id: str, num_contracts: int = 2, seed: int = 0) -> List[CapabilityContract]:
    rng = random.Random(seed)
    contracts: List[CapabilityContract] = []
    base = default_contract(f"{episode_id}:p0")
    contracts.append(base)
    for i in range(1, num_contracts):
        # Same-scene counterfactuals: stricter distance, width, motion, confidence.
        clauses = []
        for c in base.clauses:
            th = c.threshold
            if c.resource_name in ("access_distance_m", "egress_distance_m"):
                th = max(60.0, float(th) * rng.uniform(0.45, 0.85))
            elif c.resource_name == "path_width_m":
                th = min(1.8, float(th) + rng.uniform(0.1, 0.5))
            elif c.resource_name in ("peak_accel_mps2", "peak_jerk_mps3", "motion_exposure"):
                th = float(th) * rng.uniform(0.55, 0.85)
            elif c.resource_name == "map_confidence":
                th = min(0.95, float(th) + rng.uniform(0.05, 0.18))
            clauses.append(CapabilityClause(c.resource_name, list(c.phase_scope), c.operator, th, c.kind, c.confidence, c.risk_tolerance, c.source, c.consent_scope))
        contracts.append(CapabilityContract(f"{episode_id}:p{i}", clauses, {"profile": "counterfactual_stricter", "base_passenger": base.passenger_id}))
    return contracts[:num_contracts]
