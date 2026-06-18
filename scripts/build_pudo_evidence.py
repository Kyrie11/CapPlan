#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capplan.data.gis_fusion import distance_to_polyline, nearest_route_side, read_scene_contexts
from capplan.data.schemas import AccessibilityEdge, AccessibilityGraph, AccessibilityNode, edge_from_dict, node_from_dict
from capplan.utils.serialization import dump_json, read_jsonl, write_jsonl

CORE = ["curb_height_m", "deployment_clearance_m", "sidewalk_width_m"]


def _read(path: str | None) -> List[Dict[str, Any]]:
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    if p.is_dir():
        rows: List[Dict[str, Any]] = []
        for child in sorted(p.glob("*")):
            if child.suffix.lower() in {".json", ".jsonl", ".geojson", ".csv"}:
                rows.extend(_read(str(child)))
        return rows
    if p.suffix.lower() == ".json":
        payload = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            for key in ["pudo_evidence", "candidates", "records", "features", "curbs", "regulations"]:
                if isinstance(payload.get(key), list):
                    return [dict(x) for x in payload[key]]
            if payload.get("type") == "FeatureCollection" and isinstance(payload.get("features"), list):
                return [dict(x) for x in payload["features"]]
            return [payload]
        return [dict(x) for x in payload]
    if p.suffix.lower() == ".geojson":
        payload = json.loads(p.read_text(encoding="utf-8"))
        return [dict(x) for x in payload.get("features", [])]
    if p.suffix.lower() == ".csv":
        import csv
        with p.open("r", encoding="utf-8", newline="") as f:
            return [dict(x) for x in csv.DictReader(f)]
    return read_jsonl(p)


def _source_bad(src: Any) -> bool:
    s = str(src or "").lower()
    return s.startswith("synthetic") or "proxy" in s or s in {"toy", "mock"}


def _as_float(v: Any) -> Optional[float]:
    if v in (None, "", "unknown", "n/a"):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().lower()
    if s.endswith("%"):
        try:
            return float(s[:-1]) / 100.0
        except ValueError:
            return None
    try:
        return float(s.replace("m", ""))
    except ValueError:
        return None


def _bool(v: Any, default: bool = False) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in {"yes", "true", "1", "allowed", "legal", "pickup", "dropoff", "loading", "passenger_loading"}:
        return True
    if s in {"no", "false", "0", "forbidden", "illegal", "tow_away", "no_stopping", "no_standing", "bus_only", "blocked"}:
        return False
    return default


def _xy_from_row(row: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    props = row.get("properties") if isinstance(row.get("properties"), dict) else {}
    d = {**props, **{k: v for k, v in row.items() if k not in {"properties", "geometry", "type"}}}
    geom = row.get("geometry")
    if isinstance(geom, dict):
        coords = geom.get("coordinates")
        if isinstance(coords, list) and len(coords) >= 2 and isinstance(coords[0], (int, float)):
            return float(coords[0]), float(coords[1])
        if isinstance(coords, list) and coords and isinstance(coords[0], list):
            p = coords[0]
            if len(p) >= 2:
                return float(p[0]), float(p[1])
    if isinstance(d.get("curb_pose"), dict):
        return float(d["curb_pose"].get("x", 0.0)), float(d["curb_pose"].get("y", 0.0))
    if d.get("x") is not None and d.get("y") is not None:
        return float(d["x"]), float(d["y"])
    if d.get("curb_x") is not None and d.get("curb_y") is not None:
        return float(d["curb_x"]), float(d["curb_y"])
    return None


def normalize(row: Dict[str, Any], default_source: str) -> Dict[str, Any]:
    props = row.get("properties") if isinstance(row.get("properties"), dict) else {}
    row = {**props, **{k: v for k, v in row.items() if k not in {"properties", "type"}}}
    anchor_id = row.get("anchor_id") or row.get("pudo_id") or row.get("id") or row.get("feature_id")
    if not anchor_id:
        raise ValueError(f"PUDO evidence row missing anchor_id/pudo_id/id: {row}")
    if not row.get("episode_id"):
        raise ValueError(f"PUDO evidence row missing episode_id: {anchor_id}")
    source = row.get("source") or row.get("evidence_source") or default_source
    if _source_bad(source):
        raise ValueError(f"PUDO evidence rejects synthetic/proxy source for {anchor_id}: {source}")
    xy = _xy_from_row(row)
    out = dict(row)
    out["anchor_id"] = str(anchor_id)
    out["pudo_id"] = str(anchor_id)
    out["episode_id"] = str(row["episode_id"])
    out["source"] = str(source)
    if xy:
        out.setdefault("curb_pose", {"x": xy[0], "y": xy[1], "heading": float(row.get("heading", 0.0) or 0.0), "frame": row.get("frame", "map")})
        out.setdefault("stop_pose", out["curb_pose"])
        out.setdefault("x", xy[0])
        out.setdefault("y", xy[1])
    out.setdefault("legal_stop", _bool(row.get("legal_stop", row.get("vehicle_stop_feasible", row.get("regulation", None))), False))
    out.setdefault("legal_stop_source", row.get("legal_stop_source") or row.get("regulation_id") or row.get("curb_regulation_source") or source)
    out.setdefault("side", row.get("side", "unknown"))
    if "availability" in row and "dynamic_confidence" not in out:
        out["dynamic_confidence"] = max(0.0, min(1.0, float(row["availability"])))
    if "curb_occupancy" in row and "blockage_risk" not in out:
        out["blockage_risk"] = max(0.0, min(1.0, float(row["curb_occupancy"])))
    out.setdefault("blockage_risk", 0.0)
    out.setdefault("map_confidence", row.get("confidence", 1.0))
    out.setdefault("dynamic_confidence", 1.0 - float(out.get("blockage_risk", 0.0)))
    for k in ["curb_height_m", "deployment_clearance_m", "sidewalk_width_m", "blockage_risk", "map_confidence", "dynamic_confidence"]:
        if out.get(k) is not None:
            out[k] = float(out[k])
    return out


def _load_graph(graph_dir: Path, episode_id: str) -> AccessibilityGraph:
    node_file = graph_dir / f"{episode_id}.nodes.jsonl"
    edge_file = graph_dir / f"{episode_id}.edges.jsonl"
    if not node_file.exists() or not edge_file.exists():
        node_file = graph_dir / "nodes.jsonl"
        edge_file = graph_dir / "edges.jsonl"
    if not node_file.exists() or not edge_file.exists():
        raise FileNotFoundError(f"missing accessibility graph files for {episode_id} in {graph_dir}")
    nodes = [node_from_dict(x) for x in read_jsonl(node_file)]
    edges = [edge_from_dict(x) for x in read_jsonl(edge_file)]
    meta = {}
    graph_file = graph_dir / f"{episode_id}.jsonl"
    if graph_file.exists():
        rows = read_jsonl(graph_file)
        if rows:
            meta = rows[0].get("metadata", {})
    return AccessibilityGraph(episode_id, nodes, edges, meta)


def _nearest_node(x: float, y: float, nodes: Iterable[AccessibilityNode], kinds: set[str] | None = None) -> tuple[Optional[AccessibilityNode], float]:
    best, best_d = None, float("inf")
    for n in nodes:
        if kinds and n.kind not in kinds:
            continue
        d = math.hypot(x - n.x, y - n.y)
        if d < best_d:
            best, best_d = n, d
    return best, best_d


def _nearest_edge_attrs(x: float, y: float, graph: AccessibilityGraph) -> Dict[str, Any]:
    by_id = {n.node_id: n for n in graph.nodes}
    best_e, best_d = None, float("inf")
    for e in graph.edges:
        if e.from_node not in by_id or e.to_node not in by_id:
            continue
        a, b = by_id[e.from_node], by_id[e.to_node]
        # point-segment distance inline
        vx, vy = b.x - a.x, b.y - a.y
        wx, wy = x - a.x, y - a.y
        den = vx * vx + vy * vy
        t = max(0.0, min(1.0, (wx * vx + wy * vy) / den)) if den > 0 else 0.0
        d = math.hypot(x - (a.x + t * vx), y - (a.y + t * vy))
        if d < best_d:
            best_e, best_d = e, d
    if best_e is None:
        return {}
    return {"sidewalk_width_m": best_e.width_m, "lighting": best_e.lighting, "shelter": best_e.shelter, "surface": best_e.surface, "distance_to_ped_edge_m": best_d}


def _regulation_match(x: float, y: float, regs: List[Dict[str, Any]], tolerance: float) -> Optional[Dict[str, Any]]:
    best, best_d = None, float("inf")
    for r in regs:
        xy = _xy_from_row(r)
        if not xy:
            continue
        d = math.hypot(x - xy[0], y - xy[1])
        if d < best_d:
            best, best_d = r, d
    return best if best is not None and best_d <= tolerance else None


def _blockage_from_agents(x: float, y: float, scene: Dict[str, Any], radius: float = 6.0) -> float:
    count = 0
    for step in scene.get("agent_history", []) or []:
        for obj in step.get("objects", []) or []:
            try:
                d = math.hypot(float(obj.get("x")) - x, float(obj.get("y")) - y)
            except Exception:
                continue
            if d <= radius:
                count += 1
    return min(0.95, count / 10.0)


def _candidate_nodes(graph: AccessibilityGraph, route: List[List[float]], radius: float) -> List[AccessibilityNode]:
    meta_attrs = graph.metadata.get("node_attributes", {}) if isinstance(graph.metadata, dict) else {}
    out: List[AccessibilityNode] = []
    for n in graph.nodes:
        attrs = meta_attrs.get(n.node_id, {}) if isinstance(meta_attrs, dict) else {}
        route_dist = distance_to_polyline([n.x, n.y], route) if route else 0.0
        if n.kind in {"curb", "curb_ramp"} or attrs.get("pudo_connector_candidate"):
            if not route or route_dist <= radius:
                out.append(n)
    if not out:
        # Conservative fallback within this generator: use entrance/sidewalk nodes near route as
        # *candidates* but legal_stop remains false unless regulation evidence matches.
        for n in graph.nodes:
            if n.kind in {"sidewalk", "crossing", "entrance"} and (not route or distance_to_polyline([n.x, n.y], route) <= radius):
                out.append(n)
    return out


def _build_from_graphs(args: argparse.Namespace, normalized_input_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    graph_dir = Path(args.accessibility_graph_dir)
    contexts = read_scene_contexts(args.scene_dataset_dir, [], args.candidate_radius_m)
    scenes = {c.episode_id: c for c in contexts}
    out: List[Dict[str, Any]] = list(normalized_input_rows)
    existing = {(r.get("episode_id"), r.get("anchor_id")) for r in out}
    regs = _read(args.curb_regulation_jsonl) + _read(args.curb_regulation_dir)
    inventory = [normalize(r, args.source_name) for r in _read(args.curb_inventory_jsonl) if r.get("episode_id")]
    for r in inventory:
        key = (r.get("episode_id"), r.get("anchor_id"))
        if key not in existing:
            out.append(r); existing.add(key)

    episode_ids = list(scenes) or sorted({str(r.get("episode_id")) for r in out if r.get("episode_id")})
    if not episode_ids:
        # infer from graph files
        episode_ids = sorted({p.name.split(".nodes.jsonl")[0] for p in graph_dir.glob("*.nodes.jsonl")})
    for eid in episode_ids:
        graph = _load_graph(graph_dir, eid)
        scene = scenes.get(eid)
        route = scene.route_polyline if scene else []
        for idx, n in enumerate(_candidate_nodes(graph, route, args.candidate_radius_m)):
            anchor_id = f"{eid}:pudo_{idx:04d}"
            if (eid, anchor_id) in existing:
                continue
            attrs = _nearest_edge_attrs(n.x, n.y, graph)
            meta_attrs = graph.metadata.get("node_attributes", {}) if isinstance(graph.metadata, dict) else {}
            nattrs = meta_attrs.get(n.node_id, {}) if isinstance(meta_attrs, dict) else {}
            reg = _regulation_match(n.x, n.y, regs, args.regulation_snap_tolerance_m)
            legal = _bool((reg or {}).get("legal_stop", (reg or {}).get("stopping_allowed", (reg or {}).get("regulation"))), False)
            nearest_ped, _ = _nearest_node(n.x, n.y, graph.nodes, {"sidewalk", "crossing", "entrance"})
            blockage = _blockage_from_agents(n.x, n.y, scene.metadata if scene else {})
            width = nattrs.get("width_m") or attrs.get("sidewalk_width_m")
            clearance = nattrs.get("deployment_clearance_m")
            row = {
                "anchor_id": anchor_id,
                "pudo_id": anchor_id,
                "episode_id": eid,
                "kind": "pickup_dropoff",
                "curb_pose": {"x": n.x, "y": n.y, "heading": 0.0, "frame": "map"},
                "stop_pose": {"x": n.x, "y": n.y, "heading": 0.0, "frame": "map"},
                "x": n.x,
                "y": n.y,
                "side": str(nattrs.get("route_side") or (nearest_route_side([n.x, n.y], route) if route else "unknown")),
                "legal_stop": legal,
                "legal_stop_source": str((reg or {}).get("source") or (reg or {}).get("regulation_id") or "no_matching_regulation_fail_closed"),
                "adjacent_ped_node_id": nearest_ped.node_id if nearest_ped else None,
                "curb_height_m": nattrs.get("curb_height_m"),
                "sidewalk_width_m": width,
                "deployment_clearance_m": clearance,
                "blockage_risk": blockage,
                "map_confidence": min(float(n.confidence), float((reg or {}).get("confidence", 1.0) or 1.0)),
                "dynamic_confidence": 1.0 - blockage,
                "lighting": attrs.get("lighting"),
                "shelter": attrs.get("shelter"),
                "source": args.source_name,
                "evidence_notes": "derived_from_accessibility_graph_and_city_curb_regulation; legal_stop fails closed without matched regulation",
            }
            out.append(row)
            existing.add((eid, anchor_id))
    return out


def build(args: argparse.Namespace) -> Dict[str, Any]:
    rows = []
    for p in [args.input_pudo_evidence_jsonl, args.curb_inventory_jsonl]:
        rows.extend(_read(p))
    normalized_input = []
    for r in rows:
        # Curated inputs may include global curb inventory; only normalize rows with episode binding here.
        if r.get("episode_id") or (isinstance(r.get("properties"), dict) and r["properties"].get("episode_id")):
            normalized_input.append(normalize(r, args.source_name))
    if args.accessibility_graph_dir:
        out_rows = _build_from_graphs(args, normalized_input)
    else:
        if not normalized_input:
            raise RuntimeError("PUDO evidence build requires real curb/PUDO evidence or --accessibility_graph_dir to generate candidates; no synthetic fallback is available")
        out_rows = normalized_input
    total = max(1, len(out_rows))
    missing = {k: sum(1 for r in out_rows if r.get(k) is None) for k in CORE}
    if args.fail_on_missing_core_evidence:
        bad = {k: v / total for k, v in missing.items() if v / total > args.max_core_missing_rate}
        if bad:
            raise RuntimeError(f"core PUDO evidence missing rate too high: {bad}; threshold={args.max_core_missing_rate}")
    write_jsonl(args.output_pudo_evidence_jsonl, out_rows)
    report = {"rows": len(out_rows), "missing_core_counts": missing, "missing_core_rates": {k: v / total for k, v in missing.items()}, "source": args.source_name, "mode": "pudo_generator" if args.accessibility_graph_dir else "pudo_validator"}
    if args.report_json:
        dump_json(args.report_json, report)
    return report


def main() -> None:
    p = argparse.ArgumentParser(description="Generate audited PUDO evidence from accessibility graphs, curb inventory, and curb regulation evidence.")
    p.add_argument("--scene_dataset_dir", default=None)
    p.add_argument("--accessibility_graph_dir", default=None)
    p.add_argument("--nuplan_map_root", default=None)
    p.add_argument("--curb_regulation_dir", default=None)
    p.add_argument("--city_gis_dir", default=None)
    p.add_argument("--input_pudo_evidence_jsonl", default=None)
    p.add_argument("--curb_inventory_jsonl", default=None)
    p.add_argument("--curb_regulation_jsonl", default=None)
    p.add_argument("--output_pudo_evidence_jsonl", required=True)
    p.add_argument("--candidate_radius_m", type=float, default=250.0)
    p.add_argument("--regulation_snap_tolerance_m", type=float, default=12.0)
    p.add_argument("--max_route_deviation_m", type=float, default=300.0)
    p.add_argument("--source_name", default="city_curb_regulation+sidewalk_inventory")
    p.add_argument("--fail_on_missing_core_evidence", action="store_true")
    p.add_argument("--max_core_missing_rate", type=float, default=0.05)
    p.add_argument("--report_json", default=None)
    args = p.parse_args()
    print(json.dumps(build(args), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
