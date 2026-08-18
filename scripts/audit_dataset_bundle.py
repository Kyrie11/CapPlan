#!/usr/bin/env python
"""Cross-split publication audit for a CapPlan/AbilityBench dataset bundle.

This complements per-split audit_dataset_quality.py.  It verifies official split
identity, same-scene counterfactual completeness, endpoint/PUDO evidence
provenance, four-city coverage, and reports physical curb-site reuse.  Site
reuse is reported rather than silently treated as clean because nuPlan's
traffic split and a site-disjoint accessibility split are different concepts.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capplan.utils.serialization import dump_json, read_jsonl

MAP_TO_CITY = {
    "us-ma-boston": "boston",
    "us-pa-pittsburgh-hazelwood": "pittsburgh",
    "us-nv-las-vegas-strip": "vegas",
    "sg-one-north": "singapore",
}
REQUIRED_CF_AXES = {
    "access_distance", "step_free", "min_width", "ramp_lift",
    "door_side_clearance", "ride_motion", "confidence",
}
REQUIRED_ELIGIBLE_PUDO = {
    "site_id", "curb_height_m", "sidewalk_width_m", "deployment_clearance_m",
    "curb_ramp", "running_slope", "cross_slope", "surface",
    "legal_basis", "legal_stop_source", "legal_stop_tier",
}


def _safe(path: Path) -> List[Dict[str, Any]]:
    return read_jsonl(path) if path.exists() else []


def _episode_identity(row: Dict[str, Any]) -> Tuple[str, str, str]:
    return (
        str(row.get("episode_id") or ""),
        str(row.get("scenario_token") or ""),
        str(row.get("log_name") or ""),
    )


def _episode_city(row: Dict[str, Any]) -> str:
    m = str(row.get("map_name") or (row.get("metadata") or {}).get("map_name") or "")
    return MAP_TO_CITY.get(m, m or "unknown")


def _graph_endpoint_coverage(root: Path, eid: str) -> Dict[str, Any] | None:
    p = root / "accessibility_graphs" / f"{eid}.jsonl"
    if not p.exists():
        return None
    for row in _safe(p):
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        cov = meta.get("paper_endpoint_pudo_coverage")
        if isinstance(cov, dict):
            return cov
    return None


def _audit_one(split: str, root: Path, expected_profiles: int) -> Dict[str, Any]:
    episodes = _safe(root / "episodes.jsonl")
    pudos = _safe(root / "pudo_anchors.jsonl")
    requests = _safe(root / "service_requests.jsonl")
    pairs = _safe(root / "counterfactual_pairs.jsonl")
    excluded = _safe(root / "excluded_episodes.jsonl")
    manifest = json.loads((root / "dataset_manifest.json").read_text(encoding="utf-8")) if (root / "dataset_manifest.json").exists() else {}

    eids = {str(e.get("episode_id")) for e in episodes}
    scenario_tokens = {str(e.get("scenario_token")) for e in episodes if e.get("scenario_token")}
    logs = {str(e.get("log_name")) for e in episodes if e.get("log_name")}
    cities = Counter(_episode_city(e) for e in episodes)

    req_by_ep: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for r in requests:
        req_by_ep[str(r.get("episode_id"))].append(r)
    pair_axes: Dict[str, Set[str]] = defaultdict(set)
    for r in pairs:
        axis = str(r.get("counterfactual_axis") or "")
        if axis:
            pair_axes[str(r.get("episode_id"))].add(axis)

    request_issues = []
    for eid in sorted(eids):
        rows = req_by_ep.get(eid, [])
        if len(rows) != expected_profiles:
            request_issues.append({"episode_id": eid, "issue": "request_count", "count": len(rows), "expected": expected_profiles})
            continue
        odt = {(str(r.get("origin_entrance_id")), str(r.get("destination_entrance_id")), float(r.get("request_time_s", 0.0))) for r in rows}
        if len(odt) != 1:
            request_issues.append({"episode_id": eid, "issue": "counterfactual_not_same_od_time", "values": sorted(map(str, odt))[:5]})
        axes = pair_axes.get(eid, set())
        missing = sorted(REQUIRED_CF_AXES - axes)
        if missing:
            request_issues.append({"episode_id": eid, "issue": "missing_counterfactual_axes", "missing": missing})

    eligible = [p for p in pudos if bool(p.get("paper_eligible"))]
    pudo_issues = []
    sites: Set[str] = set()
    sites_by_ep: Dict[str, Set[str]] = defaultdict(set)
    for p in eligible:
        missing = sorted(k for k in REQUIRED_ELIGIBLE_PUDO if p.get(k) in (None, ""))
        prov = p.get("field_provenance")
        if not isinstance(prov, dict) or not prov:
            missing.append("field_provenance")
        if missing:
            pudo_issues.append({"episode_id": p.get("episode_id"), "anchor_id": p.get("anchor_id"), "missing": sorted(set(missing))})
        sid = str(p.get("site_id") or "")
        if sid:
            sites.add(sid)
            sites_by_ep[str(p.get("episode_id"))].add(sid)

    endpoint_missing = []
    endpoint_zero = []
    for eid in sorted(eids):
        cov = _graph_endpoint_coverage(root, eid)
        if cov is None:
            endpoint_missing.append(eid)
            continue
        if not cov.get("origin") or not cov.get("destination"):
            endpoint_zero.append(eid)

    issues = []
    if not episodes:
        issues.append("empty_split")
    if manifest.get("paper_mode") is not True or manifest.get("source_policy") != "paper":
        issues.append("not_paper_mode_dataset")
    if request_issues:
        issues.append("counterfactual_request_integrity_failed")
    if pudo_issues:
        issues.append("eligible_pudo_provenance_failed")
    if endpoint_missing or endpoint_zero:
        issues.append("endpoint_pudo_coverage_failed")

    return {
        "split": split,
        "dataset_dir": str(root),
        "episode_ids": sorted(eids),
        "scenario_tokens": sorted(scenario_tokens),
        "log_names": sorted(logs),
        "cities": dict(cities),
        "counts": {
            "episodes": len(episodes), "requests": len(requests), "counterfactual_pairs": len(pairs),
            "paper_eligible_pudos": len(eligible), "unique_paper_sites": len(sites), "excluded_episodes": len(excluded),
        },
        "sites": sorted(sites),
        "sites_by_episode": {k: sorted(v) for k, v in sites_by_ep.items()},
        "counterfactual_issues": request_issues[:100],
        "eligible_pudo_issues": pudo_issues[:100],
        "endpoint_missing": endpoint_missing[:100],
        "endpoint_zero": endpoint_zero[:100],
        "exclusion_by_stage": dict(Counter(str(x.get("stage") or "unknown") for x in excluded)),
        "issues": issues,
    }


def _pair_overlap(a: Iterable[str], b: Iterable[str]) -> List[str]:
    return sorted(set(a) & set(b))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--train_dir", required=True)
    ap.add_argument("--val_dir", required=True)
    ap.add_argument("--test_dir", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--expected_profiles_per_episode", type=int, default=8)
    ap.add_argument("--required_cities", default="boston,pittsburgh,vegas,singapore")
    ap.add_argument("--site_disjoint_test_episodes", default=None, help="Optional output TXT containing test episodes whose paper PUDO site IDs are unseen in train/val.")
    ap.add_argument("--fail_if_not_ready", action="store_true")
    args = ap.parse_args()

    roots = {"train": Path(args.train_dir), "val": Path(args.val_dir), "test": Path(args.test_dir)}
    reports = {k: _audit_one(k, v, args.expected_profiles_per_episode) for k, v in roots.items()}
    hard = []
    warnings = []
    for split, rep in reports.items():
        hard.extend(f"{split}:{x}" for x in rep["issues"])

    overlaps = {}
    for a, b in [("train", "val"), ("train", "test"), ("val", "test")]:
        eo = _pair_overlap(reports[a]["episode_ids"], reports[b]["episode_ids"])
        so = _pair_overlap(reports[a]["scenario_tokens"], reports[b]["scenario_tokens"])
        lo = _pair_overlap(reports[a]["log_names"], reports[b]["log_names"])
        site = _pair_overlap(reports[a]["sites"], reports[b]["sites"])
        overlaps[f"{a}_{b}"] = {
            "episode_ids": eo[:100], "scenario_tokens": so[:100], "log_names": lo[:100],
            "paper_site_ids": site[:100], "paper_site_overlap_count": len(site),
        }
        if eo: hard.append(f"split_episode_overlap:{a}:{b}")
        if so: hard.append(f"split_scenario_token_overlap:{a}:{b}")
        # Log overlap should not occur for official nuPlan DB splits; keep hard.
        if lo: hard.append(f"split_log_overlap:{a}:{b}")
        if site:
            warnings.append(f"physical_pudo_site_reuse:{a}:{b}:{len(site)}")

    required = {x.strip() for x in args.required_cities.replace("+", ",").split(",") if x.strip()}
    for split, rep in reports.items():
        observed = {k for k, v in rep["cities"].items() if v > 0}
        missing = sorted(required - observed)
        if missing:
            hard.append(f"{split}:missing_required_cities:{','.join(missing)}")

    seen_train_val = set(reports["train"]["sites"]) | set(reports["val"]["sites"])
    test_sites_by_ep = {k: set(v) for k, v in reports["test"]["sites_by_episode"].items()}
    site_disjoint_test = sorted(eid for eid, sites in test_sites_by_ep.items() if sites and not (sites & seen_train_val))
    if args.site_disjoint_test_episodes:
        op = Path(args.site_disjoint_test_episodes); op.parent.mkdir(parents=True, exist_ok=True)
        op.write_text("\n".join(site_disjoint_test) + ("\n" if site_disjoint_test else ""), encoding="utf-8")

    report = {
        "status": "PASS" if not hard else "FAIL",
        "ready_for_main_results": not hard,
        "hard_issues": sorted(set(hard)),
        "warnings": warnings,
        "splits": reports,
        "cross_split_overlap": overlaps,
        "site_disjoint_secondary_test": {
            "episode_count": len(site_disjoint_test),
            "episode_ids": site_disjoint_test[:200],
            "definition": "test episode has >=1 paper site and none of its paper site_ids occurs in train or val",
            "output_file": args.site_disjoint_test_episodes,
        },
        "interpretation": (
            "Official traffic-scene leakage (episode/scenario/log) is blocking. Physical PUDO site reuse is reported separately because "
            "nuPlan's official split is not a site-disjoint accessibility split; use the emitted site-disjoint test subset as a stronger secondary generalization check."
        ),
    }
    dump_json(args.output, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"CAPPLAN_BUNDLE_CHECK={'PASS' if not hard else 'FAIL'}")
    if args.fail_if_not_ready and hard:
        raise SystemExit("dataset bundle is not publication-ready: " + ", ".join(sorted(set(hard))))


if __name__ == "__main__":
    main()
