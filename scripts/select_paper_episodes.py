#!/usr/bin/env python
"""Select an auditable paper subset from full nuPlan candidate evidence.

Selection happens *after* full bootstrap/paper evidence construction.  An episode
is admitted only when it has enough distinct paper-eligible PUDO sites, a
sufficient real accessibility graph, and at least two non-proxy entrance nodes.
Optional site-leakage exclusions are applied without rewriting official nuPlan
train/val/test DB splits.
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from capplan.utils.serialization import read_jsonl


def _bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v or "").strip().lower() in {"1", "true", "yes", "y"}


def _xy(row: Dict[str, Any]) -> Tuple[float, float] | None:
    try:
        return float(row["x"]), float(row["y"])
    except Exception:
        pose = row.get("curb_pose") if isinstance(row.get("curb_pose"), dict) else {}
        try:
            return float(pose["x"]), float(pose["y"])
        except Exception:
            return None


def _distinct_site_count(rows: Iterable[Dict[str, Any]], radius_m: float) -> int:
    centers: List[Tuple[float, float]] = []
    anchor_fallback: set[str] = set()
    for row in rows:
        p = _xy(row)
        if p is None:
            aid = str(row.get("anchor_id") or row.get("pudo_id") or "")
            if aid:
                anchor_fallback.add(aid)
            continue
        if not any(math.hypot(p[0] - q[0], p[1] - q[1]) <= radius_m for q in centers):
            centers.append(p)
    return len(centers) + len(anchor_fallback)


def _source_bad(value: Any) -> bool:
    s = str(value or "").strip().lower()
    return (not s) or s.startswith("synthetic") or "proxy" in s or s in {"toy", "mock", "unknown"}



def _spatial_representatives(rows: Iterable[Dict[str, Any]], radius_m: float) -> List[Dict[str, Any]]:
    reps: List[Dict[str, Any]] = []
    for row in rows:
        p = _xy(row)
        if p is None:
            aid = str(row.get("anchor_id") or row.get("pudo_id") or row.get("node_id") or "")
            if aid and not any(str(r.get("anchor_id") or r.get("pudo_id") or r.get("node_id") or "") == aid for r in reps):
                reps.append(row)
            continue
        if not any((_xy(r) is not None and math.hypot(p[0] - _xy(r)[0], p[1] - _xy(r)[1]) <= radius_m) for r in reps):
            reps.append(row)
    return reps


def _reachable_pair_capacity(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]], eligible_rows: List[Dict[str, Any]], trusted_entrance_tokens: List[str], dedup_radius_m: float = 3.0) -> Dict[str, int]:
    """Return physical entrance<->eligible-PUDO connectivity capacity.

    Ride connects the two PUDOs, so origin and destination pedestrian legs may
    live in different graph components.  What matters is that there are at least
    two *distinct* trusted entrance / paper-eligible PUDO pairs connected by the
    pedestrian graph.  Within each weak component the maximum pair count is
    min(number of distinct trusted entrances, number of distinct eligible PUDO
    sites); summing across components yields the service-chain pair capacity.
    """
    by_id = {str(n.get("node_id") or ""): n for n in nodes if n.get("node_id")}
    parent = {nid: nid for nid in by_id}
    rank = {nid: 0 for nid in by_id}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        if a not in parent or b not in parent:
            return
        ra, rb = find(a), find(b)
        if ra == rb:
            return
        if rank[ra] < rank[rb]:
            ra, rb = rb, ra
        parent[rb] = ra
        if rank[ra] == rank[rb]:
            rank[ra] += 1

    for e in edges:
        union(str(e.get("from_node") or ""), str(e.get("to_node") or ""))

    entrance_rows = []
    for n in nodes:
        if str(n.get("kind") or "").lower() not in {"entrance", "origin_entrance", "destination_entrance"}:
            continue
        if _source_bad(n.get("source")):
            continue
        src = str(n.get("source") or "").lower()
        if any(token.lower() in src for token in trusted_entrance_tokens):
            entrance_rows.append(n)
    entrances = _spatial_representatives(entrance_rows, dedup_radius_m)
    pudos = _spatial_representatives(eligible_rows, dedup_radius_m)

    entrance_by_comp: Dict[str, int] = defaultdict(int)
    for n in entrances:
        nid = str(n.get("node_id") or "")
        if nid in parent:
            entrance_by_comp[find(nid)] += 1

    pudo_by_comp: Dict[str, int] = defaultdict(int)
    pudo_unbound = 0
    for r in pudos:
        nid = str(r.get("adjacent_ped_node_id") or "")
        if not nid or nid not in parent:
            # Some inventories bind directly to the curb/curb-ramp graph node.
            alt = str(r.get("anchor_id") or r.get("pudo_id") or "")
            nid = alt if alt in parent else ""
        if nid:
            pudo_by_comp[find(nid)] += 1
        else:
            pudo_unbound += 1

    comps = set(entrance_by_comp) | set(pudo_by_comp)
    capacity = sum(min(entrance_by_comp.get(c, 0), pudo_by_comp.get(c, 0)) for c in comps)
    connected_entrances = sum(entrance_by_comp[c] for c in comps if pudo_by_comp.get(c, 0) > 0)
    connected_pudos = sum(pudo_by_comp[c] for c in comps if entrance_by_comp.get(c, 0) > 0)
    return {
        "reachable_entrance_pudo_pair_capacity": int(capacity),
        "trusted_entrance_sites": len(entrances),
        "eligible_pudo_sites_in_graph": len(pudos),
        "trusted_entrance_sites_connected_to_eligible_pudo": int(connected_entrances),
        "eligible_pudo_sites_connected_to_trusted_entrance": int(connected_pudos),
        "eligible_pudo_sites_without_graph_binding": int(pudo_unbound),
    }


def _graph_quality(graph_dir: Path, eid: str, min_nodes: int, min_edges: int, trusted_entrance_tokens: List[str], eligible_rows: List[Dict[str, Any]]) -> Tuple[bool, str, Dict[str, int]]:
    nf = graph_dir / f"{eid}.nodes.jsonl"
    ef = graph_dir / f"{eid}.edges.jsonl"
    if not nf.exists() or not ef.exists():
        return False, "missing_graph_files", {"nodes": 0, "edges": 0, "real_entrances": 0, "trusted_entrances": 0, "reachable_entrance_pudo_pair_capacity": 0}
    nodes = read_jsonl(nf)
    edges = read_jsonl(ef)
    entrance_kinds = {"entrance", "origin_entrance", "destination_entrance"}
    real_entrances = 0
    trusted_entrances = 0
    for n in nodes:
        if str(n.get("kind") or "").lower() not in entrance_kinds:
            continue
        if _source_bad(n.get("source")):
            continue
        real_entrances += 1
        src = str(n.get("source") or "").lower()
        if any(token.lower() in src for token in trusted_entrance_tokens):
            trusted_entrances += 1
    connectivity = _reachable_pair_capacity(nodes, edges, eligible_rows, trusted_entrance_tokens)
    stats = {"nodes": len(nodes), "edges": len(edges), "real_entrances": real_entrances, "trusted_entrances": trusted_entrances, **connectivity}
    if len(nodes) < min_nodes:
        return False, "graph_nodes_below_threshold", stats
    if len(edges) < min_edges:
        return False, "graph_edges_below_threshold", stats
    if real_entrances < 2:
        return False, "fewer_than_two_real_entrances", stats
    if trusted_entrances < 2:
        return False, "fewer_than_two_audited_or_trusted_entrances", stats
    if int(connectivity.get("reachable_entrance_pudo_pair_capacity", 0)) < 2:
        return False, "fewer_than_two_reachable_trusted_entrance_pudo_pairs", stats
    return True, "ok", stats


def _load_exclusions(path: str | None, split: str) -> set[str]:
    if not path:
        return set()
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    ids = ((payload.get("exclude_episode_ids") or {}).get(split) or []) if isinstance(payload, dict) else []
    return {str(x) for x in ids}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--pudo_evidence_jsonl", required=True)
    p.add_argument("--accessibility_graph_dir", required=True)
    p.add_argument("--city", required=True, choices=["boston", "pittsburgh", "vegas", "singapore"])
    p.add_argument("--split", required=True, choices=["train", "val", "test"])
    p.add_argument("--site_exclusion_json", default=None)
    p.add_argument("--min_paper_eligible_pudos", type=int, default=2)
    p.add_argument("--min_distinct_pudo_sites", type=int, default=2)
    p.add_argument("--pudo_site_dedup_radius_m", type=float, default=3.0)
    p.add_argument("--min_graph_nodes", type=int, default=100)
    p.add_argument("--min_graph_edges", type=int, default=150)
    p.add_argument("--trusted_entrance_source", action="append", default=[], help="Additional source substring accepted as independently verified entrance truth. Manual/reviewed audit sources are always trusted by default.")
    p.add_argument("--output_txt", required=True)
    p.add_argument("--report_json", required=True)
    args = p.parse_args()

    by_episode: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in read_jsonl(args.pudo_evidence_jsonl):
        eid = str(row.get("episode_id") or "")
        if eid:
            by_episode[eid].append(row)
    if not by_episode:
        raise RuntimeError("PUDO evidence contains no episode_id rows")

    graph_dir = Path(args.accessibility_graph_dir)
    excluded_by_site = _load_exclusions(args.site_exclusion_json, args.split)
    trusted_entrance_tokens = ["reviewed_audit:", "manual_audit:"] + list(args.trusted_entrance_source or [])
    reasons: Counter[str] = Counter()
    selected: List[str] = []
    detail: List[Dict[str, Any]] = []
    for eid, rows in sorted(by_episode.items()):
        eligible = [r for r in rows if _bool(r.get("paper_eligible")) and _bool(r.get("legal_stop"))]
        complete_neg = [r for r in rows if _bool(r.get("paper_evidence_complete")) and not _bool(r.get("paper_eligible"))]
        distinct_sites = _distinct_site_count(eligible, args.pudo_site_dedup_radius_m)
        ok_graph, graph_reason, gstats = _graph_quality(graph_dir, eid, args.min_graph_nodes, args.min_graph_edges, trusted_entrance_tokens, eligible)
        episode_reasons: List[str] = []
        if len(eligible) < args.min_paper_eligible_pudos:
            episode_reasons.append("insufficient_paper_eligible_pudos")
        if distinct_sites < args.min_distinct_pudo_sites:
            episode_reasons.append("insufficient_distinct_pudo_sites")
        if not ok_graph:
            episode_reasons.append(graph_reason)
        if eid in excluded_by_site:
            episode_reasons.append("site_leakage_exclusion")
        if episode_reasons:
            reasons.update(episode_reasons)
        else:
            selected.append(eid)
        detail.append({
            "episode_id": eid,
            "selected": not episode_reasons,
            "reasons": episode_reasons,
            "pudo_rows": len(rows),
            "paper_eligible_pudos": len(eligible),
            "paper_evidence_complete_negative_pudos": len(complete_neg),
            "distinct_eligible_pudo_sites": distinct_sites,
            **gstats,
        })

    out = Path(args.output_txt)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(f"{eid}\n" for eid in selected), encoding="utf-8")
    report = {
        "status": "PASS" if selected else "FAIL",
        "city": args.city,
        "split": args.split,
        "episodes_with_pudo_evidence": len(by_episode),
        "selected_paper_episodes": len(selected),
        "selection_rate": len(selected) / max(len(by_episode), 1),
        "rejection_reason_counts": dict(reasons),
        "site_leakage_exclusion_count": len(excluded_by_site),
        "thresholds": {
            "min_paper_eligible_pudos": args.min_paper_eligible_pudos,
            "min_distinct_pudo_sites": args.min_distinct_pudo_sites,
            "pudo_site_dedup_radius_m": args.pudo_site_dedup_radius_m,
            "min_graph_nodes": args.min_graph_nodes,
            "min_graph_edges": args.min_graph_edges,
            "min_real_entrances": 2,
            "min_trusted_entrances": 2,
            "min_reachable_trusted_entrance_pudo_pairs": 2,
            "trusted_entrance_source_tokens": trusted_entrance_tokens,
        },
        "output_txt": str(out),
        "episodes": detail,
        "interpretation": "This is a paper main-set eligibility filter, not a replacement for the official nuPlan split. Rejected episodes remain useful bootstrap/uncertainty negatives.",
    }
    rp = Path(args.report_json)
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "episodes"}, indent=2, sort_keys=True))
    if not selected:
        raise RuntimeError("no paper episodes passed selection; do not run a paper dataset build yet")
    print("PAPER_EPISODE_SELECTION_CHECK=PASS")


if __name__ == "__main__":
    main()
