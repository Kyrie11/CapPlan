#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capplan.data.gis_fusion import (
    AccessibilityFusionBuilder,
    CoordinateTransformer,
    load_gis_features,
    read_scene_contexts,
)
from capplan.data.schemas import AccessibilityEdge, AccessibilityGraph, AccessibilityNode, Pose2D, to_dict
from capplan.utils.serialization import dump_json, read_jsonl, write_jsonl

try:
    from tqdm.auto import tqdm  # type: ignore
except Exception:  # pragma: no cover - tqdm is optional for minimal envs
    def tqdm(iterable=None, **kwargs):  # type: ignore
        return iterable if iterable is not None else []


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


def _episode_ids(value: str | None) -> List[str]:
    return [x.strip() for x in (value or "shared").replace(",", "+").split("+") if x.strip()]


def _write_graph(out: Path, graph: AccessibilityGraph) -> None:
    write_jsonl(out / f"{graph.episode_id}.nodes.jsonl", [to_dict(n) for n in graph.nodes])
    write_jsonl(out / f"{graph.episode_id}.edges.jsonl", [to_dict(e) for e in graph.edges])
    write_jsonl(out / f"{graph.episode_id}.jsonl", [to_dict(graph)])




def _bbox_of_points(points: Iterable[Sequence[float]]) -> Optional[Tuple[float, float, float, float]]:
    xs: List[float] = []
    ys: List[float] = []
    for p in points:
        if len(p) < 2:
            continue
        try:
            xs.append(float(p[0])); ys.append(float(p[1]))
        except Exception:
            continue
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def _feature_bbox(features: Sequence[Any], attr: str = "geometry") -> Optional[Tuple[float, float, float, float]]:
    pts: List[Sequence[float]] = []
    for f in features:
        pts.extend(getattr(f, attr, []) or [])
    return _bbox_of_points(pts)


def _bbox_overlap(a: Optional[Tuple[float, float, float, float]], b: Optional[Tuple[float, float, float, float]]) -> Optional[bool]:
    if a is None or b is None:
        return None
    return not (a[2] < b[0] or b[2] < a[0] or a[3] < b[1] or b[3] < a[1])


def _bbox_dict(b: Optional[Tuple[float, float, float, float]]) -> Optional[Dict[str, float]]:
    if b is None:
        return None
    return {"min_x": float(b[0]), "min_y": float(b[1]), "max_x": float(b[2]), "max_y": float(b[3])}


def _spatial_diagnostics(scene: Any, features: Sequence[Any], transformer: CoordinateTransformer) -> Dict[str, Any]:
    cropped = [
        f for f in features
        if scene.bbox is None or any(
            scene.bbox[0] <= float(p[0]) <= scene.bbox[2] and scene.bbox[1] <= float(p[1]) <= scene.bbox[3]
            for p in (getattr(f, "geometry", []) or [])
        )
    ]
    local_bbox = _feature_bbox(features, "geometry")
    wgs_bbox = _feature_bbox(features, "wgs84_geometry")
    route_wgs84: List[List[float]] = []
    for p in getattr(scene, "route_polyline", []) or []:
        if len(p) >= 2:
            try:
                lon, lat = transformer.local_to_wgs84(float(p[0]), float(p[1]))
                route_wgs84.append([lon, lat])
            except Exception:
                pass
    return {
        "episode_id": getattr(scene, "episode_id", None),
        "map_name": getattr(scene, "map_name", None),
        "scene_bbox_local": _bbox_dict(getattr(scene, "bbox", None)),
        "feature_bbox_local": _bbox_dict(local_bbox),
        "feature_bbox_wgs84": _bbox_dict(wgs_bbox),
        "route_bbox_wgs84_from_georef": _bbox_dict(_bbox_of_points(route_wgs84)),
        "features_loaded": len(features),
        "features_inside_scene_bbox": len(cropped),
        "local_bbox_overlaps_scene_bbox": _bbox_overlap(getattr(scene, "bbox", None), local_bbox),
        "georeference_validated": bool(transformer.config.get("validated", False)),
        "georeference_description": transformer.config.get("description"),
        "hint": "0 cropped features usually means the WGS84->nuPlan map georeference is not aligned, or the Overpass/city-GIS bbox does not cover this nuPlan map/scenario.",
    }

def _build_prepared(args: argparse.Namespace) -> Dict[str, Any]:
    node_rows = _read_json_records(args.nodes_jsonl or args.osm_nodes_jsonl)
    edge_rows = _read_json_records(args.edges_jsonl or args.osm_edges_jsonl)
    if not node_rows or not edge_rows:
        raise RuntimeError("prepared accessibility graph build requires explicit node and edge records")
    if args.fail_on_synthetic:
        _reject_synthetic(node_rows, "nodes")
        _reject_synthetic(edge_rows, "edges")
    nodes = [_node(r, args.source_name) for r in node_rows]
    by_id = {n.node_id: n for n in nodes}
    edges = [_edge(r, args.source_name, by_id) for r in edge_rows]
    if len(nodes) < args.min_nodes_per_episode or len(edges) < args.min_edges_per_episode:
        raise RuntimeError(f"accessibility graph too small: {len(nodes)} nodes/{len(edges)} edges; required {args.min_nodes_per_episode}/{args.min_edges_per_episode}")
    out = Path(args.output_graph_dir)
    out.mkdir(parents=True, exist_ok=True)
    episodes = _episode_ids(args.episode_ids)
    for eid in episodes:
        graph = AccessibilityGraph(eid, nodes, edges, {"source": args.source_name, "builder": "prepared_jsonl_validator", "episode_radius_m": args.episode_radius_m, "snap_tolerance_m": args.snap_tolerance_m})
        _write_graph(out, graph)
    return {"episodes": episodes, "nodes": len(nodes), "edges": len(edges), "source": args.source_name, "mode": "prepared_jsonl", "synthetic_rejected": bool(args.fail_on_synthetic)}


def build_graphs(args: argparse.Namespace) -> Dict[str, Any]:
    has_prepared = bool(args.nodes_jsonl or args.edges_jsonl or args.osm_nodes_jsonl or args.osm_edges_jsonl)
    has_gis = bool(args.osm_source or args.opensidewalks_source or args.city_gis_dir or args.curb_inventory_source or args.entrance_source)
    if has_prepared and not has_gis:
        report = _build_prepared(args)
    else:
        if not has_gis:
            raise RuntimeError("GIS fusion requires --osm_source/--opensidewalks_source/--city_gis_dir/--curb_inventory_source/--entrance_source or prepared node/edge JSONL")
        transformer = CoordinateTransformer.from_file(args.georeference_json)
        features = load_gis_features(
            [args.osm_source, args.opensidewalks_source, args.city_gis_dir, args.curb_inventory_source, args.entrance_source, args.elevation_source],
            transformer,
            args.source_name,
        )
        if args.fail_on_synthetic:
            _reject_synthetic([{"source": f.source, "id": f.feature_id} for f in features], "GIS features")
        if not features:
            raise RuntimeError("GIS fusion found no usable OSM/OpenSidewalks/city GIS features")
        contexts = read_scene_contexts(args.scene_dataset_dir, _episode_ids(args.episode_ids), args.episode_radius_m)
        out = Path(args.output_graph_dir)
        out.mkdir(parents=True, exist_ok=True)
        builder = AccessibilityFusionBuilder(transformer, args.snap_tolerance_m, args.source_name)
        graphs: List[AccessibilityGraph] = []
        diagnostics: List[Dict[str, Any]] = []
        iterator = contexts if args.disable_tqdm else tqdm(contexts, desc="accessibility graphs", unit="episode")
        for scene in iterator:
            try:
                graph = builder.build_for_scene(
                    scene,
                    features,
                    min_nodes=args.min_nodes_per_episode,
                    min_edges=args.min_edges_per_episode,
                    add_bidirectional=not args.no_bidirectional_edges,
                    pudo_connector_radius_m=args.pudo_connector_radius_m,
                )
            except RuntimeError as exc:
                diag = _spatial_diagnostics(scene, features, transformer)
                diagnostics.append(diag)
                if args.diagnostic_report_json:
                    dump_json(args.diagnostic_report_json, {"failed_episode": getattr(scene, "episode_id", None), "diagnostics": diagnostics})
                raise RuntimeError(f"{exc}\nSpatial alignment diagnostics: {json.dumps(diag, indent=2, sort_keys=True)}") from exc
            _write_graph(out, graph)
            graphs.append(graph)
        report = {
            "episodes": [g.episode_id for g in graphs],
            "nodes": sum(len(g.nodes) for g in graphs),
            "edges": sum(len(g.edges) for g in graphs),
            "source": args.source_name,
            "mode": "gis_fusion",
            "features_loaded": len(features),
            "synthetic_rejected": bool(args.fail_on_synthetic),
            "georeference": args.georeference_json,
            "georeference_validated": bool(transformer.config.get("validated", False)),
        }
    out = Path(args.output_graph_dir)
    dump_json(out / "source_report.json", report)
    dump_json(out / "quality_report.json", {"min_nodes_per_episode": args.min_nodes_per_episode, "min_edges_per_episode": args.min_edges_per_episode, **report})
    return report


def main() -> None:
    p = argparse.ArgumentParser(description="Build AbilityBench-AV accessibility graphs by fusing nuPlan scene corridors with OSM/OpenSidewalks/city sidewalk GIS.")
    p.add_argument("--scene_dataset_dir", default=None, help="Directory containing scenes.jsonl/episodes.jsonl used for episode IDs, route corridor, and scenario bbox cropping.")
    p.add_argument("--nuplan_map_root", default=None, help="Accepted for pipeline compatibility; nuPlan HD map semantics are supplied through scene metadata/map API during dataset build.")
    p.add_argument("--nuplan_map_version", default=None)
    p.add_argument("--georeference_json", default=None, help="JSON/YAML with origin_lat/origin_lon/origin_heading_deg or local_crs for nuPlan local frame <-> WGS84 conversion.")
    p.add_argument("--osm_source", default=None, help="OSM/Overpass JSON, OSM-derived GeoJSON/JSONL, or directory of such files.")
    p.add_argument("--opensidewalks_source", default=None, help="OpenSidewalks GeoJSON/JSONL/JSON export.")
    p.add_argument("--city_gis_dir", default=None, help="Directory or file containing city sidewalk/crosswalk/curb ramp GIS layers.")
    p.add_argument("--curb_inventory_source", default=None, help="City curb/PUDO inventory records with curb height, landing width, clearance, or regulation tags.")
    p.add_argument("--entrance_source", default=None, help="Building/POI entrance point layer; entrances are snapped to pedestrian topology.")
    p.add_argument("--elevation_source", default=None, help="DEM/elevation point/line export; endpoint elevations are used to derive missing slopes when possible.")
    p.add_argument("--nodes_jsonl", default=None, help="Prepared real accessibility nodes JSONL/JSON; used only when GIS sources are absent.")
    p.add_argument("--edges_jsonl", default=None, help="Prepared real accessibility edges JSONL/JSON; used only when GIS sources are absent.")
    p.add_argument("--osm_nodes_jsonl", default=None, help="Alias for prepared OSM/OpenSidewalks node records.")
    p.add_argument("--osm_edges_jsonl", default=None, help="Alias for prepared OSM/OpenSidewalks edge records.")
    p.add_argument("--output_graph_dir", required=True)
    p.add_argument("--episode_ids", default="shared")
    p.add_argument("--episode_radius_m", type=float, default=800.0, help="Buffer around route corridor for per-scenario crop.")
    p.add_argument("--snap_tolerance_m", type=float, default=3.0)
    p.add_argument("--pudo_connector_radius_m", type=float, default=75.0, help="Distance from route corridor for curb/curb-ramp nodes marked as PUDO connector candidates.")
    p.add_argument("--min_nodes_per_episode", type=int, default=100)
    p.add_argument("--min_edges_per_episode", type=int, default=150)
    p.add_argument("--source_name", default="nuplan_osm_opensidewalks_citygis")
    p.add_argument("--fail_on_synthetic", action="store_true")
    p.add_argument("--no_bidirectional_edges", action="store_true")
    p.add_argument("--num_workers", type=int, default=0)
    p.add_argument("--disable_tqdm", action="store_true", help="Disable per-episode progress bars.")
    p.add_argument("--diagnostic_report_json", default=None, help="Optional path for spatial/georeference diagnostics on failure.")
    args = p.parse_args()
    print(json.dumps(build_graphs(args), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
