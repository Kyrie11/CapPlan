#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capplan.data.gis_fusion import CoordinateTransformer, distance_to_polyline, nearest_route_side, read_scene_contexts
from capplan.data.schemas import AccessibilityEdge, AccessibilityGraph, AccessibilityNode, edge_from_dict, node_from_dict
from capplan.utils.serialization import dump_json, read_jsonl, write_jsonl

CORE = ["curb_height_m", "deployment_clearance_m", "sidewalk_width_m"]


def _legacy_manual_audit_source(value: Any) -> bool:
    """Compatibility for pre-v2 reviewed audit rows.

    New importers write explicit ``authoritative``/``audited`` flags. Older
    CapPlan audit fixtures used source names such as ``manual_interface_audit``
    and ``manual_posted_sign_audit``. Accept only the narrow manual+audit
    pattern, never a generic ``manual`` or official-candidate label.
    """
    s = str(value or "").strip().lower()
    return ("manual" in s and "audit" in s) or s.startswith("reviewed_audit:") or s.startswith("manual_audit:")


def _paper_evidence_flags(row: Dict[str, Any]) -> Tuple[bool, bool, str]:
    core_complete = all(row.get(k) is not None for k in CORE)
    has_ped_binding = bool(row.get("adjacent_ped_node_id"))

    legality_source = str(row.get("legal_stop_source") or "").lower()
    has_legality_source = (
        bool(legality_source)
        and legality_source not in {"unknown", "none"}
        and "no_matching_regulation" not in legality_source
        and "heuristic" not in legality_source
        and "no_legality_evidence" not in legality_source
    )
    legality_authoritative = bool(row.get("legal_stop_authoritative")) or _legacy_manual_audit_source(legality_source)
    has_legality_evidence = has_legality_source and legality_authoritative

    source = str(row.get("source") or "").lower()
    interface_source = str(row.get("curb_inventory_source") or row.get("interface_evidence_source") or "").lower()
    trustworthy_source = not (source.startswith("synthetic") or source in {"toy", "mock", "unknown", ""})
    interface_source_ok = bool(interface_source) and not (interface_source.startswith("synthetic") or "proxy" in interface_source or interface_source in {"toy", "mock", "unknown"})
    inventory_core_fields = {str(x) for x in (row.get("curb_inventory_core_fields") or [])}
    legacy_manual_interface = _legacy_manual_audit_source(interface_source)
    if not inventory_core_fields and legacy_manual_interface and core_complete:
        # In the pre-v2 schema the audit source was stored but field-level core
        # provenance was not. This compatibility path is intentionally limited
        # to explicit manual-audit source names.
        inventory_core_fields = set(CORE)
    interface_authoritative = bool(row.get("curb_inventory_authoritative")) or legacy_manual_interface
    # Every publication-core dimension must be present on the *same matched*
    # audited/authoritative inventory record. This prevents a community/graph
    # width from being combined with one audited curb dimension and then
    # mislabeled as fully audited interface evidence.
    has_interface_evidence = interface_source_ok and interface_authoritative and all(k in inventory_core_fields for k in CORE)

    evidence_complete = core_complete and has_ped_binding and has_legality_evidence and trustworthy_source and has_interface_evidence
    eligible = evidence_complete and bool(row.get("legal_stop"))
    missing = [k for k in CORE if row.get(k) is None]
    reasons = []
    if missing:
        reasons.append("missing:" + ",".join(missing))
    if not has_ped_binding:
        reasons.append("no_pedestrian_binding")
    if not has_legality_source:
        reasons.append("no_independent_legality_evidence")
    elif not legality_authoritative:
        reasons.append("legality_source_not_audited_or_authoritative")
    if not trustworthy_source:
        reasons.append("non_auditable_candidate_source")
    if not interface_source_ok:
        reasons.append("no_auditable_interface_evidence")
    elif not interface_authoritative:
        reasons.append("interface_source_not_audited_or_authoritative")
    else:
        missing_inventory = [k for k in CORE if k not in inventory_core_fields]
        if missing_inventory:
            reasons.append("interface_core_not_from_same_audited_inventory:" + ",".join(missing_inventory))
    if evidence_complete and not bool(row.get("legal_stop")):
        reasons.append("legality_negative")
    return evidence_complete, eligible, "paper_ready" if eligible else ("evidence_complete_negative" if evidence_complete else ";".join(reasons) or "candidate_uncertain")


def _annotate_paper_flags(row: Dict[str, Any]) -> Dict[str, Any]:
    complete, eligible, status = _paper_evidence_flags(row)
    row = dict(row)
    row["paper_evidence_complete"] = complete
    row["paper_eligible"] = eligible
    row["evidence_status"] = status
    return row


def _read(path: str | None) -> List[Dict[str, Any]]:
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    if p.is_dir():
        rows: List[Dict[str, Any]] = []
        for child in sorted(p.glob("*")):
            name = child.name.lower()
            if name.endswith((".report.json", ".provenance.json", ".manifest.json")):
                continue
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


def _row_view(row: Dict[str, Any]) -> Dict[str, Any]:
    props = row.get("properties") if isinstance(row.get("properties"), dict) else {}
    return {**props, **{k: v for k, v in row.items() if k not in {"properties", "geometry", "type"}}}


def _first_present(row: Dict[str, Any], keys: Iterable[str]) -> Any:
    d = _row_view(row)
    for k in keys:
        if d.get(k) not in (None, "", "unknown", "n/a"):
            return d.get(k)
    return None


def _looks_like_lonlat(x: float, y: float) -> bool:
    return -180.0 <= x <= 180.0 and -90.0 <= y <= 90.0


def _maybe_to_map(x: float, y: float, row: Dict[str, Any], transformer: Optional[CoordinateTransformer]) -> Tuple[float, float]:
    if transformer is None:
        return x, y
    d = _row_view(row)
    frame = str(d.get("frame") or d.get("coordinate_frame") or "").lower()
    if frame in {"map", "local", "nuplan_map"}:
        return x, y
    if frame in {"wgs84", "lonlat", "lon_lat", "epsg:4326", "crs84"} or _looks_like_lonlat(x, y):
        return transformer.wgs84_to_local(x, y)
    return x, y


def _xy_from_row(row: Dict[str, Any], transformer: Optional[CoordinateTransformer] = None) -> Optional[Tuple[float, float]]:
    d = _row_view(row)
    geom = row.get("geometry")
    if isinstance(geom, dict):
        coords = geom.get("coordinates")
        if isinstance(coords, list) and len(coords) >= 2 and isinstance(coords[0], (int, float)):
            return _maybe_to_map(float(coords[0]), float(coords[1]), row, transformer)
        if isinstance(coords, list) and coords and isinstance(coords[0], list):
            p = coords[0]
            if len(p) >= 2:
                return _maybe_to_map(float(p[0]), float(p[1]), row, transformer)
    if isinstance(d.get("curb_pose"), dict):
        return float(d["curb_pose"].get("x", 0.0)), float(d["curb_pose"].get("y", 0.0))
    if d.get("lon") is not None and d.get("lat") is not None:
        return _maybe_to_map(float(d["lon"]), float(d["lat"]), row, transformer)
    if d.get("longitude") is not None and d.get("latitude") is not None:
        return _maybe_to_map(float(d["longitude"]), float(d["latitude"]), row, transformer)
    if d.get("x") is not None and d.get("y") is not None:
        return _maybe_to_map(float(d["x"]), float(d["y"]), row, transformer)
    if d.get("curb_x") is not None and d.get("curb_y") is not None:
        return _maybe_to_map(float(d["curb_x"]), float(d["curb_y"]), row, transformer)
    return None


def normalize(row: Dict[str, Any], default_source: str, transformer: Optional[CoordinateTransformer] = None) -> Dict[str, Any]:
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
    xy = _xy_from_row(row, transformer)
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
    if any(out.get(k) is not None for k in CORE):
        out.setdefault("curb_inventory_source", str(source))
    tier = str(row.get("evidence_tier") or "").lower()
    authoritative = bool(row.get("authoritative") is True or row.get("audited") is True or tier.startswith("a_"))
    out.setdefault("curb_inventory_authoritative", authoritative)
    out.setdefault("curb_inventory_core_fields", [k for k in CORE if out.get(k) is not None])
    out.setdefault("legal_stop_source", row.get("legal_stop_source") or row.get("regulation_id") or row.get("curb_regulation_source") or source)
    out.setdefault("legal_stop_authoritative", authoritative)
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
    return _annotate_paper_flags(out)


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


def _regulation_match(x: float, y: float, regs: List[Dict[str, Any]], tolerance: float, transformer: Optional[CoordinateTransformer] = None) -> Optional[Dict[str, Any]]:
    best, best_d = None, float("inf")
    for r in regs:
        xy = _xy_from_row(r, transformer)
        if not xy:
            continue
        d = math.hypot(x - xy[0], y - xy[1])
        if d < best_d:
            best, best_d = r, d
    return best if best is not None and best_d <= tolerance else None


def _as_inventory_record(row: Dict[str, Any], transformer: Optional[CoordinateTransformer]) -> Optional[Dict[str, Any]]:
    xy = _xy_from_row(row, transformer)
    if not xy:
        return None
    source = _first_present(row, ["source", "evidence_source", "dataset", "name"]) or "curb_inventory"
    if _source_bad(source):
        return None
    rec: Dict[str, Any] = {
        "x": xy[0],
        "y": xy[1],
        "source": str(source),
        "confidence": _as_float(_first_present(row, ["confidence", "map_confidence", "score"])) or 0.75,
        "curb_height_m": _as_float(_first_present(row, ["curb_height_m", "curb_height", "curb:height", "kerb:height", "kerb_height_m"])),
        "deployment_clearance_m": _as_float(_first_present(row, ["deployment_clearance_m", "clearance_m", "clear_width_m", "landing_width_m", "landing_width", "ramp_clearance_m"])),
        "sidewalk_width_m": _as_float(_first_present(row, ["sidewalk_width_m", "sidewalk_width", "width_m", "width", "sidewalk:width"])),
        "side": _first_present(row, ["side", "curb_side", "route_side"]),
        "surface": _first_present(row, ["surface", "material"]),
        "curb_ramp": _bool(_first_present(row, ["curb_ramp", "ramp", "has_ramp", "kerb_ramp"]), False),
    }
    view = _row_view(row)
    tier = str(view.get("evidence_tier") or "").lower()
    legacy_audit = _legacy_manual_audit_source(source)
    rec["authoritative"] = bool(view.get("authoritative") is True or view.get("audited") is True or tier.startswith("a_") or legacy_audit)
    rec["audited"] = bool(view.get("audited") is True or legacy_audit)
    rec["evidence_tier"] = view.get("evidence_tier")
    rec["observed_at"] = view.get("observed_at")
    rec["core_fields"] = [k for k in CORE if rec.get(k) is not None]
    return rec


def _nearest_inventory_match(x: float, y: float, inventory: List[Dict[str, Any]], tolerance: float) -> Optional[Dict[str, Any]]:
    best, best_d = None, float("inf")
    for rec in inventory:
        d = math.hypot(x - float(rec["x"]), y - float(rec["y"]))
        if d < best_d:
            best, best_d = rec, d
    if best is not None and best_d <= tolerance:
        out = dict(best)
        out["distance_m"] = best_d
        return out
    return None


def _coalesce(*values: Any) -> Any:
    for v in values:
        if v is not None:
            return v
    return None


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
    transformer = CoordinateTransformer.from_file(args.georeference_json) if args.georeference_json else None
    contexts = read_scene_contexts(args.scene_dataset_dir, [], args.candidate_radius_m)
    scenes = {c.episode_id: c for c in contexts}
    out: List[Dict[str, Any]] = list(normalized_input_rows)
    existing = {(r.get("episode_id"), r.get("anchor_id")) for r in out}
    regs = _read(args.curb_regulation_jsonl) + _read(args.curb_regulation_dir)
    raw_inventory = _read(args.curb_inventory_jsonl)
    external_candidates: List[Dict[str, Any]] = []
    for candidate_path in (args.pudo_candidate_source or []):
        external_candidates.extend(_read(candidate_path))
    inventory = [normalize(r, args.source_name, transformer) for r in raw_inventory if r.get("episode_id") or (isinstance(r.get("properties"), dict) and r["properties"].get("episode_id"))]
    global_inventory = [rec for rec in (_as_inventory_record(r, transformer) for r in raw_inventory if not (r.get("episode_id") or (isinstance(r.get("properties"), dict) and r["properties"].get("episode_id")))) if rec is not None]
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
        # Official/public curbside layers (parking payment points, taxi zones,
        # taxi stands) may propose candidate locations, but NEVER confer stopping
        # legality or interface feasibility by themselves. Each candidate is
        # independently matched to regulation and physical curb evidence.
        for cidx, candidate in enumerate(external_candidates):
            xy = _xy_from_row(candidate, transformer)
            if not xy:
                continue
            x, y = xy
            if route and distance_to_polyline([x, y], route) > args.candidate_radius_m:
                continue
            nearest_ped, ped_dist = _nearest_node(x, y, graph.nodes, {"sidewalk", "crossing", "entrance"})
            ped_id = nearest_ped.node_id if nearest_ped is not None and ped_dist <= args.pedestrian_snap_tolerance_m else None
            attrs = _nearest_edge_attrs(x, y, graph) if ped_id else {}
            reg = _regulation_match(x, y, regs, args.regulation_snap_tolerance_m, transformer)
            inv = _nearest_inventory_match(x, y, global_inventory, args.inventory_snap_tolerance_m)
            legal = _bool((reg or {}).get("legal_stop", (reg or {}).get("stopping_allowed", (reg or {}).get("regulation"))), False)
            blockage = _blockage_from_agents(x, y, scene.metadata if scene else {})
            candidate_view = _row_view(candidate)
            candidate_source = str(candidate_view.get("source") or args.source_name)
            candidate_id = str(candidate_view.get("regulation_id") or candidate_view.get("anchor_id") or candidate_view.get("id") or f"external_{cidx:05d}")
            width = _coalesce((inv or {}).get("sidewalk_width_m"), attrs.get("sidewalk_width_m"))
            row = {
                "anchor_id": f"{eid}:candidate:{candidate_id}",
                "pudo_id": f"{eid}:candidate:{candidate_id}",
                "episode_id": eid, "kind": "pickup_dropoff",
                "curb_pose": {"x": x, "y": y, "heading": 0.0, "frame": "map"},
                "stop_pose": {"x": x, "y": y, "heading": 0.0, "frame": "map"},
                "x": x, "y": y,
                "side": str(_coalesce(candidate_view.get("side"), (inv or {}).get("side"), nearest_route_side([x, y], route) if route else "unknown")),
                "legal_stop": legal,
                "legal_stop_source": str((reg or {}).get("source") or (reg or {}).get("regulation_id") or "no_matching_regulation_fail_closed"),
                "legal_stop_authoritative": bool((reg or {}).get("authoritative") is True or (reg or {}).get("audited") is True or str((reg or {}).get("evidence_tier") or "").lower().startswith("a_") or _legacy_manual_audit_source((reg or {}).get("source"))),
                "adjacent_ped_node_id": ped_id,
                "curb_height_m": (inv or {}).get("curb_height_m"),
                "sidewalk_width_m": width,
                "deployment_clearance_m": (inv or {}).get("deployment_clearance_m"),
                "blockage_risk": blockage,
                "map_confidence": min(float(candidate_view.get("confidence", 0.6) or 0.6), float((inv or {}).get("confidence", 1.0) or 1.0), float((reg or {}).get("confidence", 1.0) or 1.0)),
                "dynamic_confidence": 1.0 - blockage,
                "lighting": attrs.get("lighting"), "shelter": attrs.get("shelter"),
                "candidate_source": candidate_source,
                "candidate_only": True,
                "curb_inventory_source": (inv or {}).get("source"),
                "curb_inventory_authoritative": bool((inv or {}).get("authoritative")),
                "curb_inventory_core_fields": list((inv or {}).get("core_fields") or []),
                "curb_inventory_match_distance_m": (inv or {}).get("distance_m"),
                "source": candidate_source,
                "evidence_notes": "external_candidate_only; legality and board/alight interface require independent matched evidence",
            }
            key = (eid, row["anchor_id"])
            if key not in existing:
                out.append(_annotate_paper_flags(row)); existing.add(key)

        for idx, n in enumerate(_candidate_nodes(graph, route, args.candidate_radius_m)):
            anchor_id = f"{eid}:pudo_{idx:04d}"
            if (eid, anchor_id) in existing:
                continue
            attrs = _nearest_edge_attrs(n.x, n.y, graph)
            meta_attrs = graph.metadata.get("node_attributes", {}) if isinstance(graph.metadata, dict) else {}
            nattrs = meta_attrs.get(n.node_id, {}) if isinstance(meta_attrs, dict) else {}
            reg = _regulation_match(n.x, n.y, regs, args.regulation_snap_tolerance_m, transformer)
            inv = _nearest_inventory_match(n.x, n.y, global_inventory, args.inventory_snap_tolerance_m)
            legal = _bool((reg or {}).get("legal_stop", (reg or {}).get("stopping_allowed", (reg or {}).get("regulation"))), False)
            nearest_ped, _ = _nearest_node(n.x, n.y, graph.nodes, {"sidewalk", "crossing", "entrance"})
            blockage = _blockage_from_agents(n.x, n.y, scene.metadata if scene else {})
            # Publication-core interface dimensions must come from the matched
            # audited/authoritative inventory when available.  The old order
            # preferred graph/OSM values but still labeled curb_inventory_source
            # with the audited record, which could misattribute provenance.
            width = _coalesce((inv or {}).get("sidewalk_width_m"), nattrs.get("width_m"), attrs.get("sidewalk_width_m"))
            clearance = _coalesce((inv or {}).get("deployment_clearance_m"), nattrs.get("deployment_clearance_m"))
            curb_height = _coalesce((inv or {}).get("curb_height_m"), nattrs.get("curb_height_m"))
            confidence_terms = [float(n.confidence), float((reg or {}).get("confidence", 1.0) or 1.0)]
            if inv:
                confidence_terms.append(float(inv.get("confidence", 1.0) or 1.0))
            row = {
                "anchor_id": anchor_id,
                "pudo_id": anchor_id,
                "episode_id": eid,
                "kind": "pickup_dropoff",
                "curb_pose": {"x": n.x, "y": n.y, "heading": 0.0, "frame": "map"},
                "stop_pose": {"x": n.x, "y": n.y, "heading": 0.0, "frame": "map"},
                "x": n.x,
                "y": n.y,
                "side": str(_coalesce(nattrs.get("route_side"), (inv or {}).get("side"), nearest_route_side([n.x, n.y], route) if route else "unknown")),
                "legal_stop": legal,
                "legal_stop_source": str((reg or {}).get("source") or (reg or {}).get("regulation_id") or "no_matching_regulation_fail_closed"),
                "legal_stop_authoritative": bool((reg or {}).get("authoritative") is True or (reg or {}).get("audited") is True or str((reg or {}).get("evidence_tier") or "").lower().startswith("a_") or _legacy_manual_audit_source((reg or {}).get("source"))),
                "adjacent_ped_node_id": nearest_ped.node_id if nearest_ped else None,
                "curb_height_m": curb_height,
                "sidewalk_width_m": width,
                "deployment_clearance_m": clearance,
                "blockage_risk": blockage,
                "map_confidence": min(confidence_terms),
                "dynamic_confidence": 1.0 - blockage,
                "lighting": attrs.get("lighting"),
                "shelter": attrs.get("shelter"),
                "curb_inventory_source": (inv or {}).get("source"),
                "curb_inventory_authoritative": bool((inv or {}).get("authoritative")),
                "curb_inventory_core_fields": list((inv or {}).get("core_fields") or []),
                "curb_inventory_match_distance_m": (inv or {}).get("distance_m"),
                "source": args.source_name,
                "evidence_notes": "derived_from_accessibility_graph_and_city_curb_regulation; legal_stop fails closed without matched regulation",
            }
            out.append(_annotate_paper_flags(row))
            existing.add((eid, anchor_id))
    return out


def build(args: argparse.Namespace) -> Dict[str, Any]:
    transformer = CoordinateTransformer.from_file(args.georeference_json) if args.georeference_json else None
    rows = []
    for p in [args.input_pudo_evidence_jsonl, args.curb_inventory_jsonl]:
        rows.extend(_read(p))
    normalized_input = []
    for r in rows:
        # Curated inputs may include global curb inventory; only normalize rows with episode binding here.
        if r.get("episode_id") or (isinstance(r.get("properties"), dict) and r["properties"].get("episode_id")):
            normalized_input.append(normalize(r, args.source_name, transformer))
    if args.accessibility_graph_dir:
        out_rows = _build_from_graphs(args, normalized_input)
    else:
        if not normalized_input:
            raise RuntimeError("PUDO evidence build requires real curb/PUDO evidence or --accessibility_graph_dir to generate candidates; no synthetic fallback is available")
        out_rows = normalized_input
    out_rows = [_annotate_paper_flags(r) for r in out_rows]
    total = max(1, len(out_rows))
    missing = {k: sum(1 for r in out_rows if r.get(k) is None) for k in CORE}
    if args.fail_on_missing_core_evidence:
        bad = {k: v / total for k, v in missing.items() if v / total > args.max_core_missing_rate}
        if bad:
            raise RuntimeError(f"core PUDO evidence missing rate too high: {bad}; threshold={args.max_core_missing_rate}")
    write_jsonl(args.output_pudo_evidence_jsonl, out_rows)
    by_episode: Dict[str, Dict[str, int]] = {}
    for r in out_rows:
        e = by_episode.setdefault(str(r.get("episode_id") or "unknown"), {"total": 0, "evidence_complete": 0, "paper_eligible": 0})
        e["total"] += 1
        e["evidence_complete"] += int(bool(r.get("paper_evidence_complete")))
        e["paper_eligible"] += int(bool(r.get("paper_eligible")))
    report = {
        "status": "PASS", "rows": len(out_rows), "missing_core_counts": missing,
        "missing_core_rates": {k: v / total for k, v in missing.items()},
        "paper_evidence_complete": sum(int(bool(r.get("paper_evidence_complete"))) for r in out_rows),
        "paper_eligible": sum(int(bool(r.get("paper_eligible"))) for r in out_rows),
        "episodes": by_episode, "source": args.source_name,
        "mode": "pudo_generator" if args.accessibility_graph_dir else "pudo_validator",
        "interpretation": "Unknown candidates are retained. paper_eligible requires independent legality, pedestrian binding, and all three core interface fields.",
    }
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
    p.add_argument("--pudo_candidate_source", action="append", default=[], help="Optional normalized public PUDO candidate layer; repeatable. Candidate layers never confer legality or interface feasibility.")
    p.add_argument("--georeference_json", default=None)
    p.add_argument("--output_pudo_evidence_jsonl", required=True)
    p.add_argument("--candidate_radius_m", type=float, default=250.0)
    p.add_argument("--regulation_snap_tolerance_m", type=float, default=12.0)
    p.add_argument("--inventory_snap_tolerance_m", type=float, default=15.0)
    p.add_argument("--pedestrian_snap_tolerance_m", type=float, default=25.0)
    p.add_argument("--max_route_deviation_m", type=float, default=300.0)
    p.add_argument("--source_name", default="city_curb_regulation+sidewalk_inventory")
    p.add_argument("--fail_on_missing_core_evidence", action="store_true")
    p.add_argument("--max_core_missing_rate", type=float, default=0.05)
    p.add_argument("--report_json", default=None)
    args = p.parse_args()
    print(json.dumps(build(args), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
