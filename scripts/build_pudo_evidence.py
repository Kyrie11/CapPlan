#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capplan.data.gis_fusion import CoordinateTransformer, distance_to_polyline, iter_scene_contexts, nearest_route_side, scene_context_count
from capplan.data.schemas import AccessibilityEdge, AccessibilityGraph, AccessibilityNode, edge_from_dict, node_from_dict
from capplan.utils.serialization import dump_json, read_jsonl, write_jsonl
from capplan.utils.build_fingerprint import fingerprint, fingerprint_object, path_signature

try:
    from tqdm.auto import tqdm  # type: ignore
except Exception:  # pragma: no cover
    def tqdm(iterable=None, **kwargs):  # type: ignore
        return iterable if iterable is not None else []

CORE = ["curb_height_m", "deployment_clearance_m", "sidewalk_width_m"]
PUDO_BUILD_VERSION = "20260820_exact_spatial_v3"


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
    meta: Dict[str, Any] = {}
    meta_file = graph_dir / f"{episode_id}.meta.json"
    graph_file = graph_dir / f"{episode_id}.jsonl"
    if meta_file.exists():
        payload = json.loads(meta_file.read_text(encoding="utf-8"))
        meta = dict(payload.get("metadata", payload)) if isinstance(payload, dict) else {}
    elif graph_file.exists():
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



class PolylineDistanceIndex:
    """Exact NumPy point-to-polyline distance/side queries.

    Candidate selection previously called the scalar Python
    ``distance_to_polyline`` function for every sidewalk node.  Singapore
    episodes contain tens of thousands of pedestrian nodes, making this alone
    a multi-second hot loop.  This class preserves the same point-segment
    metric and first-segment tie semantics while processing points in bounded
    NumPy chunks.
    """

    def __init__(self, polyline: List[List[float]]):
        self.points = np.asarray([(float(p[0]), float(p[1])) for p in (polyline or []) if len(p) >= 2], dtype=np.float64)
        if len(self.points) >= 2:
            self.a = self.points[:-1]
            self.b = self.points[1:]
            self.v = self.b - self.a
            self.den = np.einsum("ij,ij->i", self.v, self.v)
        else:
            self.a = np.empty((0, 2), dtype=np.float64)
            self.b = np.empty((0, 2), dtype=np.float64)
            self.v = np.empty((0, 2), dtype=np.float64)
            self.den = np.empty((0,), dtype=np.float64)

    def distances(self, points: np.ndarray | List[Tuple[float, float]]) -> np.ndarray:
        pts = np.asarray(points, dtype=np.float64).reshape((-1, 2))
        if pts.size == 0:
            return np.empty((0,), dtype=np.float64)
        if len(self.points) == 0:
            return np.zeros((len(pts),), dtype=np.float64)
        if len(self.points) == 1:
            d = pts - self.points[0]
            return np.sqrt(np.einsum("ij,ij->i", d, d))

        result = np.full(len(pts), np.inf, dtype=np.float64)
        # Keep peak temporary memory small even for long map-derived route
        # polylines.  The two-dimensional distance matrix is at most about
        # 1024*256 doubles per inner block.
        for p0 in range(0, len(pts), 1024):
            q = pts[p0:p0 + 1024]
            best = np.full(len(q), np.inf, dtype=np.float64)
            for s0 in range(0, len(self.a), 256):
                a = self.a[s0:s0 + 256]
                v = self.v[s0:s0 + 256]
                den = self.den[s0:s0 + 256]
                wx = q[:, None, 0] - a[None, :, 0]
                wy = q[:, None, 1] - a[None, :, 1]
                num = wx * v[None, :, 0] + wy * v[None, :, 1]
                t = np.divide(num, den[None, :], out=np.zeros_like(num), where=den[None, :] > 0.0)
                np.clip(t, 0.0, 1.0, out=t)
                dx = a[None, :, 0] + t * v[None, :, 0] - q[:, None, 0]
                dy = a[None, :, 1] + t * v[None, :, 1] - q[:, None, 1]
                d2 = dx * dx + dy * dy
                best = np.minimum(best, np.min(d2, axis=1))
            result[p0:p0 + len(q)] = np.sqrt(best)
        return result

    def distance_and_side(self, x: float, y: float) -> Tuple[float, str]:
        if len(self.points) == 0:
            return 0.0, "unknown"
        if len(self.points) == 1:
            return float(math.hypot(float(x) - self.points[0, 0], float(y) - self.points[0, 1])), "unknown"
        p = np.asarray([float(x), float(y)], dtype=np.float64)
        w = p - self.a
        num = np.einsum("ij,ij->i", w, self.v)
        t = np.divide(num, self.den, out=np.zeros_like(num), where=self.den > 0.0)
        t = np.clip(t, 0.0, 1.0)
        proj = self.a + self.v * t[:, None]
        d = proj - p
        d2 = np.einsum("ij,ij->i", d, d)
        i = int(np.argmin(d2))
        a = self.a[i]; b = self.b[i]
        cross = (b[0] - a[0]) * (float(y) - a[1]) - (b[1] - a[1]) * (float(x) - a[0])
        side = "left" if cross > 0 else "right" if cross < 0 else "unknown"
        return float(math.sqrt(float(d2[i]))), side


class GraphSpatialIndex:
    """Exact graph spatial queries using local metric grids.

    PUDO callers need exact semantics but almost always query points that are on
    or within 25 m of pedestrian topology.  Scanning all 20k--40k nodes/edges
    for every candidate is therefore unnecessary.  Segment AABBs are indexed in
    a uniform grid; a ring search stops only when the current best distance is
    no greater than the distance to the boundary of the searched square.  Any
    unseen segment is then provably farther away, so this is not an approximate
    nearest-neighbour optimization.
    """

    def __init__(self, graph: AccessibilityGraph, cell_size_m: float = 25.0):
        self.graph = graph
        self.nodes = list(graph.nodes)
        self.node_xy = np.asarray([(float(n.x), float(n.y)) for n in self.nodes], dtype=np.float64)
        self.kind_indices: Dict[str, np.ndarray] = {}
        kinds = sorted({str(n.kind) for n in self.nodes})
        for kind in kinds:
            self.kind_indices[kind] = np.asarray([i for i, n in enumerate(self.nodes) if n.kind == kind], dtype=np.int64)

        self.cell = max(1.0, float(cell_size_m))
        self.node_grid: Dict[Tuple[int, int], List[int]] = {}
        for i, n in enumerate(self.nodes):
            key = (int(math.floor(float(n.x) / self.cell)), int(math.floor(float(n.y) / self.cell)))
            self.node_grid.setdefault(key, []).append(i)

        node_index = {n.node_id: i for i, n in enumerate(self.nodes)}
        edge_refs: List[AccessibilityEdge] = []
        a_xy: List[Tuple[float, float]] = []
        b_xy: List[Tuple[float, float]] = []
        for edge in graph.edges:
            ia = node_index.get(edge.from_node)
            ib = node_index.get(edge.to_node)
            if ia is None or ib is None:
                continue
            edge_refs.append(edge)
            a_xy.append((self.nodes[ia].x, self.nodes[ia].y))
            b_xy.append((self.nodes[ib].x, self.nodes[ib].y))
        self.edge_refs = edge_refs
        self.edge_a = np.asarray(a_xy, dtype=np.float64).reshape((-1, 2)) if a_xy else np.empty((0, 2), dtype=np.float64)
        self.edge_b = np.asarray(b_xy, dtype=np.float64).reshape((-1, 2)) if b_xy else np.empty((0, 2), dtype=np.float64)
        self.edge_v = self.edge_b - self.edge_a
        self.edge_den = np.einsum("ij,ij->i", self.edge_v, self.edge_v) if len(self.edge_v) else np.empty((0,), dtype=np.float64)

        self.edge_grid: Dict[Tuple[int, int], List[int]] = {}
        self.global_edge_indices: List[int] = []
        for i in range(len(self.edge_refs)):
            xmin = min(float(self.edge_a[i, 0]), float(self.edge_b[i, 0]))
            xmax = max(float(self.edge_a[i, 0]), float(self.edge_b[i, 0]))
            ymin = min(float(self.edge_a[i, 1]), float(self.edge_b[i, 1]))
            ymax = max(float(self.edge_a[i, 1]), float(self.edge_b[i, 1]))
            ix0 = int(math.floor(xmin / self.cell)); ix1 = int(math.floor(xmax / self.cell))
            iy0 = int(math.floor(ymin / self.cell)); iy1 = int(math.floor(ymax / self.cell))
            ncells = (ix1 - ix0 + 1) * (iy1 - iy0 + 1)
            if ncells > 256:
                # Very long segments are rare. Keeping them in a tiny global
                # list avoids exploding the grid while still testing them for
                # every query, preserving exactness.
                self.global_edge_indices.append(i)
                continue
            for ix in range(ix0, ix1 + 1):
                for iy in range(iy0, iy1 + 1):
                    self.edge_grid.setdefault((ix, iy), []).append(i)

    def nearest_node(self, x: float, y: float, kinds: Optional[set[str]] = None) -> tuple[Optional[AccessibilityNode], float]:
        """Exact global nearest node; retained for compatibility/tests."""
        if not self.nodes:
            return None, float("inf")
        if kinds:
            chunks = [self.kind_indices[k] for k in sorted(kinds) if k in self.kind_indices and self.kind_indices[k].size]
            if not chunks:
                return None, float("inf")
            idx = np.concatenate(chunks) if len(chunks) > 1 else chunks[0]
            idx = np.sort(idx)
        else:
            idx = np.arange(len(self.nodes), dtype=np.int64)
        pts = self.node_xy[idx]
        dx = pts[:, 0] - float(x); dy = pts[:, 1] - float(y)
        d2 = dx * dx + dy * dy
        local = int(np.argmin(d2)); global_idx = int(idx[local])
        return self.nodes[global_idx], float(math.sqrt(float(d2[local])))

    def nearest_node_within(self, x: float, y: float, kinds: set[str], tolerance: float) -> tuple[Optional[AccessibilityNode], float]:
        """Exact nearest node when the caller only accepts matches in tolerance."""
        if not self.nodes or tolerance < 0:
            return None, float("inf")
        tol = float(tolerance); cell = self.cell
        ix0 = int(math.floor((float(x) - tol) / cell)); ix1 = int(math.floor((float(x) + tol) / cell))
        iy0 = int(math.floor((float(y) - tol) / cell)); iy1 = int(math.floor((float(y) + tol) / cell))
        indices: set[int] = set()
        for ix in range(ix0, ix1 + 1):
            for iy in range(iy0, iy1 + 1):
                indices.update(self.node_grid.get((ix, iy), ()))
        if not indices:
            return None, float("inf")
        idx = np.asarray(sorted(i for i in indices if self.nodes[i].kind in kinds), dtype=np.int64)
        if idx.size == 0:
            return None, float("inf")
        pts = self.node_xy[idx]
        dx = pts[:, 0] - float(x); dy = pts[:, 1] - float(y)
        d2 = dx * dx + dy * dy
        local = int(np.argmin(d2)); dist = float(math.sqrt(float(d2[local])))
        if dist > tol:
            return None, float("inf")
        return self.nodes[int(idx[local])], dist

    def _dist2_for_edges(self, x: float, y: float, idx: np.ndarray) -> np.ndarray:
        if idx.size == 0:
            return np.empty((0,), dtype=np.float64)
        a = self.edge_a[idx]; v = self.edge_v[idx]; den = self.edge_den[idx]
        wx = float(x) - a[:, 0]; wy = float(y) - a[:, 1]
        num = wx * v[:, 0] + wy * v[:, 1]
        t = np.divide(num, den, out=np.zeros_like(num), where=den > 0.0)
        t = np.clip(t, 0.0, 1.0)
        dx = a[:, 0] + v[:, 0] * t - float(x)
        dy = a[:, 1] + v[:, 1] * t - float(y)
        return dx * dx + dy * dy

    def nearest_edge_attrs(self, x: float, y: float) -> Dict[str, Any]:
        if not self.edge_refs:
            return {}
        cx = int(math.floor(float(x) / self.cell)); cy = int(math.floor(float(y) / self.cell))
        seen: set[int] = set()
        best_i: Optional[int] = None; best_d2 = float("inf")

        def consider(raw: Iterable[int]) -> None:
            nonlocal best_i, best_d2
            fresh = sorted(i for i in raw if i not in seen)
            if not fresh:
                return
            seen.update(fresh)
            idx = np.asarray(fresh, dtype=np.int64)
            d2 = self._dist2_for_edges(x, y, idx)
            j = int(np.argmin(d2)); cand_i = int(idx[j]); cand_d2 = float(d2[j])
            if cand_d2 < best_d2 or (cand_d2 == best_d2 and (best_i is None or cand_i < best_i)):
                best_i, best_d2 = cand_i, cand_d2

        consider(self.global_edge_indices)
        for ring in range(0, 17):
            cells: List[Tuple[int, int]] = []
            if ring == 0:
                cells.append((cx, cy))
            else:
                for ix in range(cx - ring, cx + ring + 1):
                    cells.append((ix, cy - ring)); cells.append((ix, cy + ring))
                for iy in range(cy - ring + 1, cy + ring):
                    cells.append((cx - ring, iy)); cells.append((cx + ring, iy))
            raw: List[int] = []
            for key in cells:
                raw.extend(self.edge_grid.get(key, ()))
            consider(raw)
            if best_i is not None:
                xmin = (cx - ring) * self.cell; xmax = (cx + ring + 1) * self.cell
                ymin = (cy - ring) * self.cell; ymax = (cy + ring + 1) * self.cell
                boundary = min(float(x) - xmin, xmax - float(x), float(y) - ymin, ymax - float(y))
                if best_d2 <= max(0.0, boundary) ** 2:
                    break
        else:
            # Extremely sparse topology: guarantee exactness with a full
            # vectorized fallback rather than expanding empty grid rings forever.
            idx = np.arange(len(self.edge_refs), dtype=np.int64)
            d2 = self._dist2_for_edges(x, y, idx)
            best_i = int(np.argmin(d2)); best_d2 = float(d2[best_i])

        if best_i is None:
            idx = np.arange(len(self.edge_refs), dtype=np.int64)
            d2 = self._dist2_for_edges(x, y, idx)
            best_i = int(np.argmin(d2)); best_d2 = float(d2[best_i])
        edge = self.edge_refs[best_i]
        return {
            "sidewalk_width_m": edge.width_m,
            "lighting": edge.lighting,
            "shelter": edge.shelter,
            "surface": edge.surface,
            "distance_to_ped_edge_m": float(math.sqrt(best_d2)),
        }


class PointRecordIndex:
    """Exact tolerance-bounded nearest lookup for normalized point records."""

    def __init__(self, records: List[Dict[str, Any]], cell_size_m: float = 20.0):
        self.records = records
        self.cell = max(1.0, float(cell_size_m))
        if records:
            self.xy = np.asarray([(float(r["x"]), float(r["y"])) for r in records], dtype=np.float64)
        else:
            self.xy = np.empty((0, 2), dtype=np.float64)
        self.grid: Dict[Tuple[int, int], List[int]] = {}
        for i, (x, y) in enumerate(self.xy):
            self.grid.setdefault((int(math.floor(float(x) / self.cell)), int(math.floor(float(y) / self.cell))), []).append(i)

    def nearest(self, x: float, y: float, tolerance: float, *, with_distance: bool = False) -> Optional[Dict[str, Any]]:
        if self.xy.size == 0 or tolerance < 0:
            return None
        tol = float(tolerance); cell = self.cell
        ix0 = int(math.floor((float(x) - tol) / cell)); ix1 = int(math.floor((float(x) + tol) / cell))
        iy0 = int(math.floor((float(y) - tol) / cell)); iy1 = int(math.floor((float(y) + tol) / cell))
        candidates: set[int] = set()
        for ix in range(ix0, ix1 + 1):
            for iy in range(iy0, iy1 + 1):
                candidates.update(self.grid.get((ix, iy), ()))
        if not candidates:
            return None
        idx = np.asarray(sorted(candidates), dtype=np.int64)
        pts = self.xy[idx]
        dx = pts[:, 0] - float(x); dy = pts[:, 1] - float(y)
        d2 = dx * dx + dy * dy
        j = int(np.argmin(d2)); dist = float(math.sqrt(float(d2[j])))
        if dist > tol:
            return None
        out = dict(self.records[int(idx[j])])
        if with_distance:
            out["distance_m"] = dist
        return out


class TemporalOccupancyIndex:
    """Vectorized version of the exact timestep occupancy blockage metric."""

    def __init__(self, scene: Dict[str, Any]):
        self.steps: List[np.ndarray] = []
        for step in (scene.get("agent_history", []) or []):
            pts: List[Tuple[float, float]] = []
            for obj in (step.get("objects", []) or []):
                try:
                    pts.append((float(obj.get("x")), float(obj.get("y"))))
                except Exception:
                    continue
            self.steps.append(np.asarray(pts, dtype=np.float64).reshape((-1, 2)) if pts else np.empty((0, 2), dtype=np.float64))

    def query_many(self, points: List[Tuple[float, float]] | np.ndarray, radius: float = 6.0) -> np.ndarray:
        pts = np.asarray(points, dtype=np.float64).reshape((-1, 2))
        if len(pts) == 0:
            return np.empty((0,), dtype=np.float64)
        if not self.steps:
            return np.zeros((len(pts),), dtype=np.float64)
        occupied = np.zeros((len(pts),), dtype=np.int32)
        r2 = float(radius) * float(radius)
        for objs in self.steps:
            if len(objs) == 0:
                continue
            hit = np.zeros((len(pts),), dtype=bool)
            for p0 in range(0, len(pts), 256):
                q = pts[p0:p0 + 256]
                # Typical nuPlan sampled timesteps contain a modest number of
                # actors. Chunk only points to keep temporary memory bounded.
                dx = q[:, None, 0] - objs[None, :, 0]
                dy = q[:, None, 1] - objs[None, :, 1]
                hit[p0:p0 + len(q)] = np.any(dx * dx + dy * dy <= r2, axis=1)
            occupied += hit.astype(np.int32)
        return occupied.astype(np.float64) / float(max(1, len(self.steps)))

def _as_regulation_record(row: Dict[str, Any], transformer: Optional[CoordinateTransformer]) -> Optional[Dict[str, Any]]:
    xy = _xy_from_row(row, transformer)
    if not xy:
        return None
    out = dict(_row_view(row))
    out["x"] = float(xy[0])
    out["y"] = float(xy[1])
    return out

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
    """Temporal occupancy ratio near a candidate curb point.

    Count *timesteps* containing at least one nearby actor rather than raw
    detections.  The old implementation counted the same stationary actor once
    per sample and saturated at 0.95 after ten detections, which made blockage
    depend on sampling rate rather than temporal occupancy.
    """
    steps = scene.get("agent_history", []) or []
    if not steps:
        return 0.0
    occupied = 0
    valid_steps = 0
    r2 = float(radius) * float(radius)
    for step in steps:
        objects = step.get("objects", []) or []
        valid_steps += 1
        hit = False
        for obj in objects:
            try:
                dx = float(obj.get("x")) - float(x)
                dy = float(obj.get("y")) - float(y)
            except Exception:
                continue
            if dx * dx + dy * dy <= r2:
                hit = True
                break
        occupied += int(hit)
    return float(occupied) / float(max(1, valid_steps))


def _candidate_nodes(
    graph: AccessibilityGraph,
    route: List[List[float]],
    radius: float,
    *,
    max_fallback: int = 128,
    fallback_spacing_m: float = 20.0,
    route_index: Optional[PolylineDistanceIndex] = None,
) -> List[Tuple[AccessibilityNode, float, str]]:
    """Return explicit curb candidates or a spatially-thinned fallback set.

    Explicit curb/curb-ramp/connector candidates are never capped.  Only the
    generic sidewalk/crossing/entrance fallback is deduplicated and capped.
    This avoids thousands of adjacent sidewalk vertices becoming independent
    PUDO audit rows while preserving explicit evidence.
    """
    meta_attrs = graph.metadata.get("node_attributes", {}) if isinstance(graph.metadata, dict) else {}
    rindex = route_index or PolylineDistanceIndex(route)
    explicit_nodes: List[AccessibilityNode] = []
    for n in graph.nodes:
        attrs = meta_attrs.get(n.node_id, {}) if isinstance(meta_attrs, dict) else {}
        if not (n.kind in {"curb", "curb_ramp"} or attrs.get("pudo_connector_candidate")):
            continue
        explicit_nodes.append(n)
    explicit: List[Tuple[AccessibilityNode, float, str]] = []
    if explicit_nodes:
        dists = rindex.distances([(n.x, n.y) for n in explicit_nodes]) if route else np.zeros(len(explicit_nodes))
        explicit = [(n, float(d), "explicit") for n, d in zip(explicit_nodes, dists) if not route or float(d) <= radius]
    if explicit:
        explicit.sort(key=lambda item: (item[1], item[0].node_id))
        return explicit

    fallback_nodes = [n for n in graph.nodes if n.kind in {"sidewalk", "crossing", "entrance"}]
    if route and fallback_nodes:
        dists = rindex.distances([(n.x, n.y) for n in fallback_nodes])
    else:
        dists = np.zeros(len(fallback_nodes), dtype=np.float64)
    fallback: List[Tuple[AccessibilityNode, float, str]] = [
        (n, float(d), "fallback")
        for n, d in zip(fallback_nodes, dists)
        if not route or float(d) <= radius
    ]
    fallback.sort(key=lambda item: (item[1], 0 if item[0].kind == "entrance" else 1 if item[0].kind == "crossing" else 2, item[0].node_id))

    spacing = max(0.1, float(fallback_spacing_m))
    seen_cells: set[Tuple[int, int]] = set()
    thinned: List[Tuple[AccessibilityNode, float, str]] = []
    for item in fallback:
        n = item[0]
        cell = (int(math.floor(float(n.x) / spacing)), int(math.floor(float(n.y) / spacing)))
        if cell in seen_cells:
            continue
        seen_cells.add(cell)
        thinned.append(item)
        if max_fallback > 0 and len(thinned) >= max_fallback:
            break
    return thinned


def _pudo_global_fingerprint(args: argparse.Namespace) -> str:
    payload = {
        "version": PUDO_BUILD_VERSION,
        "source_name": args.source_name,
        "candidate_radius_m": float(args.candidate_radius_m),
        "regulation_snap_tolerance_m": float(args.regulation_snap_tolerance_m),
        "inventory_snap_tolerance_m": float(args.inventory_snap_tolerance_m),
        "pedestrian_snap_tolerance_m": float(args.pedestrian_snap_tolerance_m),
        "max_route_deviation_m": float(args.max_route_deviation_m),
        "max_fallback_graph_candidates_per_episode": int(args.max_fallback_graph_candidates_per_episode),
        "fallback_candidate_spacing_m": float(args.fallback_candidate_spacing_m),
        "fail_on_missing_core_evidence": bool(args.fail_on_missing_core_evidence),
        "max_core_missing_rate": float(args.max_core_missing_rate),
    }
    scene_file = Path(args.scene_dataset_dir) / "scenes.jsonl" if args.scene_dataset_dir else None
    scene_manifest = Path(args.scene_dataset_dir) / "scene_context_manifest.json" if args.scene_dataset_dir else None
    scene_file_for_stat = scene_file
    if scene_manifest and scene_manifest.exists():
        try:
            sm = json.loads(scene_manifest.read_text(encoding="utf-8"))
            if sm.get("scenes_sha256"):
                payload["scene_content"] = {
                    "scenes_sha256": sm.get("scenes_sha256"),
                    "num_scenes": sm.get("num_scenes"),
                    "extract_version": sm.get("extract_version"),
                }
                scene_file_for_stat = None
        except Exception:
            pass
    paths = [
        scene_file_for_stat, args.georeference_json, args.input_pudo_evidence_jsonl,
        args.curb_inventory_jsonl, args.curb_regulation_jsonl, args.curb_regulation_dir,
        *(args.pudo_candidate_source or []),
    ]
    return fingerprint(payload, paths)


def _episode_pudo_fingerprint(global_fp: str, graph_dir: Path, episode_id: str) -> str:
    graph_marker = graph_dir / f"{episode_id}.build.json"
    graph_build_fp = None
    graph_build_version = None
    if graph_marker.exists():
        try:
            gm = json.loads(graph_marker.read_text(encoding="utf-8"))
            graph_build_fp = gm.get("build_fingerprint")
            graph_build_version = gm.get("build_version")
        except Exception:
            pass
    # Node/edge stat identities catch out-of-band graph rewrites even if a
    # stale marker was accidentally preserved.
    return fingerprint_object({
        "pudo_global_fingerprint": global_fp,
        "graph_build_fingerprint": graph_build_fp,
        "graph_build_version": graph_build_version,
        "graph_nodes": path_signature(graph_dir / f"{episode_id}.nodes.jsonl"),
        "graph_edges": path_signature(graph_dir / f"{episode_id}.edges.jsonl"),
        "graph_meta": path_signature(graph_dir / f"{episode_id}.meta.json"),
    })


def _build_from_graphs(args: argparse.Namespace, normalized_input_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build PUDO evidence as resumable per-episode shards, then concatenate.

    Full nuPlan runs can contain many thousands of episodes.  Keeping every PUDO
    row in one Python list makes memory scale with the full city and loses all
    work if the process stops before the final write.  Shards make memory scale
    with one episode and provide a durable resume boundary.
    """
    graph_dir = Path(args.accessibility_graph_dir)
    transformer = CoordinateTransformer.from_file(args.georeference_json) if args.georeference_json else None
    output = Path(args.output_pudo_evidence_jsonl)
    shard_dir = Path(args.shard_dir) if args.shard_dir else output.parent / f"{output.stem}.shards"
    shard_dir.mkdir(parents=True, exist_ok=True)
    global_build_fp = _pudo_global_fingerprint(args)

    normalized_by_episode: Dict[str, List[Dict[str, Any]]] = {}
    for row in normalized_input_rows:
        normalized_by_episode.setdefault(str(row.get("episode_id") or ""), []).append(row)

    raw_regs = _read(args.curb_regulation_jsonl) + _read(args.curb_regulation_dir)
    reg_records = [rec for rec in (_as_regulation_record(r, transformer) for r in raw_regs) if rec is not None]
    reg_index = PointRecordIndex(reg_records)

    raw_inventory = _read(args.curb_inventory_jsonl)
    for row in raw_inventory:
        if row.get("episode_id") or (isinstance(row.get("properties"), dict) and row["properties"].get("episode_id")):
            normalized = normalize(row, args.source_name, transformer)
            normalized_by_episode.setdefault(str(normalized.get("episode_id") or ""), []).append(normalized)
    global_inventory = [rec for rec in (_as_inventory_record(r, transformer) for r in raw_inventory if not (r.get("episode_id") or (isinstance(r.get("properties"), dict) and r["properties"].get("episode_id")))) if rec is not None]
    inventory_index = PointRecordIndex(global_inventory)

    prepared_external: List[Tuple[Dict[str, Any], float, float, int]] = []
    cidx = 0
    for candidate_path in (args.pudo_candidate_source or []):
        for candidate in _read(candidate_path):
            xy = _xy_from_row(candidate, transformer)
            if xy is not None:
                prepared_external.append((candidate, float(xy[0]), float(xy[1]), cidx))
            cidx += 1
    prepared_external_xy = np.asarray([(x, y) for _, x, y, _ in prepared_external], dtype=np.float64).reshape((-1, 2)) if prepared_external else np.empty((0, 2), dtype=np.float64)

    if args.scene_dataset_dir:
        scene_iter = iter_scene_contexts(args.scene_dataset_dir, [], args.candidate_radius_m)
        expected = scene_context_count(args.scene_dataset_dir)
    else:
        eids = sorted({p.name.split(".nodes.jsonl")[0] for p in graph_dir.glob("*.nodes.jsonl")})
        scene_iter = (type("_Scene", (), {"episode_id": eid, "route_polyline": [], "metadata": {}})() for eid in eids)
        expected = len(eids)

    if not args.disable_tqdm:
        scene_iter = tqdm(scene_iter, total=expected, desc="PUDO evidence", unit="episode", mininterval=1.0, dynamic_ncols=True)

    perf: Dict[str, Any] = {
        "episodes": 0,
        "resumed_episodes": 0,
        "rows_generated": 0,
        "graph_load_s": 0.0,
        "graph_index_s": 0.0,
        "external_candidate_s": 0.0,
        "graph_candidate_s": 0.0,
        "candidate_select_s": 0.0,
        "external_prefilter_s": 0.0,
        "blockage_batch_s": 0.0,
        "spatial_match_s": 0.0,
        "row_assembly_s": 0.0,
        "explicit_graph_candidates": 0,
        "fallback_graph_candidates": 0,
        "slowest_episodes": [],
    }
    total_rows = 0
    total_complete = 0
    total_eligible = 0
    missing = {k: 0 for k in CORE}
    rows_per_episode: List[int] = []
    shard_paths: List[Path] = []
    build_started = time.perf_counter()
    inprogress_marker = output.with_suffix(output.suffix + ".inprogress.json")
    started_unix = time.time()

    def update_inprogress() -> None:
        dump_json(inprogress_marker, {
            "status": "RUNNING",
            "source": args.source_name,
            "build_version": PUDO_BUILD_VERSION,
            "build_fingerprint": global_build_fp,
            "output": str(output),
            "shard_dir": str(shard_dir),
            "expected_episodes": expected,
            "completed_or_resumed_episodes": int(perf.get("episodes", 0) or 0),
            "rows_so_far": total_rows,
            "started_unix": started_unix,
            "elapsed_s": time.perf_counter() - build_started,
            "note": "While this marker exists, an older canonical city JSONL may still be present and must not be interpreted as the current run.",
        })

    update_inprogress()

    def absorb_marker(marker: Dict[str, Any]) -> None:
        nonlocal total_rows, total_complete, total_eligible
        rows = int(marker.get("rows", 0) or 0)
        total_rows += rows
        total_complete += int(marker.get("paper_evidence_complete", 0) or 0)
        total_eligible += int(marker.get("paper_eligible", 0) or 0)
        rows_per_episode.append(rows)
        for key in CORE:
            missing[key] += int((marker.get("missing_core_counts") or {}).get(key, 0) or 0)

    for scene in scene_iter:
        eid = str(scene.episode_id)
        episode_started = time.perf_counter()
        shard = shard_dir / f"{eid}.jsonl"
        marker_path = shard_dir / f"{eid}.build.json"
        shard_paths.append(shard)
        episode_build_fp = _episode_pudo_fingerprint(global_build_fp, graph_dir, eid)

        if args.resume and shard.exists() and marker_path.exists():
            try:
                marker = json.loads(marker_path.read_text(encoding="utf-8"))
            except Exception:
                marker = {}
            if (marker.get("status") == "PASS" and marker.get("build_version") == PUDO_BUILD_VERSION
                    and marker.get("build_fingerprint") == episode_build_fp):
                perf["episodes"] += 1
                perf["resumed_episodes"] += 1
                absorb_marker(marker)
                if perf["episodes"] % 25 == 0:
                    update_inprogress()
                if hasattr(scene_iter, "set_postfix"):
                    scene_iter.set_postfix(rows=total_rows, resumed=perf["resumed_episodes"], refresh=False)
                continue

        episode_rows: List[Dict[str, Any]] = []
        existing: set[Tuple[Any, Any]] = set()
        for row in normalized_by_episode.get(eid, []):
            rr = _annotate_paper_flags(row)
            key = (rr.get("episode_id"), rr.get("anchor_id"))
            if key not in existing:
                episode_rows.append(rr)
                existing.add(key)

        t0 = time.perf_counter()
        graph = _load_graph(graph_dir, eid)
        graph_load_s = time.perf_counter() - t0
        perf["graph_load_s"] += graph_load_s
        route = scene.route_polyline if scene else []
        route_index = PolylineDistanceIndex(route)

        t0 = time.perf_counter()
        graph_index = GraphSpatialIndex(graph)
        graph_index_s = time.perf_counter() - t0
        perf["graph_index_s"] += graph_index_s

        # Candidate selection is separated from evidence matching so expensive
        # route/blockage calculations can be vectorized once per episode.
        t0 = time.perf_counter()
        external_selected: List[Tuple[Dict[str, Any], float, float, int, float]] = []
        if prepared_external:
            if getattr(scene, "bbox", None) is not None:
                xmin, ymin, xmax, ymax = scene.bbox
                mask = (
                    (prepared_external_xy[:, 0] >= float(xmin)) &
                    (prepared_external_xy[:, 0] <= float(xmax)) &
                    (prepared_external_xy[:, 1] >= float(ymin)) &
                    (prepared_external_xy[:, 1] <= float(ymax))
                )
                ext_idx = np.flatnonzero(mask)
            else:
                ext_idx = np.arange(len(prepared_external), dtype=np.int64)
            if ext_idx.size:
                ext_dist = route_index.distances(prepared_external_xy[ext_idx]) if route else np.zeros(len(ext_idx), dtype=np.float64)
                for pos, dist in zip(ext_idx.tolist(), ext_dist.tolist()):
                    if not route or float(dist) <= args.candidate_radius_m:
                        candidate, x, y, candidate_index = prepared_external[int(pos)]
                        external_selected.append((candidate, x, y, candidate_index, float(dist)))
        external_prefilter_s = time.perf_counter() - t0
        perf["external_prefilter_s"] += external_prefilter_s

        t0 = time.perf_counter()
        graph_candidates = _candidate_nodes(
            graph, route, args.candidate_radius_m,
            max_fallback=args.max_fallback_graph_candidates_per_episode,
            fallback_spacing_m=args.fallback_candidate_spacing_m,
            route_index=route_index,
        )
        candidate_select_s = time.perf_counter() - t0
        perf["candidate_select_s"] += candidate_select_s

        # Compute dynamic blockage for all selected points together. This is the
        # same timestep occupancy definition as _blockage_from_agents, only the
        # point/object distance kernel is vectorized.
        t0 = time.perf_counter()
        occupancy = TemporalOccupancyIndex(scene.metadata if scene else {})
        all_points = [(x, y) for _, x, y, _, _ in external_selected] + [(n.x, n.y) for n, _, _ in graph_candidates]
        blockage_values = occupancy.query_many(all_points, radius=6.0) if all_points else np.empty((0,), dtype=np.float64)
        ext_blockage = blockage_values[:len(external_selected)]
        graph_blockage = blockage_values[len(external_selected):]
        blockage_s = time.perf_counter() - t0
        perf["blockage_batch_s"] += blockage_s

        t0 = time.perf_counter()
        external_match_s = 0.0
        external_row_s = 0.0
        for ext_pos, (candidate, x, y, candidate_index, route_distance) in enumerate(external_selected):
            tm = time.perf_counter()
            nearest_ped, ped_dist = graph_index.nearest_node_within(
                x, y, {"sidewalk", "crossing", "entrance"}, args.pedestrian_snap_tolerance_m
            )
            ped_id = nearest_ped.node_id if nearest_ped is not None else None
            attrs = graph_index.nearest_edge_attrs(x, y) if ped_id else {}
            reg = reg_index.nearest(x, y, args.regulation_snap_tolerance_m)
            inv = inventory_index.nearest(x, y, args.inventory_snap_tolerance_m, with_distance=True)
            external_match_s += time.perf_counter() - tm

            tr = time.perf_counter()
            legal = _bool((reg or {}).get("legal_stop", (reg or {}).get("stopping_allowed", (reg or {}).get("regulation"))), False)
            blockage = float(ext_blockage[ext_pos]) if ext_pos < len(ext_blockage) else 0.0
            candidate_view = _row_view(candidate)
            candidate_source = str(candidate_view.get("source") or args.source_name)
            candidate_id = str(candidate_view.get("regulation_id") or candidate_view.get("anchor_id") or candidate_view.get("id") or f"external_{candidate_index:05d}")
            width = _coalesce((inv or {}).get("sidewalk_width_m"), attrs.get("sidewalk_width_m"))
            candidate_side = _coalesce(candidate_view.get("side"), (inv or {}).get("side"))
            if candidate_side is None:
                candidate_side = route_index.distance_and_side(x, y)[1] if route else "unknown"
            row = {
                "anchor_id": f"{eid}:candidate:{candidate_id}", "pudo_id": f"{eid}:candidate:{candidate_id}",
                "episode_id": eid, "kind": "pickup_dropoff",
                "curb_pose": {"x": x, "y": y, "heading": 0.0, "frame": "map"},
                "stop_pose": {"x": x, "y": y, "heading": 0.0, "frame": "map"},
                "x": x, "y": y,
                "side": str(candidate_side),
                "legal_stop": legal,
                "legal_stop_source": str((reg or {}).get("source") or (reg or {}).get("regulation_id") or "no_matching_regulation_fail_closed"),
                "legal_stop_authoritative": bool((reg or {}).get("authoritative") is True or (reg or {}).get("audited") is True or str((reg or {}).get("evidence_tier") or "").lower().startswith("a_") or _legacy_manual_audit_source((reg or {}).get("source"))),
                "adjacent_ped_node_id": ped_id,
                "pedestrian_match_distance_m": float(ped_dist) if ped_id else None,
                "curb_height_m": (inv or {}).get("curb_height_m"),
                "sidewalk_width_m": width,
                "deployment_clearance_m": (inv or {}).get("deployment_clearance_m"),
                "blockage_risk": blockage,
                "map_confidence": min(float(candidate_view.get("confidence", 0.6) or 0.6), float((inv or {}).get("confidence", 1.0) or 1.0), float((reg or {}).get("confidence", 1.0) or 1.0)),
                "dynamic_confidence": 1.0 - blockage,
                "lighting": attrs.get("lighting"), "shelter": attrs.get("shelter"),
                "candidate_source": candidate_source, "candidate_only": True,
                "candidate_selection": "external", "candidate_route_distance_m": float(route_distance),
                "curb_inventory_source": (inv or {}).get("source"),
                "curb_inventory_authoritative": bool((inv or {}).get("authoritative")),
                "curb_inventory_core_fields": list((inv or {}).get("core_fields") or []),
                "curb_inventory_match_distance_m": (inv or {}).get("distance_m"),
                "source": candidate_source,
                "evidence_notes": "external_candidate_only; legality and board/alight interface require independent matched evidence",
            }
            key = (eid, row["anchor_id"])
            if key not in existing:
                episode_rows.append(_annotate_paper_flags(row)); existing.add(key)
            external_row_s += time.perf_counter() - tr
        external_s = time.perf_counter() - t0 + external_prefilter_s
        perf["external_candidate_s"] += external_s

        t0 = time.perf_counter()
        graph_match_s = 0.0
        graph_row_s = 0.0
        meta_attrs = graph.metadata.get("node_attributes", {}) if isinstance(graph.metadata, dict) else {}
        ped_kinds = {"sidewalk", "crossing", "entrance"}
        for idx, (n, route_distance, selection) in enumerate(graph_candidates):
            perf["explicit_graph_candidates" if selection == "explicit" else "fallback_graph_candidates"] += 1
            anchor_id = f"{eid}:pudo_{idx:04d}"
            if (eid, anchor_id) in existing:
                continue

            tm = time.perf_counter()
            attrs = graph_index.nearest_edge_attrs(n.x, n.y)
            nattrs = meta_attrs.get(n.node_id, {}) if isinstance(meta_attrs, dict) else {}
            reg = reg_index.nearest(n.x, n.y, args.regulation_snap_tolerance_m)
            inv = inventory_index.nearest(n.x, n.y, args.inventory_snap_tolerance_m, with_distance=True)
            # For a fallback sidewalk/crossing/entrance candidate, the old
            # global nearest-node query always returned the candidate itself at
            # distance zero. Preserve that exact result without any search.
            if n.kind in ped_kinds:
                nearest_ped, ped_dist = n, 0.0
            else:
                nearest_ped, ped_dist = graph_index.nearest_node_within(
                    n.x, n.y, ped_kinds, args.pedestrian_snap_tolerance_m
                )
                if nearest_ped is None:
                    ped_dist = float("inf")
            graph_match_s += time.perf_counter() - tm

            tr = time.perf_counter()
            legal = _bool((reg or {}).get("legal_stop", (reg or {}).get("stopping_allowed", (reg or {}).get("regulation"))), False)
            blockage = float(graph_blockage[idx]) if idx < len(graph_blockage) else 0.0
            width = _coalesce((inv or {}).get("sidewalk_width_m"), nattrs.get("width_m"), attrs.get("sidewalk_width_m"))
            clearance = _coalesce((inv or {}).get("deployment_clearance_m"), nattrs.get("deployment_clearance_m"))
            curb_height = _coalesce((inv or {}).get("curb_height_m"), nattrs.get("curb_height_m"))
            confidence_terms = [float(n.confidence), float((reg or {}).get("confidence", 1.0) or 1.0)]
            if inv:
                confidence_terms.append(float(inv.get("confidence", 1.0) or 1.0))
            candidate_side = _coalesce(nattrs.get("route_side"), (inv or {}).get("side"))
            if candidate_side is None:
                candidate_side = route_index.distance_and_side(n.x, n.y)[1] if route else "unknown"
            row = {
                "anchor_id": anchor_id, "pudo_id": anchor_id, "episode_id": eid, "kind": "pickup_dropoff",
                "curb_pose": {"x": n.x, "y": n.y, "heading": 0.0, "frame": "map"},
                "stop_pose": {"x": n.x, "y": n.y, "heading": 0.0, "frame": "map"},
                "x": n.x, "y": n.y,
                "side": str(candidate_side),
                "legal_stop": legal,
                "legal_stop_source": str((reg or {}).get("source") or (reg or {}).get("regulation_id") or "no_matching_regulation_fail_closed"),
                "legal_stop_authoritative": bool((reg or {}).get("authoritative") is True or (reg or {}).get("audited") is True or str((reg or {}).get("evidence_tier") or "").lower().startswith("a_") or _legacy_manual_audit_source((reg or {}).get("source"))),
                "adjacent_ped_node_id": nearest_ped.node_id if nearest_ped else None,
                "pedestrian_match_distance_m": None if not nearest_ped else float(ped_dist),
                "curb_height_m": curb_height, "sidewalk_width_m": width, "deployment_clearance_m": clearance,
                "blockage_risk": blockage, "map_confidence": min(confidence_terms), "dynamic_confidence": 1.0 - blockage,
                "lighting": attrs.get("lighting"), "shelter": attrs.get("shelter"),
                "candidate_node_kind": n.kind, "candidate_selection": selection,
                "candidate_route_distance_m": float(route_distance),
                "curb_inventory_source": (inv or {}).get("source"),
                "curb_inventory_authoritative": bool((inv or {}).get("authoritative")),
                "curb_inventory_core_fields": list((inv or {}).get("core_fields") or []),
                "curb_inventory_match_distance_m": (inv or {}).get("distance_m"),
                "source": args.source_name,
                "evidence_notes": "derived_from_accessibility_graph_and_city_curb_regulation; legal_stop fails closed without matched regulation",
            }
            episode_rows.append(_annotate_paper_flags(row)); existing.add((eid, anchor_id))
            graph_row_s += time.perf_counter() - tr
        graph_candidate_s = time.perf_counter() - t0 + candidate_select_s
        perf["graph_candidate_s"] += graph_candidate_s
        perf["spatial_match_s"] += external_match_s + graph_match_s
        perf["row_assembly_s"] += external_row_s + graph_row_s

        part = shard.with_suffix(shard.suffix + ".part")
        part.unlink(missing_ok=True)
        write_jsonl(part, episode_rows)
        part.replace(shard)
        ep_missing = {k: sum(1 for r in episode_rows if r.get(k) is None) for k in CORE}
        marker = {
            "status": "PASS", "episode_id": eid, "rows": len(episode_rows),
            "build_version": PUDO_BUILD_VERSION, "build_fingerprint": episode_build_fp,
            "paper_evidence_complete": sum(int(bool(r.get("paper_evidence_complete"))) for r in episode_rows),
            "paper_eligible": sum(int(bool(r.get("paper_eligible"))) for r in episode_rows),
            "missing_core_counts": ep_missing,
            "graph_nodes": len(graph.nodes), "graph_edges": len(graph.edges),
            "graph_load_s": graph_load_s, "graph_index_s": graph_index_s,
            "external_candidate_s": external_s, "graph_candidate_s": graph_candidate_s,
            "external_prefilter_s": external_prefilter_s,
            "candidate_select_s": candidate_select_s,
            "blockage_batch_s": blockage_s,
            "spatial_match_s": external_match_s + graph_match_s,
            "row_assembly_s": external_row_s + graph_row_s,
            "elapsed_s": time.perf_counter() - episode_started,
        }
        dump_json(marker_path, marker)
        absorb_marker(marker)
        perf["episodes"] += 1
        perf["rows_generated"] += len(episode_rows)
        perf["slowest_episodes"].append({"episode_id": eid, "elapsed_s": marker["elapsed_s"], "rows": len(episode_rows), "nodes": len(graph.nodes), "edges": len(graph.edges)})
        perf["slowest_episodes"] = sorted(perf["slowest_episodes"], key=lambda x: x["elapsed_s"], reverse=True)[:20]
        if perf["episodes"] % 25 == 0:
            update_inprogress()
        if hasattr(scene_iter, "set_postfix"):
            scene_iter.set_postfix(rows=total_rows, last=f"{marker['elapsed_s']:.2f}s", resumed=perf["resumed_episodes"], refresh=False)

    update_inprogress()
    # Combine only shards observed in this run; stale shards from a previous
    # different scene selection can never leak into the canonical city output.
    output.parent.mkdir(parents=True, exist_ok=True)
    combined_part = output.with_suffix(output.suffix + ".part")
    combined_part.unlink(missing_ok=True)
    with combined_part.open("w", encoding="utf-8") as dst:
        for shard in shard_paths:
            if not shard.exists():
                raise RuntimeError(f"missing PUDO shard after build: {shard}")
            with shard.open("r", encoding="utf-8") as src:
                for line in src:
                    if line.strip():
                        dst.write(line)
    combined_part.replace(output)

    perf["elapsed_s"] = time.perf_counter() - build_started
    perf["episodes_per_s"] = perf["episodes"] / max(perf["elapsed_s"], 1e-9)
    perf["shard_dir"] = str(shard_dir)
    inprogress_marker.unlink(missing_ok=True)
    episode_total = max(1, perf["episodes"])
    row_den = max(1, total_rows)
    report = {
        "status": "PASS", "rows": total_rows,
        "missing_core_counts": missing,
        "missing_core_rates": {k: v / row_den for k, v in missing.items()},
        "paper_evidence_complete": total_complete, "paper_eligible": total_eligible,
        "episode_count": perf["episodes"],
        "rows_per_episode": {
            "min": min(rows_per_episode) if rows_per_episode else 0,
            "max": max(rows_per_episode) if rows_per_episode else 0,
            "mean": (sum(rows_per_episode) / episode_total) if rows_per_episode else 0.0,
        },
        "source": args.source_name, "mode": "pudo_generator",
        "build_version": PUDO_BUILD_VERSION, "build_fingerprint": global_build_fp,
        "performance": perf,
        "interpretation": "Unknown candidates are retained. paper_eligible requires independent legality, pedestrian binding, and all three core interface fields.",
    }
    if args.fail_on_missing_core_evidence:
        bad = {k: v for k, v in report["missing_core_rates"].items() if v > args.max_core_missing_rate}
        if bad:
            raise RuntimeError(f"core PUDO evidence missing rate too high: {bad}; threshold={args.max_core_missing_rate}")
    return report


def build(args: argparse.Namespace) -> Dict[str, Any]:
    transformer = CoordinateTransformer.from_file(args.georeference_json) if args.georeference_json else None
    source_paths = [args.input_pudo_evidence_jsonl]
    if not args.accessibility_graph_dir:
        source_paths.append(args.curb_inventory_jsonl)
    normalized_input: List[Dict[str, Any]] = []
    for path in source_paths:
        for r in _read(path):
            if r.get("episode_id") or (isinstance(r.get("properties"), dict) and r["properties"].get("episode_id")):
                normalized_input.append(normalize(r, args.source_name, transformer))

    if args.accessibility_graph_dir:
        report = _build_from_graphs(args, normalized_input)
    else:
        if not normalized_input:
            raise RuntimeError("PUDO evidence build requires real curb/PUDO evidence or --accessibility_graph_dir to generate candidates; no synthetic fallback is available")
        out_rows = [_annotate_paper_flags(r) for r in normalized_input]
        total = max(1, len(out_rows))
        missing = {k: sum(1 for r in out_rows if r.get(k) is None) for k in CORE}
        if args.fail_on_missing_core_evidence:
            bad = {k: v / total for k, v in missing.items() if v / total > args.max_core_missing_rate}
            if bad:
                raise RuntimeError(f"core PUDO evidence missing rate too high: {bad}; threshold={args.max_core_missing_rate}")
        write_jsonl(args.output_pudo_evidence_jsonl, out_rows)
        by_episode: Dict[str, int] = {}
        for r in out_rows:
            eid = str(r.get("episode_id") or "unknown")
            by_episode[eid] = by_episode.get(eid, 0) + 1
        totals = list(by_episode.values())
        report = {
            "status": "PASS", "rows": len(out_rows), "missing_core_counts": missing,
            "missing_core_rates": {k: v / total for k, v in missing.items()},
            "paper_evidence_complete": sum(int(bool(r.get("paper_evidence_complete"))) for r in out_rows),
            "paper_eligible": sum(int(bool(r.get("paper_eligible"))) for r in out_rows),
            "episode_count": len(by_episode), "episode_id_sample": sorted(by_episode)[:20],
            "rows_per_episode": {
                "min": min(totals) if totals else 0, "max": max(totals) if totals else 0,
                "mean": (sum(totals) / len(totals)) if totals else 0.0,
            },
            "source": args.source_name, "mode": "pudo_validator",
            "interpretation": "Unknown candidates are retained. paper_eligible requires independent legality, pedestrian binding, and all three core interface fields.",
        }

    if args.report_json:
        dump_json(args.report_json, report)
    if args.timing_report_json:
        dump_json(args.timing_report_json, {
            "status": "PASS", "output": str(args.output_pudo_evidence_jsonl),
            "rows": int(report.get("rows", 0) or 0), "performance": report.get("performance", {}),
        })
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
    p.add_argument("--max_fallback_graph_candidates_per_episode", type=int, default=128, help="Cap only generic sidewalk/crossing/entrance fallback candidates; explicit curb candidates are never capped.")
    p.add_argument("--fallback_candidate_spacing_m", type=float, default=20.0, help="Spatial thinning grid for generic fallback PUDO candidates.")
    p.add_argument("--disable_tqdm", action="store_true", help="Disable per-episode PUDO progress.")
    p.add_argument("--timing_report_json", default=None, help="Optional compact performance report for bottleneck diagnosis.")
    p.add_argument("--resume", action="store_true", help="Resume PUDO generation from completed per-episode shards.")
    p.add_argument("--shard_dir", default=None, help="Optional directory for resumable per-episode PUDO shards.")
    p.add_argument("--source_name", default="city_curb_regulation+sidewalk_inventory")
    p.add_argument("--fail_on_missing_core_evidence", action="store_true")
    p.add_argument("--max_core_missing_rate", type=float, default=0.05)
    p.add_argument("--report_json", default=None)
    args = p.parse_args()
    print(json.dumps(build(args), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
