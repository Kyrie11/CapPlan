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


def _percentile(xs: Sequence[float], q: float) -> float:
    vals = sorted(float(x) for x in xs)
    if not vals:
        return 0.0
    if len(vals) == 1:
        return vals[0]
    pos = max(0.0, min(1.0, float(q))) * (len(vals) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(vals) - 1)
    frac = pos - lo
    return vals[lo] * (1.0 - frac) + vals[hi] * frac


def _macro_f1(y_true: Sequence[str], y_pred: Sequence[str]) -> float:
    labels = sorted(set(str(x) for x in y_true) | set(str(x) for x in y_pred))
    if not labels:
        return 1.0
    vals = []
    for label in labels:
        tp = sum(1 for t, p in zip(y_true, y_pred) if str(t) == label and str(p) == label)
        fp = sum(1 for t, p in zip(y_true, y_pred) if str(t) != label and str(p) == label)
        fn = sum(1 for t, p in zip(y_true, y_pred) if str(t) == label and str(p) != label)
        precision = tp / max(tp + fp, 1)
        recall = tp / max(tp + fn, 1)
        vals.append(2.0 * precision * recall / max(precision + recall, EPS))
    return _mean(vals)


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
    """TSPIR = traffic-safe route-complete but passenger-incomplete rate.

    Vehicle route success is evaluated independently of passenger completion so
    first/last-meter or interface failures remain visible.
    """
    vals = []
    for e in episodes:
        traffic_safe = bool(e.get("traffic_safe", (not e.get("collision", False) and e.get("drivable_area", True) and not e.get("rule_violation", False))))
        route_complete = float(e.get("route_completion", 0.0)) >= rc_threshold
        vals.append(1.0 if (traffic_safe and route_complete and not e.get("passenger_complete", False)) else 0.0)
    return _mean(vals)


def phase_acceptance_rate(episodes: List[Dict[str, Any]]) -> float:
    """PAR = fraction whose service skeleton reaches accepting automaton state."""
    return _mean([1.0 if e.get("phase_accepted", False) else 0.0 for e in episodes])


def capability_violation_rate(episodes: List[Dict[str, Any]], accepted_only: bool = True) -> float:
    """Capability violation rate over actually evaluated hard-clause margins.

    Missing margins are *not* interpreted as zero violations.  Publication-facing
    ``CVR`` uses returned/accepted plans when possible; ``CVR_all_evaluated`` is
    also exposed by :func:`compute_all_metrics` for diagnostics.
    """
    vals = []
    for e in episodes:
        margins = list((e.get("capability_margins") or {}).values())
        if not margins:
            continue
        if accepted_only and not bool(e.get("phase_accepted", e.get("plan_returned", False))):
            continue
        vals.append(_mean([1.0 if float(m) < 0.0 else 0.0 for m in margins]))
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


def diagnostic_breakdown(episodes: List[Dict[str, Any]]) -> Dict[str, float]:
    """Verifier-referenced certificate metrics used by T5.

    Returns phase accuracy and macro-F1 for resource/evidence source separately,
    matching the paper tables more faithfully than a single averaged score.
    """
    phase_true: List[str] = []
    phase_pred: List[str] = []
    resource_true: List[str] = []
    resource_pred: List[str] = []
    source_true: List[str] = []
    source_pred: List[str] = []
    for e in episodes:
        o = e.get("oracle_certificate") or {}
        if not o:
            continue
        c = e.get("certificate") or {}
        phase_true.append(str(o.get("phase", "<missing>")))
        phase_pred.append(str(c.get("phase", "<missing>")))
        resource_true.append(str(o.get("resource_type", "<missing>")))
        resource_pred.append(str(c.get("resource_type", "<missing>")))
        source_true.append(str(o.get("evidence_source", "<missing>")))
        source_pred.append(str(c.get("evidence_source", "<missing>")))
    if not phase_true:
        return {
            "phase_accuracy": 1.0, "phase_macro_f1": 1.0,
            "resource_macro_f1": 1.0, "source_macro_f1": 1.0,
            "exact_match": 1.0, "n": 0.0,
        }
    exact = [
        1.0 if (pt == pp and rt == rp and st == sp) else 0.0
        for pt, pp, rt, rp, st, sp in zip(
            phase_true, phase_pred, resource_true, resource_pred, source_true, source_pred
        )
    ]
    return {
        "phase_accuracy": _mean([1.0 if a == b else 0.0 for a, b in zip(phase_true, phase_pred)]),
        # Phase accuracy is majority-sensitive (the frozen benchmark is access-heavy).
        # Macro-F1 is the publication-facing complement for T5 diversity/fidelity.
        "phase_macro_f1": _macro_f1(phase_true, phase_pred),
        "resource_macro_f1": _macro_f1(resource_true, resource_pred),
        "source_macro_f1": _macro_f1(source_true, source_pred),
        # Strong certificate metric: all three categorical fields must agree with
        # the independent verifier on the same failed request.
        "exact_match": _mean(exact),
        "n": float(len(phase_true)),
    }


def diagnostic_fidelity(episodes: List[Dict[str, Any]]) -> float:
    """DF = mean of T5 phase accuracy, resource macro-F1, and source macro-F1."""
    d = diagnostic_breakdown(episodes)
    return _mean([d["phase_macro_f1"], d["resource_macro_f1"], d["source_macro_f1"]])


def signed_margin_error(episodes: List[Dict[str, Any]]) -> float:
    """SME = MAE between reported and verifier-computed signed margins."""
    vals = []
    for e in episodes:
        c = e.get("certificate") or {}
        o = e.get("oracle_certificate") or {}
        if c and o and "signed_margin" in c and "signed_margin" in o:
            vals.append(abs(float(c["signed_margin"]) - float(o["signed_margin"])))
    return _mean(vals)




def plan_return_rate(episodes: List[Dict[str, Any]]) -> float:
    """Fraction of requests for which TSBS returns any service skeleton."""
    return _mean([1.0 if e.get("plan_returned", False) else 0.0 for e in episodes])


def passenger_completion_decision_breakdown(episodes: List[Dict[str, Any]]) -> Dict[str, float]:
    """Verifier-referenced passenger-complete decision quality.

    ``PCR`` alone is not sufficient for mechanism ablations: relaxing a hard
    capability mechanism can increase returned-plan rate while accepting
    requests that the exhaustive offline verifier marks infeasible.  These
    metrics score the planner's *success decision* against the frozen oracle.
    """
    rows = [e for e in episodes if bool(e.get("oracle_label_available", False))]
    if not rows:
        return {
            "tp": 0.0, "fp": 0.0, "fn": 0.0, "tn": 0.0,
            "precision": 0.0, "recall": 0.0, "f1": 0.0,
            "accuracy": 0.0, "balanced_accuracy": 0.0,
            "false_accept_rate": 0.0, "false_reject_rate": 0.0,
            "oracle_pcr": 0.0, "evaluable_count": 0.0,
        }
    tp = fp = fn = tn = 0
    for e in rows:
        pred = bool(e.get("passenger_complete", False))
        truth = bool(e.get("oracle_passenger_complete", False))
        if pred and truth:
            tp += 1
        elif pred and not truth:
            fp += 1
        elif not pred and truth:
            fn += 1
        else:
            tn += 1
    precision = tp / max(tp + fp, 1)
    recall = tp / max(tp + fn, 1)
    f1 = 2.0 * precision * recall / max(precision + recall, EPS)
    tnr = tn / max(tn + fp, 1)
    return {
        "tp": float(tp), "fp": float(fp), "fn": float(fn), "tn": float(tn),
        "precision": float(precision),
        "recall": float(recall),
        "f1": float(f1),
        "accuracy": float((tp + tn) / max(len(rows), 1)),
        "balanced_accuracy": float(0.5 * (recall + tnr)),
        # False acceptance is normalized by oracle-infeasible requests; false
        # rejection is normalized by oracle-feasible requests.
        "false_accept_rate": float(fp / max(fp + tn, 1)),
        "false_reject_rate": float(fn / max(fn + tp, 1)),
        "oracle_pcr": float((tp + fn) / max(len(rows), 1)),
        "evaluable_count": float(len(rows)),
    }


def counterfactual_breakdown(pairs: List[Dict[str, Any]]) -> Dict[str, float]:
    """Outcome-aware T4 diagnostics.

    Aggregate CRsp can look deceptively high when both the oracle and a collapsed
    model fail on most pairs.  Report changed/stable behavior plus precision and
    recall of the central capability-induced weak-success -> strict-failure event.
    """
    if not pairs:
        return {
            "outcome_pair_accuracy": 0.0,
            "response_accuracy_oracle_changed": 0.0,
            "response_accuracy_oracle_stable": 0.0,
            "success_flip_precision": 0.0,
            "success_flip_recall": 0.0,
            "oracle_changed_count": 0.0,
            "oracle_success_flip_count": 0.0,
            "model_success_flip_count": 0.0,
            "base_oracle_success_rate": 0.0,
            "base_model_success_rate": 0.0,
        }
    outcome = [1.0 if p.get("outcomes_match_oracle", False) else 0.0 for p in pairs]
    changed = [p for p in pairs if p.get("oracle_changed", False)]
    stable = [p for p in pairs if not p.get("oracle_changed", False)]
    oracle_flips = [p for p in pairs if bool(p.get("oracle_weak_success")) and not bool(p.get("oracle_strict_success"))]
    model_flips = [p for p in pairs if bool(p.get("model_weak_success")) and not bool(p.get("model_strict_success"))]
    correct_flip_ids = {
        (str(p.get("episode_id")), str(p.get("weak_passenger_id")), str(p.get("strict_passenger_id")))
        for p in oracle_flips
        if bool(p.get("model_weak_success")) and not bool(p.get("model_strict_success"))
    }
    precision = len(correct_flip_ids) / max(len(model_flips), 1)
    recall = len(correct_flip_ids) / max(len(oracle_flips), 1)
    return {
        "outcome_pair_accuracy": _mean(outcome),
        "response_accuracy_oracle_changed": _mean([1.0 if p.get("response_correct", False) else 0.0 for p in changed]),
        "response_accuracy_oracle_stable": _mean([1.0 if p.get("response_correct", False) else 0.0 for p in stable]),
        "success_flip_precision": float(precision),
        "success_flip_recall": float(recall),
        "oracle_changed_count": float(len(changed)),
        "oracle_success_flip_count": float(len(oracle_flips)),
        "model_success_flip_count": float(len(model_flips)),
        "base_oracle_success_rate": _mean([1.0 if p.get("oracle_weak_success", False) else 0.0 for p in pairs]),
        "base_model_success_rate": _mean([1.0 if p.get("model_weak_success", False) else 0.0 for p in pairs]),
    }

def capability_responsiveness(pairs: List[Dict[str, Any]]) -> float:
    """CRsp = agreement with verifier-approved counterfactual response behavior."""
    vals = []
    for p in pairs:
        if "response_correct" in p:
            vals.append(1.0 if p.get("response_correct") else 0.0)
        elif "responsive" in p:  # backward-compatible historical rows
            vals.append(1.0 if p.get("responsive") else 0.0)
    return _mean(vals)


def efficiency_cost_of_accommodation(episodes: List[Dict[str, Any]]) -> float:
    """ECA = nonnegative accommodation cost or nan-excluded average.

    Only episodes with both capability-aware and standard service times available
    are included.  Negative values caused by different baselines are clipped to
    zero rather than interpreted as negative accommodation burden.
    """
    vals = []
    for e in episodes:
        if "tt_cap_s" not in e or "tt_std_s" not in e:
            continue
        std = float(e.get("tt_std_s", 0.0))
        if std <= 0:
            continue
        vals.append(max(0.0, (float(e.get("tt_cap_s", 0.0)) - std) / (std + EPS)))
    return _mean(vals)


def search_expansion_mean(episodes: List[Dict[str, Any]]) -> float:
    return _mean([float(e.get("search_expansions", 0.0)) for e in episodes if e.get("search_expansions") is not None])


def search_expansion_p95(episodes: List[Dict[str, Any]]) -> float:
    return _percentile([float(e.get("search_expansions", 0.0)) for e in episodes if e.get("search_expansions") is not None], 0.95)


def continuation_pruned_mean(episodes: List[Dict[str, Any]]) -> float:
    return _mean([float(e.get("continuation_pruned", 0.0)) for e in episodes if e.get("continuation_pruned") is not None])


def continuation_pruned_p95(episodes: List[Dict[str, Any]]) -> float:
    return _percentile([float(e.get("continuation_pruned", 0.0)) for e in episodes if e.get("continuation_pruned") is not None], 0.95)


def viability_pruned_mean(episodes: List[Dict[str, Any]]) -> float:
    return _mean([float(e.get("viability_pruned", 0.0)) for e in episodes if e.get("viability_pruned") is not None])


def viability_structural_pruned_mean(episodes: List[Dict[str, Any]]) -> float:
    return _mean([float(e.get("viability_structural_pruned", 0.0)) for e in episodes if e.get("viability_structural_pruned") is not None])


def viability_typed_pruned_mean(episodes: List[Dict[str, Any]]) -> float:
    return _mean([float(e.get("viability_typed_pruned", 0.0)) for e in episodes if e.get("viability_typed_pruned") is not None])


def viability_path_checks_mean(episodes: List[Dict[str, Any]]) -> float:
    return _mean([float(e.get("viability_path_checks", 0.0)) for e in episodes if e.get("viability_path_checks") is not None])


def viability_cache_hits_mean(episodes: List[Dict[str, Any]]) -> float:
    return _mean([float(e.get("viability_cache_hits", 0.0)) for e in episodes if e.get("viability_cache_hits") is not None])


def planning_latency_mean_ms(episodes: List[Dict[str, Any]]) -> float:
    return _mean([float(e.get("planning_latency_ms", 0.0)) for e in episodes if e.get("planning_latency_ms") is not None])


def planning_latency_p95_ms(episodes: List[Dict[str, Any]]) -> float:
    return _percentile([float(e.get("planning_latency_ms", 0.0)) for e in episodes if e.get("planning_latency_ms") is not None], 0.95)




def _mean_present_numeric(episodes: List[Dict[str, Any]], key: str) -> float:
    vals = []
    for e in episodes:
        if e.get(key) is None:
            continue
        try:
            vals.append(float(e[key]))
        except Exception:
            continue
    return _mean(vals)

def compute_all_metrics(episodes: List[Dict[str, Any]], counterfactual_pairs: List[Dict[str, Any]] | None = None) -> Dict[str, float]:
    diag = diagnostic_breakdown(episodes)
    pairs = counterfactual_pairs or []
    cf = counterfactual_breakdown(pairs)
    pc_decision = passenger_completion_decision_breakdown(episodes)
    metrics: Dict[str, float] = {
        "CR": collision_rate(episodes),
        "RC": route_completion(episodes),
        "TRV": traffic_rule_violation(episodes),
        "TT": travel_time(episodes),
        "DR": detour_ratio(episodes),
        "PCR": passenger_completion_rate(episodes),
        "OraclePCR": pc_decision["oracle_pcr"],
        "PCDecisionPrecision": pc_decision["precision"],
        "PCDecisionRecall": pc_decision["recall"],
        "PCDecisionF1": pc_decision["f1"],
        "PCDecisionAccuracy": pc_decision["accuracy"],
        "PCDecisionBalancedAccuracy": pc_decision["balanced_accuracy"],
        "PCFalseAcceptRate": pc_decision["false_accept_rate"],
        "PCFalseRejectRate": pc_decision["false_reject_rate"],
        "PCDecisionTP": pc_decision["tp"],
        "PCDecisionFP": pc_decision["fp"],
        "PCDecisionFN": pc_decision["fn"],
        "PCDecisionTN": pc_decision["tn"],
        "PCDecisionEvaluableCount": pc_decision["evaluable_count"],
        "TSPIR": traffic_safe_passenger_incomplete_rate(episodes),
        "PAR": phase_acceptance_rate(episodes),
        "PlanReturnRate": plan_return_rate(episodes),
        "CVR": capability_violation_rate(episodes, accepted_only=True),
        "CVR_all_evaluated": capability_violation_rate(episodes, accepted_only=False),
        "CSM": capability_safety_margin(episodes),
        "FLF": first_last_meter_feasibility(episodes),
        "BAF": boarding_alighting_feasibility(episodes),
        "MER": motion_exposure_ratio(episodes),
        "MVR": motion_violation_rate(episodes),
        "SBR": safe_budget_residual(episodes),
        "IR": inconclusive_rate(episodes),
        "DF": diagnostic_fidelity(episodes),
        "DF_phase_accuracy": diag["phase_accuracy"],
        "DF_phase_macro_f1": diag["phase_macro_f1"],
        "DF_resource_macro_f1": diag["resource_macro_f1"],
        "DF_source_macro_f1": diag["source_macro_f1"],
        "DF_certificate_exact_match": diag["exact_match"],
        "SME": signed_margin_error(episodes),
        "CRsp": capability_responsiveness(pairs),
        "CF_outcome_pair_accuracy": cf["outcome_pair_accuracy"],
        "CF_response_accuracy_oracle_changed": cf["response_accuracy_oracle_changed"],
        "CF_response_accuracy_oracle_stable": cf["response_accuracy_oracle_stable"],
        "CF_success_flip_precision": cf["success_flip_precision"],
        "CF_success_flip_recall": cf["success_flip_recall"],
        "CF_oracle_changed_count": cf["oracle_changed_count"],
        "CF_oracle_success_flip_count": cf["oracle_success_flip_count"],
        "CF_model_success_flip_count": cf["model_success_flip_count"],
        "CF_base_oracle_success_rate": cf["base_oracle_success_rate"],
        "CF_base_model_success_rate": cf["base_model_success_rate"],
        "ECA": efficiency_cost_of_accommodation(episodes),
        "ECA_evaluable_count": float(sum(1 for e in episodes if float(e.get("tt_std_s", 0.0) or 0.0) > 0.0)),
        "TSBS_expansions_mean": search_expansion_mean(episodes),
        "TSBS_expansions_p95": search_expansion_p95(episodes),
        "CCE_pruned_mean": continuation_pruned_mean(episodes),
        "CCE_pruned_p95": continuation_pruned_p95(episodes),
        "CVK_pruned_mean": viability_pruned_mean(episodes),
        "CVK_structural_pruned_mean": viability_structural_pruned_mean(episodes),
        "CVK_typed_pruned_mean": viability_typed_pruned_mean(episodes),
        "CVK_path_checks_mean": viability_path_checks_mean(episodes),
        "CVK_cache_hits_mean": viability_cache_hits_mean(episodes),
        "PlannerLatency_ms_mean": planning_latency_mean_ms(episodes),
        "PlannerLatency_ms_p95": planning_latency_p95_ms(episodes),
    }
    # Preserve standard nuPlan scenario metrics when an integrated or post-hoc
    # vehicle-metric source provides them. Publication semantics are reported
    # separately by the closed-loop runner; these aggregates do not by themselves
    # imply a method-specific integrated simulation.
    for key in [
        "at_fault_collision_rate", "drivable_area_compliance", "ego_progress_along_expert_route",
        "time_to_collision_within_bound", "speed_limit_compliance", "driving_direction_compliance",
        "comfort", "nuplan_score",
    ]:
        if any(e.get(key) is not None for e in episodes):
            metrics[f"nuplan::{key}"] = _mean_present_numeric(episodes, key)

    # Per-axis counterfactual reporting is important because a single aggregate
    # CRsp can hide an inactive capability dimension.
    axes = sorted({str(p.get("counterfactual_axis") or p.get("axis")) for p in pairs if (p.get("counterfactual_axis") or p.get("axis")) is not None})
    for axis in axes:
        subset = [p for p in pairs if str(p.get("counterfactual_axis") or p.get("axis")) == axis]
        metrics[f"CRsp_axis::{axis}"] = capability_responsiveness(subset)
        axis_cf = counterfactual_breakdown(subset)
        metrics[f"CF_success_flip_precision_axis::{axis}"] = axis_cf["success_flip_precision"]
        metrics[f"CF_success_flip_recall_axis::{axis}"] = axis_cf["success_flip_recall"]
        metrics[f"CF_success_flip_support_axis::{axis}"] = axis_cf["oracle_success_flip_count"]
        metrics[f"CF_model_success_flip_support_axis::{axis}"] = axis_cf["model_success_flip_count"]
        metrics[f"CF_response_changed_axis::{axis}"] = axis_cf["response_accuracy_oracle_changed"]
    return metrics

