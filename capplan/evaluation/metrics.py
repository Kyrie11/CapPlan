"""Evaluation metrics for passenger-complete autonomous mobility.

Every metric accepts a list of episode dictionaries.  The closed-loop runner
creates these dictionaries, but tests and external evaluators can provide the
same input schema directly.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, Iterable, List, Sequence

EPS = 1e-9


def _mean(xs: Sequence[float]) -> float:
    return float(sum(xs) / len(xs)) if xs else 0.0


def collision_rate(episodes: List[Dict[str, Any]]) -> float:
    """CR = fraction of episodes with any collision.

    Input: ``episode['collision']`` boolean.  Output unit: fraction [0, 1].
    """
    return _mean([1.0 if e.get("collision", False) else 0.0 for e in episodes])


def route_completion(episodes: List[Dict[str, Any]]) -> float:
    """RC = completed route length / planned route length, averaged over episodes."""
    return _mean([float(e.get("completed_route_m", 0.0)) / (float(e.get("planned_route_m", 0.0)) + EPS) for e in episodes])


def traffic_rule_violation(episodes: List[Dict[str, Any]], per_km: bool = False) -> float:
    """TRV = fraction with any traffic-rule violation or count per km."""
    if per_km:
        total = sum(float(e.get("rule_violation_count", 0.0)) for e in episodes)
        km = sum(float(e.get("vehicle_distance_m", 0.0)) for e in episodes) / 1000.0
        return total / (km + EPS)
    return _mean([1.0 if e.get("rule_violation", False) or e.get("rule_violation_count", 0) > 0 else 0.0 for e in episodes])


def travel_time(episodes: List[Dict[str, Any]]) -> float:
    """TT = time from request to destination completion or failure, in seconds."""
    return _mean([float(e.get("travel_time_s", 0.0)) for e in episodes])


def detour_ratio(episodes: List[Dict[str, Any]]) -> float:
    """DR = vehicle distance / shortest traffic-feasible route distance."""
    return _mean([float(e.get("vehicle_distance_m", 0.0)) / (float(e.get("shortest_route_m", 0.0)) + EPS) for e in episodes])


def passenger_completion_rate(episodes: List[Dict[str, Any]]) -> float:
    """PCR = N^-1 sum_i I[PC(Omega_i,p_i)=1]."""
    return _mean([1.0 if e.get("passenger_complete", False) else 0.0 for e in episodes])


def traffic_safe_passenger_incomplete_rate(episodes: List[Dict[str, Any]], rc_threshold: float = 0.95) -> float:
    """TSPIR = fraction with no collision, route complete, and PC = 0."""
    return _mean([1.0 if (not e.get("collision", False) and float(e.get("route_completion", 0.0)) >= rc_threshold and not e.get("passenger_complete", False)) else 0.0 for e in episodes])


def phase_acceptance_rate(episodes: List[Dict[str, Any]]) -> float:
    """PAR = fraction whose service skeleton reaches accepting automaton state."""
    return _mean([1.0 if e.get("phase_accepted", False) else 0.0 for e in episodes])


def capability_violation_rate(episodes: List[Dict[str, Any]]) -> float:
    """CVR = average fraction of active hard capability clauses with negative margin."""
    vals = []
    for e in episodes:
        margins = list((e.get("capability_margins") or {}).values())
        vals.append(_mean([1.0 if float(m) < 0.0 else 0.0 for m in margins]) if margins else 0.0)
    return _mean(vals)


def capability_safety_margin(episodes: List[Dict[str, Any]]) -> float:
    """CSM = mean of worst normalized signed slack over active clauses."""
    vals = []
    for e in episodes:
        margins = list((e.get("capability_margins") or {}).values())
        vals.append(min([float(m) for m in margins]) if margins else (1.0 if e.get("passenger_complete", False) else -1.0))
    return _mean(vals)


def first_last_meter_feasibility(episodes: List[Dict[str, Any]]) -> float:
    """FLF = indicator that access and egress constraints hold."""
    return _mean([1.0 if e.get("first_last_meter_feasible", False) else 0.0 for e in episodes])


def boarding_alighting_feasibility(episodes: List[Dict[str, Any]]) -> float:
    """BAF = indicator that boarding/alighting interface constraints hold."""
    return _mean([1.0 if e.get("boarding_alighting_feasible", False) else 0.0 for e in episodes])


def motion_exposure_ratio(episodes: List[Dict[str, Any]]) -> float:
    """MER = D^motion(tau_v)/(B_p^motion + eps)."""
    return _mean([float(e.get("motion_exposure", 0.0)) / (float(e.get("motion_budget", 0.0)) + EPS) for e in episodes])


def motion_violation_rate(episodes: List[Dict[str, Any]]) -> float:
    """MVR = fraction violating acceleration, jerk, braking, or motion-exposure clauses."""
    return _mean([1.0 if e.get("motion_violation", False) else 0.0 for e in episodes])


def safe_budget_residual(episodes: List[Dict[str, Any]]) -> float:
    """SBR = remaining resource margin after completion; minimum normalized residual."""
    vals = []
    for e in episodes:
        residuals = list((e.get("budget_residuals") or e.get("capability_margins") or {}).values())
        vals.append(min([float(r) for r in residuals]) if residuals else 0.0)
    return _mean(vals)


def inconclusive_rate(episodes: List[Dict[str, Any]]) -> float:
    """IR = fraction of episodes failing uncertainty or confidence clauses."""
    return _mean([1.0 if e.get("inconclusive", False) else 0.0 for e in episodes])


def diagnostic_fidelity(episodes: List[Dict[str, Any]]) -> float:
    """DF = accuracy over failed phase, resource type, and evidence source.

    Input fields: ``certificate`` and ``oracle_certificate`` dictionaries.  If no
    oracle is present, successful episodes are ignored and failed episodes count
    as 0.
    """
    vals = []
    for e in episodes:
        if e.get("passenger_complete", False):
            continue
        c = e.get("certificate") or {}
        o = e.get("oracle_certificate") or {}
        if not o:
            vals.append(0.0)
        else:
            vals.append(_mean([
                1.0 if c.get("phase") == o.get("phase") else 0.0,
                1.0 if c.get("resource_type") == o.get("resource_type") else 0.0,
                1.0 if c.get("evidence_source") == o.get("evidence_source") else 0.0,
            ]))
    return _mean(vals) if vals else 1.0


def signed_margin_error(episodes: List[Dict[str, Any]]) -> float:
    """SME = MAE between reported and verifier-computed signed margins."""
    vals = []
    for e in episodes:
        c = e.get("certificate") or {}
        o = e.get("oracle_certificate") or {}
        if c and o and "signed_margin" in c and "signed_margin" in o:
            vals.append(abs(float(c["signed_margin"]) - float(o["signed_margin"])))
    return _mean(vals)


def capability_responsiveness(pairs: List[Dict[str, Any]]) -> float:
    """CRsp = fraction of counterfactual pairs with verifier-approved plan/cert change."""
    return _mean([1.0 if p.get("responsive", False) else 0.0 for p in pairs])


def efficiency_cost_of_accommodation(episodes: List[Dict[str, Any]]) -> float:
    """ECA = (TT_cap - TT_std)/(TT_std + eps), averaged over episodes."""
    return _mean([(float(e.get("tt_cap_s", e.get("travel_time_s", 0.0))) - float(e.get("tt_std_s", e.get("travel_time_s", 0.0)))) / (float(e.get("tt_std_s", e.get("travel_time_s", 0.0))) + EPS) for e in episodes])


def compute_all_metrics(episodes: List[Dict[str, Any]], counterfactual_pairs: List[Dict[str, Any]] | None = None) -> Dict[str, float]:
    return {
        "CR": collision_rate(episodes),
        "RC": route_completion(episodes),
        "TRV": traffic_rule_violation(episodes),
        "TT": travel_time(episodes),
        "DR": detour_ratio(episodes),
        "PCR": passenger_completion_rate(episodes),
        "TSPIR": traffic_safe_passenger_incomplete_rate(episodes),
        "PAR": phase_acceptance_rate(episodes),
        "CVR": capability_violation_rate(episodes),
        "CSM": capability_safety_margin(episodes),
        "FLF": first_last_meter_feasibility(episodes),
        "BAF": boarding_alighting_feasibility(episodes),
        "MER": motion_exposure_ratio(episodes),
        "MVR": motion_violation_rate(episodes),
        "SBR": safe_budget_residual(episodes),
        "IR": inconclusive_rate(episodes),
        "DF": diagnostic_fidelity(episodes),
        "SME": signed_margin_error(episodes),
        "CRsp": capability_responsiveness(counterfactual_pairs or []),
        "ECA": efficiency_cost_of_accommodation(episodes),
    }
