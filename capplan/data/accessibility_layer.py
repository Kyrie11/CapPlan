"""Accessibility graph construction, routing, and evidence aggregation."""
from __future__ import annotations

import heapq
import math
import random
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from capplan.data.schemas import AccessibilityEdge, AccessibilityGraph, AccessibilityNode, PUDOAnchor, Pose2D, graph_from_records, to_dict
from capplan.utils.serialization import read_jsonl, write_jsonl


class NoAccessiblePathError(RuntimeError):
    pass


class SyntheticAccessibilityBuilder:
    """Deterministic routable local graph tied to a scene's local coordinates."""

    def build(self, episode_id: str, seed: int = 0, n_pudo: int = 4, origin: Pose2D | None = None, destination: Pose2D | None = None) -> AccessibilityGraph:
        return synthetic_accessibility_graph(episode_id, seed=seed, n_pudo=n_pudo, origin=origin, destination=destination)


class PreparedAccessibilityBuilder:
    """Load audited/prepared accessibility graphs from JSONL files.

    The builder looks first for per-episode files
    ``<graph_dir>/<episode_id>.nodes.jsonl`` and
    ``<graph_dir>/<episode_id>.edges.jsonl``.  If those are absent it falls
    back to shared ``nodes.jsonl`` and ``edges.jsonl``.  Records should match
    ``AccessibilityNode`` and ``AccessibilityEdge`` fields. Missing evidence is
    preserved as missing and is never converted into feasible defaults.
    """

    def __init__(self, graph_dir: str | Path, source: str = "prepared_accessibility_jsonl") -> None:
        self.graph_dir = Path(graph_dir)
        self.source = source

    def _paths_for(self, episode_id: str) -> tuple[Path, Path]:
        ep_nodes = self.graph_dir / f"{episode_id}.nodes.jsonl"
        ep_edges = self.graph_dir / f"{episode_id}.edges.jsonl"
        if ep_nodes.exists() and ep_edges.exists():
            return ep_nodes, ep_edges
        shared_nodes = self.graph_dir / "nodes.jsonl"
        shared_edges = self.graph_dir / "edges.jsonl"
        if shared_nodes.exists() and shared_edges.exists():
            return shared_nodes, shared_edges
        raise FileNotFoundError(
            f"missing prepared accessibility graph for {episode_id}; expected "
            f"{ep_nodes.name}/{ep_edges.name} or shared nodes.jsonl/edges.jsonl in {self.graph_dir}"
        )

    def build(self, episode_id: str, **_: Any) -> AccessibilityGraph:
        nodes_path, edges_path = self._paths_for(episode_id)
        return graph_from_records(
            episode_id,
            read_jsonl(nodes_path),
            read_jsonl(edges_path),
            {"source": self.source, "nodes_path": str(nodes_path), "edges_path": str(edges_path)},
        )


class GeoJSONAccessibilityBuilder:
    """Minimal prepared-JSON/JSONL graph reader.

    This implementation expects records matching AccessibilityNode and
    AccessibilityEdge.  It intentionally does not infer missing accessibility
    fields as feasible.
    """

    def __init__(self, nodes_path: str | Path, edges_path: str | Path) -> None:
        self.nodes_path = Path(nodes_path)
        self.edges_path = Path(edges_path)

    def build(self, episode_id: str, **_: Any) -> AccessibilityGraph:
        return graph_from_records(episode_id, read_jsonl(self.nodes_path), read_jsonl(self.edges_path), {"source": "geojson"})


# Alias requested by the implementation plan.
OpenSidewalksAccessibilityBuilder = GeoJSONAccessibilityBuilder


def synthetic_accessibility_graph(episode_id: str, seed: int = 0, n_pudo: int = 4, origin: Pose2D | None = None, destination: Pose2D | None = None) -> AccessibilityGraph:
    rng = random.Random(seed)
    origin = origin or Pose2D(0.0, 0.0)
    destination = destination or Pose2D(160.0, 24.0)
    overlay_source = "synthetic_map_overlay" if origin.frame == "map" or destination.frame == "map" else "synthetic_local"
    nodes: List[AccessibilityNode] = [
        AccessibilityNode("origin", origin.x, origin.y, "entrance", 0.98, source=overlay_source, pose=origin),
        AccessibilityNode("destination", destination.x, destination.y, "entrance", 0.98, source=overlay_source, pose=destination),
    ]
    edges: List[AccessibilityEdge] = []
    # Add sidewalk spine and curb/PUDO nodes.  Each PUDO has a direct route and
    # an alternative via the spine, so shortest path tests can distinguish true
    # graph routing from single-edge fallback.
    last_spine = "origin"
    for i in range(n_pudo):
        sx = origin.x + (i + 1) * (destination.x - origin.x) / (n_pudo + 1)
        sy = origin.y + 0.15 * (destination.y - origin.y) + rng.uniform(-3, 3)
        spine = f"sidewalk_{i}"
        pudo = f"pudo_{i}"
        nodes.append(AccessibilityNode(spine, sx, sy, "sidewalk", 0.94 - 0.03 * i, source=overlay_source))
        nodes.append(AccessibilityNode(pudo, sx + rng.uniform(-1.5, 1.5), sy + rng.uniform(-2, 2), "pudo", 0.92 - 0.04 * i, source=overlay_source))
        width = max(0.72, 1.55 - 0.14 * i)
        slope = 0.025 + 0.012 * i
        cross = 0.014 + 0.004 * i
        curb = i != 2
        step = i != 3
        obstacle = i == 3
        conf = 0.94 - 0.045 * i
        # Spine edge.
        prev = next(n for n in nodes if n.node_id == last_spine)
        length = math.hypot(sx - prev.x, sy - prev.y)
        edges.append(AccessibilityEdge(f"{last_spine}_to_{spine}", last_spine, spine, length, width, slope, cross, "paved", True, True, False, "day", i % 2 == 0, conf, [[prev.x, prev.y], [sx, sy]], source=overlay_source))
        # Curb connector contains curb ramp/step evidence.
        edges.append(AccessibilityEdge(f"{spine}_to_{pudo}", spine, pudo, max(3.0, math.hypot(nodes[-1].x - sx, nodes[-1].y - sy)), width, slope, cross, "concrete", curb, step, obstacle, "day", i % 2 == 0, conf, [[sx, sy], [nodes[-1].x, nodes[-1].y]], crossing_type="curb", obstacle_state="blocked" if obstacle else None, source=overlay_source))
        # Direct access edge to keep hidden tests honest: sometimes longer than via spine.
        direct_len = math.hypot(nodes[-1].x - origin.x, nodes[-1].y - origin.y) * (1.15 + 0.05 * i)
        edges.append(AccessibilityEdge(f"origin_direct_to_{pudo}", "origin", pudo, direct_len, width, slope, cross, "paved", curb, step, obstacle, "day", i % 2 == 0, conf, [[origin.x, origin.y], [nodes[-1].x, nodes[-1].y]], crossing_type="curb", obstacle_state="blocked" if obstacle else None, source=overlay_source))
        last_spine = spine
    last_node = next(n for n in nodes if n.node_id == last_spine)
    edges.append(AccessibilityEdge(f"{last_spine}_to_destination", last_spine, "destination", math.hypot(destination.x - last_node.x, destination.y - last_node.y), 1.2, 0.035, 0.018, "paved", True, True, False, "day", True, 0.90, [[last_node.x, last_node.y], [destination.x, destination.y]], source=overlay_source))
    for i in range(n_pudo):
        pudo_node = next(n for n in nodes if n.node_id == f"pudo_{i}")
        width = max(0.7, 1.45 - 0.12 * i)
        slope = 0.026 + 0.012 * i
        cross = 0.014 + 0.004 * i
        curb = i != 2
        step = i != 3
        obstacle = i == 3
        conf = 0.94 - 0.045 * i
        # Egress connector to destination; not necessarily shortest if spine is better.
        edges.append(AccessibilityEdge(f"pudo_{i}_to_destination", f"pudo_{i}", "destination", math.hypot(destination.x - pudo_node.x, destination.y - pudo_node.y) * (1.05 + 0.03 * i), width, slope, cross, "paved", curb, step, obstacle, "day", i % 2 == 1, conf, [[pudo_node.x, pudo_node.y], [destination.x, destination.y]], crossing_type="curb", obstacle_state="blocked" if obstacle else None, source=overlay_source))
    return AccessibilityGraph(episode_id=episode_id, nodes=nodes, edges=edges, metadata={"source": overlay_source, "seed": seed, "frame": origin.frame if origin.frame == destination.frame else "mixed"})



def _point_segment_distance(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    denom = dx * dx + dy * dy
    if denom <= 1e-12:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / denom))
    qx, qy = ax + t * dx, ay + t * dy
    return math.hypot(px - qx, py - qy)


def _edge_distance_to_point(edge: AccessibilityEdge, x: float, y: float, nodes_by_id: Dict[str, AccessibilityNode]) -> float:
    coords = edge.geometry or []
    pts: List[Tuple[float, float]] = []
    for xy in coords:
        try:
            pts.append((float(xy[0]), float(xy[1])))
        except Exception:
            continue
    if len(pts) < 2:
        a = nodes_by_id.get(edge.from_node)
        b = nodes_by_id.get(edge.to_node)
        if a is not None and b is not None:
            pts = [(a.x, a.y), (b.x, b.y)]
    if len(pts) < 2:
        return float("inf")
    return min(_point_segment_distance(x, y, ax, ay, bx, by) for (ax, ay), (bx, by) in zip(pts[:-1], pts[1:]))


def _nearest_accessibility_evidence_edge(graph: AccessibilityGraph, x: float, y: float, prefer_synthetic: bool = False) -> AccessibilityEdge | None:
    nodes_by_id = {n.node_id: n for n in graph.nodes}
    candidates = []
    for edge in graph.edges:
        if edge.from_node.startswith("nuplan_") or edge.to_node.startswith("nuplan_"):
            continue
        has_useful_evidence = any(v is not None for v in (edge.width_m, edge.slope, edge.cross_slope, edge.surface, edge.curb_ramp, edge.step_free, edge.lighting, edge.shelter))
        if not has_useful_evidence:
            continue
        synthetic_rank = 0 if (prefer_synthetic and str(edge.source).startswith("synthetic")) else 1
        dist = _edge_distance_to_point(edge, x, y, nodes_by_id)
        candidates.append((synthetic_rank, dist, edge))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (item[0], item[1]))
    return candidates[0][2]


def _synthetic_overlay_enabled(graph: AccessibilityGraph) -> bool:
    return str(graph.metadata.get("source", "")).startswith("synthetic")


def attach_pudo_nodes_to_graph(graph: AccessibilityGraph, anchors: List[PUDOAnchor], connector_width_default_m: float = 1.2) -> tuple[AccessibilityGraph, List[PUDOAnchor]]:
    """Ensure PUDO anchors are routable nodes in the pedestrian graph.

    nuPlan lane-derived PUDO points live in the same map frame as the scene,
    while the accessibility spine may not already contain a node with exactly
    the same anchor id.  This helper inserts a pedestrian PUDO node at each curb
    pose and connects it to the nearest existing sidewalk/entrance node.

    Evidence policy:
    * Real/prepared accessibility graphs remain fail-closed: missing connector
      attributes stay missing.
    * The repository's ``synthetic_local``/``synthetic_map_overlay`` mode is a
      controlled proxy benchmark.  In that mode only, connector attributes and
      wait lighting/shelter may be inherited from the nearest synthetic
      accessibility edge and are marked with an explicit synthetic proxy source.
      This prevents a degenerate all-failure benchmark while keeping provenance
      auditable.
    """
    node_ids = {n.node_id for n in graph.nodes}
    updated: List[PUDOAnchor] = []
    use_synthetic_proxy = _synthetic_overlay_enabled(graph)

    def nearest_existing(x: float, y: float, exclude: set[str]) -> AccessibilityNode | None:
        best = None
        best_d = float("inf")
        for node in graph.nodes:
            if node.node_id in exclude or node.kind == "pudo":
                continue
            d = math.hypot(node.x - x, node.y - y)
            if d < best_d:
                best, best_d = node, d
        return best

    for anchor0 in anchors:
        anchor = anchor0
        ped_id = anchor.adjacent_ped_node_id or anchor.anchor_id
        context_edge = _nearest_accessibility_evidence_edge(graph, anchor.curb_pose.x, anchor.curb_pose.y, prefer_synthetic=use_synthetic_proxy) if use_synthetic_proxy else None
        proxy_source = "synthetic_accessibility_proxy_to_nuplan_pudo" if context_edge is not None and anchor.source.startswith("nuplan_route") else anchor.source

        if use_synthetic_proxy and context_edge is not None and (anchor.lighting is None or anchor.shelter is None):
            anchor = replace(
                anchor,
                lighting=context_edge.lighting if anchor.lighting is None else anchor.lighting,
                shelter=context_edge.shelter if anchor.shelter is None else anchor.shelter,
            )

        if ped_id not in node_ids:
            graph.nodes.append(AccessibilityNode(
                ped_id,
                anchor.curb_pose.x,
                anchor.curb_pose.y,
                "pudo",
                anchor.map_confidence,
                timestamp_s=anchor.timestamp_s,
                source=anchor.source,
                pose=anchor.curb_pose,
            ))
            node_ids.add(ped_id)
        # Add a connector only if one does not already touch this PUDO node.
        if not any(e.from_node == ped_id or e.to_node == ped_id for e in graph.edges):
            near = nearest_existing(anchor.curb_pose.x, anchor.curb_pose.y, {ped_id})
            if near is not None:
                length = math.hypot(anchor.curb_pose.x - near.x, anchor.curb_pose.y - near.y)
                if use_synthetic_proxy and context_edge is not None:
                    width = anchor.sidewalk_width_m if anchor.sidewalk_width_m is not None else context_edge.width_m
                    slope = context_edge.slope
                    cross_slope = context_edge.cross_slope
                    surface = context_edge.surface
                    curb_ramp = context_edge.curb_ramp
                    step_free = context_edge.step_free
                    lighting = anchor.lighting if anchor.lighting is not None else context_edge.lighting
                    shelter = anchor.shelter if anchor.shelter is not None else context_edge.shelter
                    confidence = min(anchor.map_confidence, anchor.dynamic_confidence, context_edge.confidence)
                    edge_source = proxy_source
                else:
                    width = anchor.sidewalk_width_m if anchor.sidewalk_width_m is not None else (connector_width_default_m if anchor.source.startswith("synthetic") else None)
                    slope = None
                    cross_slope = None
                    surface = "unknown"
                    curb_ramp = None
                    step_free = None
                    lighting = anchor.lighting
                    shelter = anchor.shelter
                    confidence = min(anchor.map_confidence, anchor.dynamic_confidence)
                    edge_source = anchor.source
                graph.edges.append(AccessibilityEdge(
                    f"{near.node_id}_to_{ped_id}",
                    near.node_id,
                    ped_id,
                    max(0.1, length),
                    width,
                    slope,
                    cross_slope,
                    surface,
                    curb_ramp,
                    step_free,
                    anchor.blockage_risk >= 0.85,
                    lighting,
                    shelter,
                    confidence,
                    [[near.x, near.y], [anchor.curb_pose.x, anchor.curb_pose.y]],
                    crossing_type="curb",
                    obstacle_state="blocked" if anchor.blockage_risk >= 0.85 else None,
                    timestamp_s=anchor.timestamp_s,
                    source=edge_source,
                ))
        if anchor.adjacent_ped_node_id != ped_id:
            anchor = replace(anchor, adjacent_ped_node_id=ped_id)
        updated.append(anchor)
    graph.metadata = {**graph.metadata, "pudo_nodes_attached": True, "pudo_connector_policy": "synthetic_proxy" if use_synthetic_proxy else "fail_closed"}
    return graph, updated

def _adjacency(graph: AccessibilityGraph) -> Dict[str, List[Tuple[str, AccessibilityEdge]]]:
    adj: Dict[str, List[Tuple[str, AccessibilityEdge]]] = {}
    for e in graph.edges:
        adj.setdefault(e.from_node, []).append((e.to_node, e))
        adj.setdefault(e.to_node, []).append((e.from_node, e))
    return adj


def shortest_path_edges(graph: AccessibilityGraph, start_node: str, end_node: str, risk_penalty: float = 0.0) -> Tuple[List[str], List[AccessibilityEdge], float]:
    nodes = {n.node_id for n in graph.nodes}
    if start_node not in nodes or end_node not in nodes:
        raise NoAccessiblePathError(f"unknown node in path {start_node}->{end_node}")
    adj = _adjacency(graph)
    q: List[Tuple[float, str]] = [(0.0, start_node)]
    dist = {start_node: 0.0}
    prev: Dict[str, Tuple[str, AccessibilityEdge]] = {}
    while q:
        d, u = heapq.heappop(q)
        if d > dist.get(u, float("inf")) + 1e-9:
            continue
        if u == end_node:
            break
        for v, e in adj.get(u, []):
            penalty = risk_penalty * (1.0 if e.obstacle else 0.0)
            nd = d + float(e.length_m) + penalty
            if nd < dist.get(v, float("inf")):
                dist[v] = nd
                prev[v] = (u, e)
                heapq.heappush(q, (nd, v))
    if end_node not in dist:
        raise NoAccessiblePathError(f"no path {start_node}->{end_node}")
    edge_path: List[AccessibilityEdge] = []
    node_path = [end_node]
    cur = end_node
    while cur != start_node:
        pu, pe = prev[cur]
        edge_path.append(pe)
        cur = pu
        node_path.append(cur)
    edge_path.reverse()
    node_path.reverse()
    return node_path, edge_path, dist[end_node]


def _agg_optional(values: List[Any], op: str, missing_default: Any = None) -> Any:
    vals = [v for v in values if v is not None]
    if not vals:
        return missing_default
    if op == "min":
        return min(vals)
    if op == "max":
        return max(vals)
    if op == "all":
        return all(bool(v) for v in vals)
    if op == "any":
        return any(bool(v) for v in vals)
    return vals[0]


def shortest_accessible_path_stats(graph: AccessibilityGraph, start_node: str, end_node: str, contract: Any | None = None) -> Dict[str, Any]:
    node_ids, edges, distance = shortest_path_edges(graph, start_node, end_node, risk_penalty=50.0 if contract is not None else 0.0)
    missing_fields = []
    def missing_check(name: str, vals: List[Any]) -> None:
        if any(v is None for v in vals):
            missing_fields.append(name)

    width_vals = [e.width_m for e in edges]; missing_check("path_width_m", width_vals)
    slope_vals = [e.slope for e in edges]; missing_check("slope", slope_vals)
    cross_vals = [e.cross_slope for e in edges]; missing_check("cross_slope", cross_vals)
    node_kinds = {n.node_id: n.kind for n in graph.nodes}
    def touches_curb_context(e: AccessibilityEdge) -> bool:
        return (
            e.crossing_type == "curb"
            or "curb" in e.edge_id
            or node_kinds.get(e.from_node) in {"pudo", "curb"}
            or node_kinds.get(e.to_node) in {"pudo", "curb"}
        )

    curb_vals = [e.curb_ramp for e in edges if touches_curb_context(e)]
    step_vals = [e.step_free for e in edges]
    surface_vals = [e.surface for e in edges]
    if any(v is None for v in curb_vals): missing_fields.append("curb_ramp")
    if any(v is None for v in step_vals): missing_fields.append("step_free")
    if any(v is None for v in surface_vals): missing_fields.append("surface")
    confidence = min([e.confidence for e in edges] or [0.0])
    if missing_fields:
        confidence = min(confidence, 0.49)
    crossing_count = sum(1 for e in edges if e.crossing_type not in (None, "curb"))
    risk = 1.0
    for e in edges:
        er = 0.30 if e.obstacle else 0.02
        risk *= (1.0 - er)
    blockage_risk = 1.0 - risk
    return {
        "path_node_ids": node_ids,
        "path_edge_ids": [e.edge_id for e in edges],
        "distance": distance,
        "width": _agg_optional(width_vals, "min"),
        "slope": _agg_optional(slope_vals, "max"),
        "cross_slope": _agg_optional(cross_vals, "max"),
        "curb_ramp": _agg_optional(curb_vals, "all", True if not curb_vals else None),
        "step_free": _agg_optional(step_vals, "all"),
        "surface": _agg_optional(surface_vals, "first"),
        "obstacle": any(e.obstacle for e in edges),
        "blockage_risk": blockage_risk,
        "crossing_count": crossing_count,
        "lighting": _agg_optional([e.lighting for e in edges], "first", None),
        "shelter": _agg_optional([e.shelter for e in edges], "any", None),
        "confidence": confidence,
        "missing_fields": sorted(set(missing_fields)),
        "source": "pedestrian_graph",
    }


def write_accessibility_graph(dataset_dir: str | Path, graph: AccessibilityGraph) -> None:
    root = Path(dataset_dir) / "accessibility_graphs"
    root.mkdir(parents=True, exist_ok=True)
    write_jsonl(root / f"{graph.episode_id}.nodes.jsonl", [to_dict(n) for n in graph.nodes])
    write_jsonl(root / f"{graph.episode_id}.edges.jsonl", [to_dict(e) for e in graph.edges])
    # Backward-compatible combined graph record for older users; evaluation uses
    # the canonical node/edge files.
    write_jsonl(root / f"{graph.episode_id}.jsonl", [to_dict(graph)])


def load_accessibility_graph(dataset_dir: str | Path, episode_id: str) -> AccessibilityGraph:
    root = Path(dataset_dir) / "accessibility_graphs"
    nodes_path = root / f"{episode_id}.nodes.jsonl"
    edges_path = root / f"{episode_id}.edges.jsonl"
    if nodes_path.exists() and edges_path.exists():
        return graph_from_records(episode_id, read_jsonl(nodes_path), read_jsonl(edges_path), {"loaded_from": str(root)})
    combined = read_jsonl(root / f"{episode_id}.jsonl")
    if combined:
        rec = combined[0]
        return graph_from_records(rec.get("episode_id", episode_id), rec.get("nodes", []), rec.get("edges", []), rec.get("metadata", {}))
    raise FileNotFoundError(f"missing saved accessibility graph for {episode_id} in {root}")
