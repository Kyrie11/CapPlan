#!/usr/bin/env python
"""Audit a built hybrid AbilityBench dataset for passenger-complete semantics.

This audit is intentionally stricter than generic file/schema validation.  It
checks same-scene counterfactual invariants, outcome completeness, OD provenance,
vehicle diversity, and label distributions.  It does not require simulated
hybrid values to be real-city measurements; instead it verifies that benchmark
truth is internally consistent and transparently provenance-tagged.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from capplan.utils.serialization import iter_jsonl

VERSION = "abilitybench_hybrid_dataset_audit_v1_20260823"


def _rows(path: Path) -> Iterable[Dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(path)
    return iter_jsonl(path)


def _quantiles(xs: List[float]) -> Dict[str, float | None]:
    ys = sorted(float(x) for x in xs if math.isfinite(float(x)))
    if not ys:
        return {"min": None, "p10": None, "median": None, "p90": None, "max": None}
    def q(p: float) -> float:
        if len(ys) == 1:
            return ys[0]
        pos = p * (len(ys)-1)
        lo = int(math.floor(pos)); hi = int(math.ceil(pos))
        if lo == hi:
            return ys[lo]
        return ys[lo] * (hi-pos) + ys[hi] * (pos-lo)
    return {"min": ys[0], "p10": q(.10), "median": q(.50), "p90": q(.90), "max": ys[-1]}


def audit(dataset_dir: Path, expected_requests_per_episode: int = 8) -> Dict[str, Any]:
    manifest = json.loads((dataset_dir / "dataset_manifest.json").read_text())
    episode_ids = [str(r["episode_id"]) for r in _rows(dataset_dir / "episodes.jsonl")]
    episode_set = set(episode_ids)
    requests = list(_rows(dataset_dir / "service_requests.jsonl"))
    contracts = list(_rows(dataset_dir / "capability_contracts.jsonl"))
    skeletons = list(_rows(dataset_dir / "skeleton_labels.jsonl"))
    certs = list(_rows(dataset_dir / "certificate_labels.jsonl"))
    pairs = list(_rows(dataset_dir / "counterfactual_pairs.jsonl"))
    vehicles = list(_rows(dataset_dir / "vehicle_interfaces.jsonl"))
    edge_labels = list(_rows(dataset_dir / "passenger_edge_labels.jsonl"))
    pudo = list(_rows(dataset_dir / "pudo_anchors.jsonl"))

    errors: List[str] = []
    warnings: List[str] = []
    req_by_ep: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in requests:
        req_by_ep[str(r.get("episode_id"))].append(r)

    # Every retained episode should expose the same counterfactual request set.
    req_counts = Counter(len(req_by_ep.get(eid, [])) for eid in episode_ids)
    for eid in episode_ids:
        rs = req_by_ep.get(eid, [])
        if len(rs) != expected_requests_per_episode:
            errors.append(f"episode {eid} has {len(rs)} service requests, expected {expected_requests_per_episode}")
            continue
        group_keys = {
            (
                str(r.get("counterfactual_group_id")),
                str(r.get("origin_entrance_id")),
                str(r.get("destination_entrance_id")),
                float(r.get("request_time_s", 0.0)),
                str(r.get("vehicle_id")),
            )
            for r in rs
        }
        if len(group_keys) != 1:
            errors.append(f"episode {eid} counterfactual requests are not same OD/time/vehicle: {len(group_keys)} variants")
        profiles = [str(r.get("passenger_profile_id")) for r in rs]
        if len(set(profiles)) != len(profiles):
            errors.append(f"episode {eid} has duplicated passenger_profile_id values")

    passenger_ids = {str(c.get("passenger_id")) for c in contracts}
    skeleton_by_pid = {str(x.get("passenger_id")): x for x in skeletons}
    cert_by_pid = {str(x.get("passenger_id")): x for x in certs}
    both = sorted(set(skeleton_by_pid).intersection(cert_by_pid))
    missing_outcome = sorted(passenger_ids - set(skeleton_by_pid) - set(cert_by_pid))
    extra_outcomes = sorted((set(skeleton_by_pid) | set(cert_by_pid)) - passenger_ids)
    if both:
        errors.append(f"{len(both)} passenger contracts have both skeleton and certificate")
    if missing_outcome:
        errors.append(f"{len(missing_outcome)} passenger contracts have neither skeleton nor certificate")
    if extra_outcomes:
        errors.append(f"{len(extra_outcomes)} outcomes do not reference a capability contract")

    pair_counts = Counter(str(p.get("episode_id")) for p in pairs)
    expected_pairs = max(0, expected_requests_per_episode - 1)
    bad_pair_eps = sorted(eid for eid in episode_ids if pair_counts[eid] != expected_pairs)
    if bad_pair_eps:
        errors.append(f"{len(bad_pair_eps)} episodes do not have {expected_pairs} explicit base-vs-variant counterfactual pairs")
    pair_axes = Counter(str(p.get("counterfactual_axis") or "unknown") for p in pairs)

    # Outcome/diagnostic distributions are not hard-balanced here, but near-total
    # collapse makes T3/T5 training uninformative and is surfaced as a warning.
    success_rate = len(skeletons) / max(1, len(passenger_ids))
    if success_rate < 0.05 or success_rate > 0.95:
        warnings.append(f"passenger-complete success rate is highly imbalanced: {success_rate:.4f}")
    cert_phase = Counter(str(c.get("phase") or "unknown") for c in certs)
    cert_resource = Counter(str(c.get("resource_type") or "unknown") for c in certs)
    if certs and len(cert_resource) < 3:
        warnings.append(f"failure certificates cover only {len(cert_resource)} resource types")

    y_pos = 0; y_total = 0
    for r in edge_labels:
        y_total += 1
        y_pos += int(bool(r.get("y_e_p")))
    edge_positive_rate = y_pos / max(1, y_total)

    vehicle_ids_by_ep: Dict[str, set[str]] = defaultdict(set)
    vehicle_type_counts = Counter()
    for v in vehicles:
        vehicle_ids_by_ep[str(v.get("episode_id"))].add(str(v.get("vehicle_id")))
        vehicle_type_counts[str(v.get("fleet_type") or "unknown")] += 1
    assigned_vehicle_counts = Counter(str(r.get("vehicle_id") or "missing") for r in requests)
    if len([k for k in assigned_vehicle_counts if k != "missing"]) < 3:
        warnings.append("fewer than three primary vehicle interface variants are assigned across the dataset")

    od_kind_counts = Counter()
    frontage_count = 0
    od_sep: List[float] = []
    route_o_dist: List[float] = []
    route_d_dist: List[float] = []
    time_sources = Counter()
    local_hours: List[float] = []
    for r in requests:
        prov = r.get("od_provenance") if isinstance(r.get("od_provenance"), Mapping) else {}
        od_kind_counts[str(prov.get("kind") or "unknown")] += 1
        frontage_count += int(bool(r.get("hybrid_frontage_proxy_od")))
        for dest, key in [(od_sep, "od_euclidean_separation_m"), (route_o_dist, "route_origin_distance_m"), (route_d_dist, "route_destination_distance_m")]:
            try:
                if prov.get(key) is not None:
                    dest.append(float(prov[key]))
            except Exception:
                pass
        time_sources[str(r.get("request_time_source") or "unknown")] += 1
        try:
            if r.get("request_local_hour") is not None:
                local_hours.append(float(r.get("request_local_hour")))
        except Exception:
            pass
    frontage_rate = frontage_count / max(1, len(requests))
    if frontage_rate > 0.90:
        warnings.append(f"{frontage_rate:.1%} of requests use simulated frontage access points; consider reporting this explicitly")

    pudo_by_ep = Counter(str(p.get("episode_id")) for p in pudo)
    bad_pudo_eps = sorted(eid for eid in episode_ids if pudo_by_ep[eid] < 2)
    if bad_pudo_eps:
        errors.append(f"{len(bad_pudo_eps)} retained episodes contain fewer than two PUDO anchors")
    curb_sides = Counter(str(p.get("side") or "unknown") for p in pudo)
    pudo_truth_modes = Counter(str(p.get("truth_mode") or (p.get("metadata") or {}).get("truth_mode") or "unknown") for p in pudo)

    # For explicit monotonic pairs, a stricter contract must never succeed when
    # the base contract fails under identical scene/OD/vehicle evidence.
    monotonic_violations = []
    outcome = {pid: True for pid in skeleton_by_pid}
    outcome.update({pid: False for pid in cert_by_pid})
    for p in pairs:
        if not bool(p.get("expected_monotonic")):
            continue
        weak = str(p.get("weak_passenger_id")); strict = str(p.get("strict_passenger_id"))
        if weak in outcome and strict in outcome and (not outcome[weak]) and outcome[strict]:
            monotonic_violations.append(str(p.get("pair_id")))
    if monotonic_violations:
        errors.append(f"{len(monotonic_violations)} expected-monotonic pairs have strict success while base fails")

    report = {
        "status": "PASS" if not errors else "FAIL",
        "version": VERSION,
        "dataset_dir": str(dataset_dir),
        "source_policy": manifest.get("source_policy") or "merged",
        "num_episodes": len(episode_ids),
        "num_service_requests": len(requests),
        "num_contracts": len(passenger_ids),
        "request_count_per_episode_distribution": {str(k): v for k, v in sorted(req_counts.items())},
        "counterfactual_pair_count": len(pairs),
        "counterfactual_axis_counts": dict(pair_axes),
        "monotonic_violation_count": len(monotonic_violations),
        "passenger_complete_success_count": len(skeletons),
        "failure_certificate_count": len(certs),
        "passenger_complete_success_rate": success_rate,
        "certificate_phase_counts": dict(cert_phase),
        "certificate_resource_counts": dict(cert_resource),
        "passenger_edge_positive_rate": edge_positive_rate,
        "vehicle_assignment_counts": dict(assigned_vehicle_counts),
        "vehicle_type_row_counts": dict(vehicle_type_counts),
        "od_provenance_kind_counts": dict(od_kind_counts),
        "frontage_proxy_request_rate": frontage_rate,
        "od_separation_m": _quantiles(od_sep),
        "route_origin_anchor_distance_m": _quantiles(route_o_dist),
        "route_destination_anchor_distance_m": _quantiles(route_d_dist),
        "request_time_source_counts": dict(time_sources),
        "request_local_hour": _quantiles(local_hours),
        "pudo_curb_side_counts": dict(curb_sides),
        "pudo_truth_mode_counts": dict(pudo_truth_modes),
        "errors": errors[:200],
        "warnings": warnings[:200],
        "interpretation": (
            "PASS means the hybrid benchmark is internally coherent for passenger-complete training/evaluation. "
            "It does not mean simulated fields are measured city ground truth."
        ),
    }
    return report


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset_dir", required=True)
    p.add_argument("--expected_requests_per_episode", type=int, default=8)
    p.add_argument("--output", required=True)
    p.add_argument("--fail_on_error", action="store_true")
    args = p.parse_args()
    report = audit(Path(args.dataset_dir), args.expected_requests_per_episode)
    out = Path(args.output); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"HYBRID_BENCHMARK_AUDIT_CHECK={report['status']}")
    if args.fail_on_error and report["status"] != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
