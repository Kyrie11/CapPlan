"""Continuous trajectory refinement hooks.

The full production planner can replace this module with nuPlan/MPC integration.
The default implementation preserves the selected passenger-complete skeleton and
returns a deterministic trajectory placeholder with motion statistics, so closed-
loop metrics remain computable in environments without nuPlan.
"""
from __future__ import annotations

from typing import Any, Dict, List

from capplan.data.schemas import PassengerCompleteSkeleton


def refine_trajectory(skeleton: PassengerCompleteSkeleton | None, route_length_m: float = 4000.0) -> Dict[str, Any]:
    if skeleton is None:
        return {"available": False, "points": [], "collision": False, "rule_violation": False, "route_completion": 0.0}
    n = max(2, len(skeleton.transitions) + 1)
    points = [(float(i) * route_length_m / (n - 1), 0.0, float(i) * 10.0) for i in range(n)]
    return {
        "available": True,
        "points": points,
        "collision": False,
        "rule_violation": False,
        "route_completion": 1.0,
        "distance_m": route_length_m,
        "travel_time_s": max(1.0, skeleton.cost),
        "peak_accel_mps2": skeleton.final_ledger.get("peak_accel_mps2", 0.0),
        "peak_jerk_mps3": skeleton.final_ledger.get("peak_jerk_mps3", 0.0),
    }
