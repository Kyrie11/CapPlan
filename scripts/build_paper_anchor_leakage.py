#!/usr/bin/env python
"""Build a split-leakage exclusion manifest on preliminary paper episodes.

The checker operates only on episodes that already pass paper evidence/graph
quality.  It clusters two physical anchor types in the city's local metric CRS:
  * paper-eligible PUDO sites; and
  * independently trusted entrance sites.

It then protects the official evaluation ordering test > val > train without
rewriting nuPlan splits: all preliminary test episodes are retained, val
episodes sharing a physical anchor with retained test episodes are excluded,
and train episodes sharing a physical anchor with retained test/val episodes
are excluded.  Same-split site reuse is reported but is not considered leakage.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Set, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from capplan.utils.serialization import read_jsonl

SPLITS = ("train", "val", "test")
SPLIT_RANK = {"train": 0, "val": 1, "test": 2}
ENTRANCE_KINDS = {"entrance", "origin_entrance", "destination_entrance", "transit_stop"}


def _parse_spec(spec: str, name: str) -> Tuple[str, Path]:
    if "=" not in spec:
        raise argparse.ArgumentTypeError(f"{name} must be split=/path")
    split, value = spec.split("=", 1)
    split = split.strip().lower()
    if split not in SPLIT_RANK:
        raise argparse.ArgumentTypeError(f"unsupported split {split!r}")
    return split, Path(value)


def _read_allowlist(path: Path) -> Set[str]:
    if not path.exists():
        raise FileNotFoundError(path)
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def _bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v or "").strip().lower() in {"1", "true", "yes", "y"}


def _xy(row: Mapping[str, Any]) -> Tuple[float, float] | None:
    for xk, yk in (("x", "y"), ("local_x", "local_y")):
        try:
            return float(row[xk]), float(row[yk])
        except Exception:
            pass
    pose = row.get("pose") if isinstance(row.get("pose"), Mapping) else None
    if pose:
        try:
            return float(pose["x"]), float(pose["y"])
        except Exception:
            pass
    pose = row.get("curb_pose") if isinstance(row.get("curb_pose"), Mapping) else None
    if pose:
        try:
            return float(pose["x"]), float(pose["y"])
        except Exception:
            pass
    return None


def _source_is_trusted(source: Any, tokens: Sequence[str]) -> bool:
    s = str(source or "").strip().lower()
    if not s or s.startswith("synthetic") or "proxy" in s or s in {"unknown", "toy", "mock"}:
        return False
    return any(str(token).lower() in s for token in tokens)


def _cluster_points(points: Iterable[Dict[str, Any]], radius_m: float, anchor_type: str) -> List[Dict[str, Any]]:
    radius = max(float(radius_m), 0.01)
    grid: Dict[Tuple[int, int], List[int]] = defaultdict(list)
    clusters: List[Dict[str, Any]] = []
    for point in points:
        xy = _xy(point)
        if xy is None:
            continue
        x, y = xy
        cx, cy = math.floor(x / radius), math.floor(y / radius)
        match_idx: int | None = None
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for idx in grid.get((cx + dx, cy + dy), []):
                    c = clusters[idx]
                    if math.hypot(x - c["x"], y - c["y"]) <= radius:
                        match_idx = idx
                        break
                if match_idx is not None:
                    break
            if match_idx is not None:
                break
        if match_idx is None:
            match_idx = len(clusters)
            clusters.append({
                "anchor_type": anchor_type,
                "x": x,
                "y": y,
                "episodes": {s: set() for s in SPLITS},
                "sources": set(),
                "anchor_ids": set(),
            })
            grid[(cx, cy)].append(match_idx)
        c = clusters[match_idx]
        split = str(point["_split"])
        eid = str(point["_episode_id"])
        c["episodes"][split].add(eid)
        src = point.get("source") or point.get("candidate_source") or point.get("curb_inventory_source")
        if src:
            c["sources"].add(str(src))
        aid = point.get("anchor_id") or point.get("pudo_id") or point.get("node_id")
        if aid:
            c["anchor_ids"].add(str(aid))
    return clusters


def _episodes_sharing_with_retained(clusters: Sequence[Dict[str, Any]], split: str, retained_higher: Mapping[str, Set[str]]) -> Set[str]:
    excluded: Set[str] = set()
    for c in clusters:
        candidates = set(c["episodes"].get(split, set()))
        if not candidates:
            continue
        conflict = False
        for higher_split, retained_ids in retained_higher.items():
            if set(c["episodes"].get(higher_split, set())) & retained_ids:
                conflict = True
                break
        if conflict:
            excluded.update(candidates)
    return excluded


def _cluster_json(c: Dict[str, Any], idx: int) -> Dict[str, Any]:
    return {
        "cluster_id": f"{c['anchor_type']}-{idx:06d}",
        "anchor_type": c["anchor_type"],
        "x": c["x"],
        "y": c["y"],
        "episode_ids": {s: sorted(c["episodes"].get(s, set())) for s in SPLITS if c["episodes"].get(s)},
        "splits": [s for s in SPLITS if c["episodes"].get(s)],
        "sources": sorted(c["sources"]),
        "anchor_ids": sorted(c["anchor_ids"]),
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--pudo_input", action="append", required=True, help="Repeat: train=/.../pudo/city.jsonl")
    p.add_argument("--graph_input", action="append", required=True, help="Repeat: train=/.../accessibility_graphs")
    p.add_argument("--allowlist", action="append", required=True, help="Repeat: train=/.../train.pre_site.txt")
    p.add_argument("--city", required=True, choices=["boston", "pittsburgh", "vegas", "singapore"])
    p.add_argument("--pudo_radius_m", type=float, default=5.0)
    p.add_argument("--entrance_radius_m", type=float, default=5.0)
    p.add_argument("--trusted_entrance_source", action="append", default=[], help="Additional independently verified entrance-source substring")
    p.add_argument("--output_json", required=True)
    args = p.parse_args()

    pudo_paths = dict(_parse_spec(x, "--pudo_input") for x in args.pudo_input)
    graph_dirs = dict(_parse_spec(x, "--graph_input") for x in args.graph_input)
    allow_paths = dict(_parse_spec(x, "--allowlist") for x in args.allowlist)
    missing = [s for s in SPLITS if s not in pudo_paths or s not in graph_dirs or s not in allow_paths]
    if missing:
        raise RuntimeError(f"train/val/test inputs are all required; missing={missing}")

    preliminary = {s: _read_allowlist(allow_paths[s]) for s in SPLITS}
    for split, ids in preliminary.items():
        if not ids:
            raise RuntimeError(f"preliminary paper allowlist is empty for {args.city}/{split}: {allow_paths[split]}")

    pudo_points: List[Dict[str, Any]] = []
    entrance_points: List[Dict[str, Any]] = []
    trusted_tokens = ["reviewed_audit:", "manual_audit:"] + list(args.trusted_entrance_source or [])
    missing_graphs: List[str] = []

    for split in SPLITS:
        seen_pudo_episodes: Set[str] = set()
        for row in read_jsonl(pudo_paths[split]):
            eid = str(row.get("episode_id") or "")
            if eid not in preliminary[split] or not _bool(row.get("paper_eligible")):
                continue
            rr = dict(row)
            rr["_split"] = split
            rr["_episode_id"] = eid
            if _xy(rr) is not None:
                pudo_points.append(rr)
                seen_pudo_episodes.add(eid)
        missing_pudo = sorted(preliminary[split] - seen_pudo_episodes)
        if missing_pudo:
            raise RuntimeError(f"{args.city}/{split}: preliminary episodes without any paper-eligible PUDO: {missing_pudo[:10]}")

        for eid in sorted(preliminary[split]):
            node_path = graph_dirs[split] / f"{eid}.nodes.jsonl"
            if not node_path.exists():
                missing_graphs.append(str(node_path))
                continue
            for node in read_jsonl(node_path):
                if str(node.get("kind") or "").lower() not in ENTRANCE_KINDS:
                    continue
                if not _source_is_trusted(node.get("source"), trusted_tokens):
                    continue
                rr = dict(node)
                rr["_split"] = split
                rr["_episode_id"] = eid
                if _xy(rr) is not None:
                    entrance_points.append(rr)
    if missing_graphs:
        raise RuntimeError(f"missing graph node files ({len(missing_graphs)}), first={missing_graphs[0]}")

    pudo_clusters = _cluster_points(pudo_points, args.pudo_radius_m, "pudo")
    entrance_clusters = _cluster_points(entrance_points, args.entrance_radius_m, "entrance")
    clusters = pudo_clusters + entrance_clusters

    # Resolve physical-site conflicts by evaluation priority, but only against
    # episodes that will actually remain after higher-priority resolution.
    retained_test = set(preliminary["test"])
    excluded_val = _episodes_sharing_with_retained(clusters, "val", {"test": retained_test})
    retained_val = set(preliminary["val"]) - excluded_val
    excluded_train = _episodes_sharing_with_retained(clusters, "train", {"test": retained_test, "val": retained_val})
    retained_train = set(preliminary["train"]) - excluded_train
    exclusions = {"train": excluded_train, "val": excluded_val, "test": set()}
    retained = {"train": retained_train, "val": retained_val, "test": retained_test}

    # Verify the algorithm rather than assuming the exclusion policy worked.
    residual_conflicts: List[Dict[str, Any]] = []
    for i, c in enumerate(clusters, 1):
        active = {
            s: sorted(set(c["episodes"].get(s, set())) & retained[s])
            for s in SPLITS
        }
        active = {s: ids for s, ids in active.items() if ids}
        if len(active) > 1:
            residual_conflicts.append({"cluster": _cluster_json(c, i), "retained_episode_ids": active})

    cross_split_clusters = [c for c in clusters if sum(bool(c["episodes"].get(s)) for s in SPLITS) > 1]
    zero_retained_splits = [s for s in SPLITS if not retained[s]]
    payload = {
        "status": "PASS" if (not residual_conflicts and not zero_retained_splits) else "FAIL",
        "city": args.city,
        "policy": "paper_eligible_physical_anchor_disjoint_keep_test_then_val_then_train",
        "trusted_entrance_source_tokens": trusted_tokens,
        "radii_m": {"pudo": args.pudo_radius_m, "entrance": args.entrance_radius_m},
        "preliminary_episode_counts": {s: len(preliminary[s]) for s in SPLITS},
        "point_counts": {"paper_eligible_pudo": len(pudo_points), "trusted_entrance": len(entrance_points)},
        "cluster_counts": {
            "pudo": len(pudo_clusters),
            "entrance": len(entrance_clusters),
            "cross_split_any_anchor": len(cross_split_clusters),
        },
        "exclude_episode_ids": {s: sorted(exclusions[s]) for s in SPLITS},
        "excluded_episode_counts": {s: len(exclusions[s]) for s in SPLITS},
        "retained_episode_counts": {s: len(retained[s]) for s in SPLITS},
        "residual_cross_split_conflicts": residual_conflicts,
        "zero_retained_splits": zero_retained_splits,
        "cross_split_clusters": [_cluster_json(c, i) for i, c in enumerate(cross_split_clusters, 1)],
        "interpretation": "Physical-site leakage is computed only on preliminary paper-eligible episodes. Official nuPlan DB split membership is unchanged; the exclusions apply only to the paper main subset.",
    }
    out = Path(args.output_json)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in payload.items() if k not in {"cross_split_clusters", "residual_cross_split_conflicts"}}, indent=2, sort_keys=True))
    if residual_conflicts:
        raise RuntimeError("site-disjoint resolution left residual train/val/test anchor leakage")
    for split in SPLITS:
        if not retained[split]:
            raise RuntimeError(f"site-disjoint policy leaves zero {split} paper episodes for {args.city}; acquire more independent audited sites")
    print("PAPER_ANCHOR_LEAKAGE_CHECK=PASS")


if __name__ == "__main__":
    main()
