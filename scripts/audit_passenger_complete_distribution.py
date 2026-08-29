#!/usr/bin/env python
"""Distribution audit for passenger-complete AbilityBench/CapPlan datasets.

This is deliberately a *quality* audit rather than a structural gate.  The
hybrid semantic audit proves label completeness and lifecycle consistency; this
script asks whether the resulting benchmark is informative for the paper's
passenger-complete claims: base/strict outcome balance, counterfactual binding,
failure-phase/resource diversity, and OD-to-route anchoring.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from capplan.utils.serialization import iter_jsonl

VERSION = "capplan_passenger_complete_distribution_audit_v1_20260829"


def _rows(path: Path) -> Iterable[Dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(path)
    return iter_jsonl(path)


def _rate(n: int, d: int) -> float:
    return float(n) / float(d) if d else 0.0


def _q(xs: list[float]) -> Dict[str, float | None]:
    ys = sorted(x for x in xs if math.isfinite(x))
    if not ys:
        return {"min": None, "p10": None, "median": None, "p90": None, "max": None}
    def at(p: float) -> float:
        if len(ys) == 1:
            return ys[0]
        pos = p * (len(ys) - 1)
        lo, hi = int(math.floor(pos)), int(math.ceil(pos))
        if lo == hi:
            return ys[lo]
        return ys[lo] * (hi-pos) + ys[hi] * (pos-lo)
    return {"min": ys[0], "p10": at(.1), "median": at(.5), "p90": at(.9), "max": ys[-1]}


def audit(dataset_dir: Path) -> Dict[str, Any]:
    requests = list(_rows(dataset_dir / "service_requests.jsonl"))
    skeletons = list(_rows(dataset_dir / "skeleton_labels.jsonl"))
    certs = list(_rows(dataset_dir / "certificate_labels.jsonl"))
    pairs = list(_rows(dataset_dir / "counterfactual_pairs.jsonl"))

    success = {str(x.get("passenger_id")) for x in skeletons}
    failure = {str(x.get("passenger_id")) for x in certs}
    outcome: Dict[str, bool] = {pid: True for pid in success}
    outcome.update({pid: False for pid in failure})

    profile_by_pid: Dict[str, str] = {}
    for r in requests:
        eid = str(r.get("episode_id") or "")
        profile = str(r.get("passenger_profile_id") or "unknown")
        profile_by_pid[f"{eid}:{profile}"] = profile

    profile_counts: Dict[str, Counter] = defaultdict(Counter)
    for pid, ok in outcome.items():
        profile = profile_by_pid.get(pid, pid.rsplit(":", 1)[-1] if ":" in pid else "unknown")
        profile_counts[profile]["success" if ok else "failure"] += 1
    profile_summary = {}
    for profile, c in sorted(profile_counts.items()):
        n = c["success"] + c["failure"]
        profile_summary[profile] = {
            "count": n,
            "success": c["success"],
            "failure": c["failure"],
            "success_rate": _rate(c["success"], n),
        }

    pair_outcomes: Dict[str, Counter] = defaultdict(Counter)
    for p in pairs:
        axis = str(p.get("counterfactual_axis") or "unknown")
        weak = str(p.get("weak_passenger_id") or "")
        strict = str(p.get("strict_passenger_id") or "")
        if weak not in outcome or strict not in outcome:
            pair_outcomes[axis]["missing_outcome"] += 1
            continue
        w, s = outcome[weak], outcome[strict]
        if w and s:
            pair_outcomes[axis]["both_success"] += 1
        elif w and not s:
            pair_outcomes[axis]["base_success_strict_fail"] += 1
        elif (not w) and (not s):
            pair_outcomes[axis]["both_fail"] += 1
        else:
            pair_outcomes[axis]["monotonic_violation"] += 1

    axis_summary = {}
    for axis, c in sorted(pair_outcomes.items()):
        total = sum(c.values())
        base_success = c["both_success"] + c["base_success_strict_fail"] + c["monotonic_violation"]
        axis_summary[axis] = {
            "pair_count": total,
            "both_success": c["both_success"],
            "base_success_strict_fail": c["base_success_strict_fail"],
            "both_fail": c["both_fail"],
            "monotonic_violation": c["monotonic_violation"],
            "missing_outcome": c["missing_outcome"],
            "binding_rate_all_pairs": _rate(c["base_success_strict_fail"], total),
            "binding_rate_given_base_success": _rate(c["base_success_strict_fail"], base_success),
        }

    cert_phase_by_profile: Dict[str, Counter] = defaultdict(Counter)
    cert_resource_by_profile: Dict[str, Counter] = defaultdict(Counter)
    phase = Counter(); resource = Counter()
    for c in certs:
        pid = str(c.get("passenger_id") or "")
        profile = profile_by_pid.get(pid, pid.rsplit(":", 1)[-1] if ":" in pid else "unknown")
        ph = str(c.get("phase") or "unknown")
        rs = str(c.get("resource_type") or "unknown")
        phase[ph] += 1; resource[rs] += 1
        cert_phase_by_profile[profile][ph] += 1
        cert_resource_by_profile[profile][rs] += 1

    route_o: list[float] = []
    route_d: list[float] = []
    sep: list[float] = []
    adjustment = Counter(); target_met = Counter()
    for r in requests:
        prov = r.get("od_provenance") if isinstance(r.get("od_provenance"), Mapping) else {}
        for arr, key in ((route_o, "route_origin_distance_m"), (route_d, "route_destination_distance_m"), (sep, "od_euclidean_separation_m")):
            try:
                v = float(prov.get(key))
                if math.isfinite(v): arr.append(v)
            except Exception:
                pass
        if "od_separation_adjustment" in prov:
            adjustment[str(prov.get("od_separation_adjustment") or "unknown")] += 1
        if "od_separation_target_met" in prov:
            target_met[str(bool(prov.get("od_separation_target_met"))).lower()] += 1

    def over(xs: list[float], threshold: float) -> Dict[str, float | int]:
        n = sum(x > threshold for x in xs)
        return {"count": n, "rate": _rate(n, len(xs))}

    quality_flags: list[str] = []
    overall_success = _rate(len(success), len(outcome))
    if overall_success < 0.05:
        quality_flags.append("overall_passenger_complete_success_below_5pct")
    if route_d and over(route_d, 500.0)["rate"] > 0.01:
        quality_flags.append("route_destination_anchor_outliers_gt500m")
    for axis, s in axis_summary.items():
        if s["pair_count"] and s["binding_rate_all_pairs"] < 0.01:
            quality_flags.append(f"counterfactual_axis_nearly_inactive:{axis}")

    return {
        "version": VERSION,
        "dataset_dir": str(dataset_dir),
        "passenger_contract_outcomes": {
            "success": len(success), "failure": len(failure), "total": len(outcome),
            "success_rate": overall_success,
        },
        "profile_outcome_summary": profile_summary,
        "counterfactual_axis_outcome_summary": axis_summary,
        "failure_phase_counts": dict(phase),
        "failure_resource_counts": dict(resource),
        "failure_phase_by_profile": {k: dict(v) for k, v in sorted(cert_phase_by_profile.items())},
        "failure_resource_by_profile": {k: dict(v) for k, v in sorted(cert_resource_by_profile.items())},
        "od_route_anchoring": {
            "origin_route_distance_m": _q(route_o),
            "destination_route_distance_m": _q(route_d),
            "od_separation_m": _q(sep),
            "origin_gt350m": over(route_o, 350.0),
            "destination_gt350m": over(route_d, 350.0),
            "origin_gt500m": over(route_o, 500.0),
            "destination_gt500m": over(route_d, 500.0),
            "separation_adjustment_counts": dict(adjustment),
            "separation_target_met_counts": dict(target_met),
        },
        "quality_flags": quality_flags,
        "interpretation": (
            "This report measures benchmark informativeness, not structural validity. "
            "Sparse positives can be trainable with weighting/sampling, but near-inactive "
            "counterfactual axes or route-anchor outliers weaken passenger-complete claims."
        ),
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset_dir", required=True)
    p.add_argument("--output", required=True)
    args = p.parse_args()
    report = audit(Path(args.dataset_dir))
    out = Path(args.output); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
