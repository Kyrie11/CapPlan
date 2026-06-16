#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capplan.data.schemas import AccessibilityEdge, AccessibilityGraph, AccessibilityNode, Pose2D, edge_from_dict, graph_from_records, node_from_dict, to_dict
from capplan.utils.serialization import dump_json, read_jsonl, write_jsonl


def _read_json_records(path: str | None) -> List[Dict[str, Any]]:
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    if p.suffix.lower() == ".json":
        payload = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            for key in ["features", "nodes", "edges", "records"]:
                if isinstance(payload.get(key), list):
                    return [dict(x) for x in payload[key]]
            return [payload]
        return [dict(x) for x in payload]
    return read_jsonl(p)


def _norm_source(row: Dict[str, Any], fallback: str) -> str:
    return str(row.get("source") or row.get("evidence_source") or row.get("data_source") or fallback)


def _node(row: Dict[str, Any], fallback_source: str) -> AccessibilityNode:
    if "node_id" not in row:
        if "id" in row:
            row = {**row, "node_id": row["id"]}
        else:
            raise ValueError(f"accessibility node missing node_id/id: {row}")
    if "x" not in row or "y" not in row:
        coords = row.get("coordinates") or row.get("geometry")
        if isinstance(coords, list) and coords and isinstance(coords[0], (int, float)):
            row = {**row, "x": coords[0], "y": coords[1]}
        else:
            raise ValueError(f"accessibility node missing x/y: {row.get('node_id')}")
    kind = row.get("kind") or row.get("node_type") or "sidewalk_vertex"
    if kind in {"origin_entrance", "destination_entrance"}:
        kind = "entrance"
    return AccessibilityNode(
        node_id=str(row["node_id"]),
        x=float(row["x"]),
        y=float(row["y"]),
        kind=str(kind),
        confidence=float(row.get("confidence", row.get("map_confidence", 1.0))),
        timestamp_s=row.get("timestamp_s"),
        source=_norm_source(row, fallback_source),
        pose=Pose2D(float(row["x"]), float(row["y"]), float(row.get("heading", 0.0)), str(row.get("frame", "map"))),
    )


def _edge(row: Dict[str, Any], fallback_source: str, nodes: Dict[str, AccessibilityNode]) -> AccessibilityEdge:
    if "edge_id" not in row:
        if "id" in row:
            row = {**row, "edge_id": row["id"]}
        elif row.get("from_node") and row.get("to_node"):
            row = {**row, "edge_id": f"{row['from_node']}_to_{row['to_node']}"}
        else:
            raise ValueError(f"accessibility edge missing edge_id/id: {row}")
    frm = str(row.get("from_node") or row.get("u") or row.get("from"))
    to = str(row.get("to_node") or row.get("v") or row.get("to"))
    if frm not in nodes or to not in nodes:
        raise ValueError(f"accessibility edge {row.get('edge_id')} references missing nodes {frm}->{to}")
    geom = row.get("geometry") or [[nodes[frm].x, nodes[frm].y], [nodes[to].x, nodes[to].y]]
    if row.get("length_m") is None:
        length = 0.0
        for a, b in zip(geom[:-1], geom[1:]):
            length += math.hypot(float(b[0]) - float(a[0]), float(b[1]) - float(a[1]))
    else:
        length = float(row["length_m"])
    return AccessibilityEdge(
        edge_id=str(row["edge_id"]),
        from_node=frm,
        to_node=to,
        length_m=max(0.001, float(length)),
        width_m=row.get("width_m", row.get("sidewalk_width_m")),
        slope=row.get("slope", row.get("running_slope")),
        cross_slope=row.get("cross_slope"),
        surface=row.get("surface"),
        curb_ramp=row.get("curb_ramp"),
        step_free=row.get("step_free"),
        obstacle=bool(row.get("obstacle", row.get("obstacle_state") == "blocked")),
        lighting=row.get("lighting"),
        shelter=row.get("shelter"),
        confidence=float(row.get("confidence", row.get("map_confidence", 1.0))),
        geometry=geom,
        crossing_type=row.get("crossing_type", row.get("edge_type")),
        obstacle_state=row.get("obstacle_state"),
        timestamp_s=row.get("timestamp_s"),
        source=_norm_source(row, fallback_source),
    )


def _reject_synthetic(records: Iterable[Dict[str, Any]], label: str) -> None:
    bad = []
    for r in records:
        src = str(r.get("source") or r.get("evidence_source") or "").lower()
        if src.startswith("synthetic") or "proxy" in src or src in {"toy", "mock"}:
            bad.append((r.get("node_id") or r.get("edge_id") or r.get("id"), src))
    if bad:
        raise RuntimeError(f"--fail_on_synthetic rejects {label}; first examples: {bad[:5]}")


def build_graphs(args: argparse.Namespace) -> Dict[str, Any]:
    node_rows = _read_json_records(args.nodes_jsonl or args.osm_nodes_jsonl)
    edge_rows = _read_json_records(args.edges_jsonl or args.osm_edges_jsonl)
    if not node_rows or not edge_rows:
        raise RuntimeError("real accessibility graph build requires explicit node and edge records; no synthetic fallback is available")
    if args.fail_on_synthetic:
        _reject_synthetic(node_rows, "nodes")
        _reject_synthetic(edge_rows, "edges")
    nodes = [_node(r, args.source_name) for r in node_rows]
    by_id = {n.node_id: n for n in nodes}
    edges = [_edge(r, args.source_name, by_id) for r in edge_rows]
    if len(nodes) < args.min_nodes_per_episode or len(edges) < args.min_edges_per_episode:
        raise RuntimeError(f"accessibility graph too small: {len(nodes)} nodes/{len(edges)} edges; required {args.min_nodes_per_episode}/{args.min_edges_per_episode}")
    episode_ids = [x.strip() for x in (args.episode_ids or "shared").replace(",", "+").split("+") if x.strip()]
    out = Path(args.output_graph_dir)
    out.mkdir(parents=True, exist_ok=True)
    for eid in episode_ids:
        graph = AccessibilityGraph(eid, nodes, edges, {"source": args.source_name, "builder": "build_accessibility_graphs", "episode_radius_m": args.episode_radius_m, "snap_tolerance_m": args.snap_tolerance_m})
        write_jsonl(out / f"{eid}.nodes.jsonl", [to_dict(n) for n in graph.nodes])
        write_jsonl(out / f"{eid}.edges.jsonl", [to_dict(e) for e in graph.edges])
        write_jsonl(out / f"{eid}.jsonl", [to_dict(graph)])
    report = {"episodes": episode_ids, "nodes": len(nodes), "edges": len(edges), "source": args.source_name, "synthetic_rejected": bool(args.fail_on_synthetic)}
    dump_json(out / "source_report.json", report)
    dump_json(out / "quality_report.json", {"nodes_per_episode": len(nodes), "edges_per_episode": len(edges), "min_nodes_per_episode": args.min_nodes_per_episode, "min_edges_per_episode": args.min_edges_per_episode})
    return report


def main() -> None:
    p = argparse.ArgumentParser(description="Build prepared_jsonl accessibility graphs from real/prepared pedestrian and curbside layers.")
    p.add_argument("--scene_dataset_dir", default=None, help="Optional scene metadata directory; episode IDs may also be supplied with --episode_ids.")
    p.add_argument("--nuplan_map_root", default=None)
    p.add_argument("--nuplan_map_version", default=None)
    p.add_argument("--osm_source", default=None)
    p.add_argument("--city_gis_dir", default=None)
    p.add_argument("--elevation_source", default=None)
    p.add_argument("--nodes_jsonl", default=None, help="Prepared real accessibility nodes JSONL/JSON.")
    p.add_argument("--edges_jsonl", default=None, help="Prepared real accessibility edges JSONL/JSON.")
    p.add_argument("--osm_nodes_jsonl", default=None, help="Alias for prepared OSM/OpenSidewalks node records.")
    p.add_argument("--osm_edges_jsonl", default=None, help="Alias for prepared OSM/OpenSidewalks edge records.")
    p.add_argument("--output_graph_dir", required=True)
    p.add_argument("--episode_ids", default="shared")
    p.add_argument("--episode_radius_m", type=float, default=800.0)
    p.add_argument("--snap_tolerance_m", type=float, default=3.0)
    p.add_argument("--min_nodes_per_episode", type=int, default=100)
    p.add_argument("--min_edges_per_episode", type=int, default=150)
    p.add_argument("--source_name", default="opensidewalks_city_gis_prepared")
    p.add_argument("--fail_on_synthetic", action="store_true")
    p.add_argument("--num_workers", type=int, default=0)
    args = p.parse_args()
    print(json.dumps(build_graphs(args), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
