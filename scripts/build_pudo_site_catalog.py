#!/usr/bin/env python
"""Build one spatially de-duplicated PUDO audit catalog across train/val/test.

Unlike ``export_pudo_audit_shortlist.py`` (which was designed for a small pilot),
this utility accepts multiple split inputs and preserves the split/episode reuse
of every physical site.  It is intended for the two-pass paper build:

1) build bootstrap PUDO candidates for all desired splits;
2) make this site catalog, audit/verify unique sites once;
3) rebuild the paper-eligible subset with the resulting evidence.

The report also exposes candidate-site overlap across official nuPlan splits so a
paper can create a stricter site-disjoint evaluation subset instead of checking
only episode-ID overlap.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from capplan.data.gis_fusion import CoordinateTransformer
from capplan.utils.serialization import iter_jsonl

SPLIT_PRIORITY = {"train": 0, "val": 1, "test": 2}


def _xy(row: Dict[str, Any]) -> Tuple[float, float] | None:
    try:
        return float(row["x"]), float(row["y"])
    except Exception:
        pose = row.get("curb_pose") if isinstance(row.get("curb_pose"), dict) else {}
        try:
            return float(pose["x"]), float(pose["y"])
        except Exception:
            return None


def _priority(row: Dict[str, Any]) -> Tuple[int, int, int, float, float]:
    # Prefer candidates with an actual pedestrian binding and explicit/public
    # curb semantics. Generic fallback sidewalk vertices remain available but
    # do not crowd out curb-ramp or independently sourced candidate locations.
    ped = int(bool(row.get("adjacent_ped_node_id")))
    selection = str(row.get("candidate_selection") or "").lower()
    node_kind = str(row.get("candidate_node_kind") or "").lower()
    semantic = 3 if selection == "external" or bool(row.get("candidate_only")) else 2 if selection == "explicit" or node_kind in {"curb", "curb_ramp"} else 0
    complete_negative = int(bool(row.get("paper_evidence_complete")) and not bool(row.get("paper_eligible")))
    try:
        route_distance = float(row.get("candidate_route_distance_m"))
    except Exception:
        route_distance = float("inf")
    try:
        conf = float(row.get("map_confidence") or 0.0)
    except Exception:
        conf = 0.0
    # reverse=True below: smaller route distance is better, hence negative.
    return ped, semantic, complete_negative, -route_distance, conf


def _parse_input(spec: str) -> Tuple[str, Path]:
    if "=" not in spec:
        raise argparse.ArgumentTypeError("--input must be split=/path/to/pudo.jsonl")
    split, path = spec.split("=", 1)
    split = split.strip().lower()
    if split not in SPLIT_PRIORITY:
        raise argparse.ArgumentTypeError(f"unsupported split {split!r}")
    return split, Path(path)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", action="append", required=True, help="Repeat as train=..., val=..., test=...")
    p.add_argument("--georeference_json", required=True)
    p.add_argument("--city", required=True, choices=["boston", "pittsburgh", "vegas", "singapore"])
    p.add_argument("--output_csv", required=True)
    p.add_argument("--report_json", required=True)
    p.add_argument("--split_exclusion_json", default=None, help="Optional recommended exclusion manifest; test > val > train when a site spans splits.")
    p.add_argument("--max_candidates_per_episode", type=int, default=4)
    p.add_argument("--dedup_radius_m", type=float, default=5.0)
    p.add_argument("--include_paper_eligible", action="store_true", help="Include already paper-eligible sites for complete site leakage accounting.")
    args = p.parse_args()

    transformer = CoordinateTransformer.from_file(args.georeference_json)
    inputs = [_parse_input(x) for x in args.input]
    by_split_episode: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    input_counts: Dict[str, int] = defaultdict(int)
    for split, path in inputs:
        if not path.exists():
            raise FileNotFoundError(path)
        for row in iter_jsonl(path):
            input_counts[split] += 1
            if bool(row.get("paper_eligible")) and not args.include_paper_eligible:
                continue
            xy = _xy(row)
            if xy is None:
                continue
            eid = str(row.get("episode_id") or "")
            if not eid:
                continue
            rr = dict(row); rr["_split"] = split
            by_split_episode[(split, eid)].append(rr)

    selected: List[Dict[str, Any]] = []
    for key, rows in sorted(by_split_episode.items()):
        rows.sort(key=_priority, reverse=True)
        selected.extend(rows[:max(1, args.max_candidates_per_episode)])

    clusters: List[Dict[str, Any]] = []
    # Spatial hashing keeps full four-city catalogs O(N) on average.  The old
    # all-pairs scan became quadratic once every train/val/test episode was
    # included, which made the supposedly "full" audit workflow impractical.
    cell_size = max(float(args.dedup_radius_m), 0.01)
    grid: Dict[Tuple[int, int], List[int]] = defaultdict(list)
    for row in selected:
        x, y = _xy(row) or (None, None)
        if x is None:
            continue
        cx, cy = math.floor(x / cell_size), math.floor(y / cell_size)
        match = None
        # All rows in this catalog belong to one city/local CRS, so local metric
        # de-duplication is exact enough and avoids lat/lon approximation error.
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for idx in grid.get((cx + dx, cy + dy), []):
                    c = clusters[idx]
                    if math.hypot(x - c["x"], y - c["y"]) <= args.dedup_radius_m:
                        match = c
                        break
                if match is not None:
                    break
            if match is not None:
                break
        if match is None:
            lon, lat = transformer.local_to_wgs84(x, y)
            match = {
                "x": x, "y": y, "lon": lon, "lat": lat,
                "episodes": defaultdict(set), "anchors": defaultdict(set),
                "sources": set(), "ped_nodes": set(), "statuses": set(),
            }
            clusters.append(match)
            grid[(cx, cy)].append(len(clusters) - 1)
        split = row["_split"]
        eid = str(row.get("episode_id") or "")
        aid = str(row.get("anchor_id") or row.get("pudo_id") or "")
        match["episodes"][split].add(eid)
        if aid: match["anchors"][split].add(aid)
        if row.get("candidate_source") or row.get("source"):
            match["sources"].add(str(row.get("candidate_source") or row.get("source")))
        if row.get("adjacent_ped_node_id"):
            match["ped_nodes"].add(str(row["adjacent_ped_node_id"]))
        match["statuses"].add(str(row.get("evidence_status") or "candidate_uncertain"))

    fields = [
        "audit_id", "city", "lon", "lat", "curb_height_m", "sidewalk_width_m", "deployment_clearance_m",
        "curb_ramp", "running_slope", "cross_slope", "surface", "legal_stop", "legal_basis", "service_class",
        "time_window", "entrance_id", "entrance_lon", "entrance_lat", "observed_at", "auditor_id", "photo_ref",
        "notes", "protocol_version", "split_membership", "cross_split_site", "episode_ids_train", "episode_ids_val",
        "episode_ids_test", "candidate_anchor_ids_train", "candidate_anchor_ids_val", "candidate_anchor_ids_test",
        "candidate_sources", "evidence_status_before_audit", "adjacent_ped_node_ids",
    ]
    out = Path(args.output_csv); out.parent.mkdir(parents=True, exist_ok=True)
    cross_split = 0
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader()
        for idx, c in enumerate(clusters, 1):
            splits = sorted([s for s in SPLIT_PRIORITY if c["episodes"].get(s)], key=lambda s: SPLIT_PRIORITY[s])
            is_cross = len(splits) > 1; cross_split += int(is_cross)
            w.writerow({
                "audit_id": f"{args.city.upper()}-SITE-{idx:06d}", "city": args.city,
                "lon": f"{c['lon']:.8f}", "lat": f"{c['lat']:.8f}", "service_class": "autonomous_mobility",
                "protocol_version": "abilitybench_site_audit_v2", "split_membership": ";".join(splits),
                "cross_split_site": str(is_cross).lower(),
                **{f"episode_ids_{s}": ";".join(sorted(c["episodes"].get(s, set()))) for s in SPLIT_PRIORITY},
                **{f"candidate_anchor_ids_{s}": ";".join(sorted(c["anchors"].get(s, set()))) for s in SPLIT_PRIORITY},
                "candidate_sources": ";".join(sorted(c["sources"])),
                "evidence_status_before_audit": ";".join(sorted(c["statuses"])),
                "adjacent_ped_node_ids": ";".join(sorted(c["ped_nodes"])),
                "notes": "Verify physical interface + independent stopping legality + true entrance; candidate semantics are not ground truth",
            })

    exclusions: Dict[str, set[str]] = {s: set() for s in SPLIT_PRIORITY}
    leakage_clusters = []
    for idx, c in enumerate(clusters, 1):
        splits = [s for s in SPLIT_PRIORITY if c["episodes"].get(s)]
        if len(splits) <= 1:
            continue
        keep = max(splits, key=lambda s: SPLIT_PRIORITY[s])  # protect test, then val
        excluded = {}
        for s in splits:
            if s == keep:
                continue
            exclusions[s].update(c["episodes"][s]); excluded[s] = sorted(c["episodes"][s])
        leakage_clusters.append({
            "site_id": f"{args.city.upper()}-SITE-{idx:06d}", "splits": splits, "keep_split": keep,
            "excluded_episode_ids": excluded, "lon": c["lon"], "lat": c["lat"],
        })

    report = {
        "status": "PASS",
        "city": args.city,
        "input_rows_by_split": dict(input_counts),
        "episodes_with_candidates_by_split": {s: len({eid for (sp, eid) in by_split_episode if sp == s}) for s in SPLIT_PRIORITY},
        "selected_rows_before_dedup": len(selected),
        "unique_sites": len(clusters),
        "cross_split_site_clusters": cross_split,
        "cross_split_site_rate": cross_split / max(len(clusters), 1),
        "recommended_excluded_episode_counts": {s: len(v) for s, v in exclusions.items()},
        "dedup_radius_m": args.dedup_radius_m,
        "output_csv": str(out),
        "interpretation": "Cross-split site rate is a leakage diagnostic. The exclusion manifest protects test > val > train but should be applied only to the paper site-disjoint subset, not by rewriting official nuPlan DB splits.",
    }
    rp = Path(args.report_json); rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.split_exclusion_json:
        ep = Path(args.split_exclusion_json); ep.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "policy": "site_disjoint_keep_test_then_val_then_train",
            "city": args.city, "dedup_radius_m": args.dedup_radius_m,
            "exclude_episode_ids": {s: sorted(v) for s, v in exclusions.items()},
            "leakage_clusters": leakage_clusters,
        }
        ep.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    print("PUDO_SITE_CATALOG_CHECK=PASS")


if __name__ == "__main__":
    main()
