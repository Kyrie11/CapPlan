"""Metrics for executed pickup/dropoff handoff traces.

The metrics intentionally separate decisions, executed burden, information
use, and terminal outcomes.  An empty denominator is returned as ``None``
rather than silently converted to zero.
"""
from __future__ import annotations

import math
from statistics import mean
from typing import Any, Dict, Iterable, List, Sequence


def _rate(num: int, den: int) -> float | None:
    return num / den if den else None


def _quantile(values: Sequence[float], q: float) -> float | None:
    if not values:
        return None
    xs = sorted(float(x) for x in values)
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    if lo == hi:
        return xs[lo]
    return xs[lo] * (hi - pos) + xs[hi] * (pos - lo)


def _summary(values: Iterable[float]) -> Dict[str, float | int | None]:
    xs = [float(x) for x in values]
    return {
        "support": len(xs),
        "mean": mean(xs) if xs else None,
        "p50": _quantile(xs, 0.50),
        "p95": _quantile(xs, 0.95),
        "max": max(xs) if xs else None,
    }


def compute_handoff_metrics(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate method rollouts using explicit, auditable denominators.

    Expected per-row fields are documented in ``docs/HANDOFF_RECOURSE_UPGRADE.md``.
    Boolean fields are only counted when present, so partial simulator exports
    cannot masquerade as measured negatives.
    """
    n = len(rows)
    completed = [r for r in rows if r.get("handoff_confirmed") is True]
    terminal_known = [r for r in rows if "handoff_confirmed" in r]
    committed = [r for r in rows if r.get("passenger_commitment_issued") is True]
    recourse_eval = [r for r in committed if "recourse_preserved_after_commit" in r]
    false_commit_eval = [r for r in committed if "robust_recourse_existed_at_commit" in r]
    confirmation_eval = [r for r in rows if "reported_handoff_confirmed" in r and "handoff_confirmed" in r]
    stale_eval = [r for r in committed if "evidence_valid_at_commit" in r]
    feasible = [r for r in rows if r.get("oracle_initially_recoverable") is True]
    accepted = [r for r in rows if r.get("planner_accepted") is True]

    false_confirmation = sum(
        r.get("reported_handoff_confirmed") is True and r.get("handoff_confirmed") is not True
        for r in confirmation_eval
    )
    result: Dict[str, Any] = {
        "episodes": n,
        "outcomes": {
            "verified_handoff_completion_rate": _rate(len(completed), len(terminal_known)),
            "verified_handoff_completion_support": len(terminal_known),
            "safe_continuation_rate": _rate(sum(r.get("safe_continuation_confirmed") is True for r in rows), n),
            "assistance_transfer_rate": _rate(sum(r.get("assistance_transfer_confirmed") is True for r in rows), n),
            "planner_acceptance_rate": _rate(len(accepted), n),
            "feasible_service_coverage": _rate(
                sum(r.get("handoff_confirmed") is True for r in feasible), len(feasible)
            ),
        },
        "recourse": {
            "post_commitment_recourse_loss_rate": _rate(
                sum(r.get("recourse_preserved_after_commit") is False for r in recourse_eval),
                len(recourse_eval),
            ),
            "post_commitment_recourse_support": len(recourse_eval),
            "false_commit_rate": _rate(
                sum(r.get("robust_recourse_existed_at_commit") is False for r in false_commit_eval),
                len(false_commit_eval),
            ),
            "false_commit_support": len(false_commit_eval),
            "stale_evidence_commit_rate": _rate(
                sum(r.get("evidence_valid_at_commit") is False for r in stale_eval), len(stale_eval)
            ),
            "stale_evidence_commit_support": len(stale_eval),
        },
        "passenger_burden": {
            "executed_distance_m": _summary(r["executed_passenger_distance_m"] for r in rows if r.get("executed_passenger_distance_m") is not None),
            "waiting_time_s": _summary(r["passenger_waiting_time_s"] for r in rows if r.get("passenger_waiting_time_s") is not None),
            "excess_distance_vs_clairvoyant_m": _summary(r["excess_distance_vs_clairvoyant_m"] for r in rows if r.get("excess_distance_vs_clairvoyant_m") is not None),
            "redirections": _summary(r["passenger_redirections"] for r in rows if r.get("passenger_redirections") is not None),
            "unsafe_wait_exposure_s": _summary(r["unsafe_wait_exposure_s"] for r in rows if r.get("unsafe_wait_exposure_s") is not None),
        },
        "coordination": {
            "arrival_mismatch_s": _summary(abs(r["passenger_arrival_s"] - r["vehicle_arrival_s"]) for r in rows if r.get("passenger_arrival_s") is not None and r.get("vehicle_arrival_s") is not None),
            "missed_handoff_window_rate": _rate(sum(r.get("missed_handoff_window") is True for r in rows), n),
            "false_handoff_confirmation_rate": _rate(false_confirmation, len(confirmation_eval)),
            "confirmation_support": len(confirmation_eval),
            "acknowledgement_latency_s": _summary(r["acknowledgement_latency_s"] for r in rows if r.get("acknowledgement_latency_s") is not None),
        },
        "information": {
            "observations_per_episode": _summary(r["observation_count"] for r in rows if r.get("observation_count") is not None),
            "verification_latency_s": _summary(r["verification_latency_s"] for r in rows if r.get("verification_latency_s") is not None),
            "inconclusive_rate": _rate(sum(r.get("decision") == "inconclusive" for r in rows), n),
        },
        "efficiency": {
            "planning_latency_ms": _summary(r["planning_latency_ms"] for r in rows if r.get("planning_latency_ms") is not None),
            "expanded_states": _summary(r["expanded_states"] for r in rows if r.get("expanded_states") is not None),
        },
    }

    by_profile: Dict[str, Dict[str, Any]] = {}
    for profile in sorted({str(r.get("profile_id")) for r in rows if r.get("profile_id") is not None}):
        group = [r for r in rows if str(r.get("profile_id")) == profile]
        known = [r for r in group if "handoff_confirmed" in r]
        by_profile[profile] = {
            "episodes": len(group),
            "verified_handoff_completion_rate": _rate(
                sum(r.get("handoff_confirmed") is True for r in known), len(known)
            ),
            "post_commitment_recourse_loss_rate": _rate(
                sum(r.get("recourse_preserved_after_commit") is False for r in group if r.get("passenger_commitment_issued") is True and "recourse_preserved_after_commit" in r),
                sum(r.get("passenger_commitment_issued") is True and "recourse_preserved_after_commit" in r for r in group),
            ),
        }
    result["by_profile"] = by_profile
    measured = [v["verified_handoff_completion_rate"] for v in by_profile.values() if v["verified_handoff_completion_rate"] is not None]
    result["profile_robustness"] = {
        "worst_profile_completion_rate": min(measured) if measured else None,
        "max_min_gap": max(measured) - min(measured) if measured else None,
        "profile_count": len(measured),
    }
    return result

