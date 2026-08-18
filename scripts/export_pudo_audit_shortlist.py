#!/usr/bin/env python
"""Export unresolved PUDO candidates into a compact manual-audit CSV shortlist.

The shortlist deliberately contains only candidate coordinates and missing-evidence
reasons. It does not infer legality, curb height, width, or deployment clearance.
Nearby candidates are de-duplicated so one field/site audit can support multiple
nuPlan episodes when they reference the same curb location.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capplan.data.gis_fusion import CoordinateTransformer
from capplan.utils.serialization import read_jsonl


def _xy(row: Dict[str, Any]) -> Tuple[float, float] | None:
    try:
        return float(row["x"]), float(row["y"])
    except (KeyError, TypeError, ValueError):
        pose = row.get("curb_pose") if isinstance(row.get("curb_pose"), dict) else {}
        try:
            return float(pose["x"]), float(pose["y"])
        except (KeyError, TypeError, ValueError):
            return None


def _priority(row: Dict[str, Any]) -> Tuple[int, int, float]:
    # Prefer candidates already attached to a pedestrian node and public candidate
    # layers; within that group prefer higher map confidence.
    ped = int(bool(row.get("adjacent_ped_node_id")))
    public = int(bool(row.get("candidate_source")))
    try:
        conf = float(row.get("map_confidence") or 0.0)
    except (TypeError, ValueError):
        conf = 0.0
    return ped, public, conf


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--pudo_evidence_jsonl", required=True, action="append", help="Repeat for train/val/test candidate files; all inputs are de-duplicated by physical location.")
    p.add_argument("--georeference_json", required=True)
    p.add_argument("--city", required=True, choices=["boston", "pittsburgh", "vegas", "singapore"])
    p.add_argument("--output_csv", required=True)
    p.add_argument("--max_candidates_per_episode", type=int, default=4)
    p.add_argument("--dedup_radius_m", type=float, default=5.0)
    p.add_argument("--include_legal_negative", action="store_true", help="Also audit evidence-complete candidates that are independently illegal.")
    p.add_argument("--report_json", default=None, help="Optional JSON report path for reproducible build diagnostics.")
    args = p.parse_args()

    rows: List[Dict[str, Any]] = []
    for input_path in args.pudo_evidence_jsonl:
        rows.extend(read_jsonl(input_path))
    transformer = CoordinateTransformer.from_file(args.georeference_json)

    by_episode: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if bool(row.get("paper_eligible")):
            continue
        if bool(row.get("paper_evidence_complete")) and not args.include_legal_negative:
            continue
        xy = _xy(row)
        if xy is None:
            continue
        by_episode[str(row.get("episode_id") or "unknown")].append(row)

    selected: List[Dict[str, Any]] = []
    for episode_id, candidates in sorted(by_episode.items()):
        candidates.sort(key=_priority, reverse=True)
        selected.extend(candidates[: max(1, args.max_candidates_per_episode)])

    # Metric-space de-duplication. Each cluster stores episode IDs/anchor IDs so
    # one observation can be re-used by the downstream spatial matcher.
    clusters: List[Dict[str, Any]] = []
    for row in selected:
        xy = _xy(row)
        assert xy is not None
        x, y = xy
        match = None
        for c in clusters:
            if math.hypot(x - c["x"], y - c["y"]) <= args.dedup_radius_m:
                match = c
                break
        if match is None:
            lon, lat = transformer.local_to_wgs84(x, y)
            match = {
                "x": x, "y": y, "lon": lon, "lat": lat,
                "episodes": set(), "anchors": set(), "statuses": set(),
                "sources": set(), "ped_nodes": set(),
            }
            clusters.append(match)
        match["episodes"].add(str(row.get("episode_id") or ""))
        match["anchors"].add(str(row.get("anchor_id") or row.get("pudo_id") or ""))
        match["statuses"].add(str(row.get("evidence_status") or "candidate_uncertain"))
        if row.get("candidate_source") or row.get("source"):
            match["sources"].add(str(row.get("candidate_source") or row.get("source")))
        if row.get("adjacent_ped_node_id"):
            match["ped_nodes"].add(str(row["adjacent_ped_node_id"]))

    out = Path(args.output_csv)
    out.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "audit_id", "city", "lon", "lat",
        "curb_height_m", "curb_height_m_source", "curb_height_m_tier",
        "sidewalk_width_m", "sidewalk_width_m_source", "sidewalk_width_m_tier",
        "deployment_clearance_m", "deployment_clearance_m_source", "deployment_clearance_m_tier",
        "curb_ramp", "curb_ramp_source", "curb_ramp_tier",
        "running_slope", "running_slope_source", "running_slope_tier",
        "cross_slope", "cross_slope_source", "cross_slope_tier",
        "surface", "surface_source", "surface_tier",
        "permanent_obstruction", "lighting", "shelter",
        "legal_stop", "legal_stop_source", "legal_stop_tier", "legal_basis", "service_class", "time_window",
        "entrance_id", "entrance_lon", "entrance_lat", "entrance_source", "entrance_tier",
        "entrance_access_width_m", "entrance_access_width_m_source", "entrance_access_width_m_tier",
        "entrance_running_slope", "entrance_running_slope_source", "entrance_running_slope_tier",
        "entrance_cross_slope", "entrance_cross_slope_source", "entrance_cross_slope_tier",
        "entrance_surface", "entrance_surface_source", "entrance_surface_tier",
        "entrance_step_free", "entrance_step_free_source", "entrance_step_free_tier",
        "observed_at", "auditor_id", "photo_ref", "notes", "audit_method", "manual_confirmed",
        "protocol_version", "episode_ids", "candidate_anchor_ids", "candidate_sources",
        "evidence_status_before_audit", "adjacent_ped_node_ids", "auto_residual_fields",
    ]
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for idx, c in enumerate(clusters, 1):
            w.writerow({
                "audit_id": f"{args.city.upper()}-AUD-{idx:05d}",
                "city": args.city,
                "lon": f"{c['lon']:.8f}", "lat": f"{c['lat']:.8f}",
                "service_class": "autonomous_mobility",
                "protocol_version": "abilitybench_manual_audit_v2",
                "audit_method": "unresolved_evidence_shortlist",
                "manual_confirmed": "false",
                "episode_ids": ";".join(sorted(x for x in c["episodes"] if x)),
                "candidate_anchor_ids": ";".join(sorted(x for x in c["anchors"] if x)),
                "candidate_sources": ";".join(sorted(c["sources"])),
                "evidence_status_before_audit": ";".join(sorted(c["statuses"])),
                "adjacent_ped_node_ids": ";".join(sorted(c["ped_nodes"])),
                "notes": "FILL measured interface + independent stopping legality; do not copy candidate semantics as ground truth",
            })

    report = {
        "status": "PASS" if clusters else "FAIL",
        "city": args.city,
        "input_paths": list(args.pudo_evidence_jsonl),
        "input_rows": len(rows),
        "unresolved_episodes": len(by_episode),
        "selected_rows_before_dedup": len(selected),
        "audit_sites_after_dedup": len(clusters),
        "output_csv": str(out),
        "interpretation": "PASS means an auditable shortlist was generated; it does not mean the dataset is publication-ready.",
    }
    if args.report_json:
        rp = Path(args.report_json)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"PUDO_AUDIT_SHORTLIST_CHECK={report['status']}")
    if not clusters:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
