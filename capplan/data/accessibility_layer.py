"""Accessibility graph construction, routing, and evidence aggregation."""
from __future__ import annotations

import heapq
import math
import random
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

from capplan.data.schemas import AccessibilityEdge, AccessibilityGraph, AccessibilityNode, Pose2D, graph_from_records, to_dict
from capplan.utils.serialization import read_jsonl, write_jsonl


class NoAccessiblePathError(RuntimeError):
    pass


class SyntheticAccessibilityBuilder:
    """Deterministic routable local graph tied to a scene's local coordinates."""

    def build(self, episode_id: str, seed: int = 0, n_pudo: int = 4, origin: Pose2D | None = None, destination: Pose2D | None = None) -> AccessibilityGraph:
        return synthetic_accessibility_graph(episode_id, seed=seed, n_pudo=n_pudo, origin=origin, destination=destination)


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
    nodes: List[AccessibilityNode] = [
        AccessibilityNode("origin", origin.x, origin.y, "entrance", 0.98, source="synthetic_service_overlay"),
        AccessibilityNode("destination", destination.x, destination.y, "entrance", 0.98, source="synthetic_service_overlay"),
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
        nodes.append(AccessibilityNode(spine, sx, sy, "sidewalk", 0.94 - 0.03 * i, source="synthetic_local"))
        nodes.append(AccessibilityNode(pudo, sx + rng.uniform(-1.5, 1.5), sy + rng.uniform(-2, 2), "pudo", 0.92 - 0.04 * i, source="synthetic_local"))
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
        edges.append(AccessibilityEdge(f"{last_spine}_to_{spine}", last_spine, spine, length, width, slope, cross, "paved", True, True, False, "day", i % 2 == 0, conf, [[prev.x, prev.y], [sx, sy]], source="synthetic_local"))
        # Curb connector contains curb ramp/step evidence.
        edges.append(AccessibilityEdge(f"{spine}_to_{pudo}", spine, pudo, max(3.0, math.hypot(nodes[-1].x - sx, nodes[-1].y - sy)), width, slope, cross, "concrete", curb, step, obstacle, "day", i % 2 == 0, conf, [[sx, sy], [nodes[-1].x, nodes[-1].y]], crossing_type="curb", obstacle_state="blocked" if obstacle else None, source="synthetic_local"))
        # Direct access edge to keep hidden tests honest: sometimes longer than via spine.
        direct_len = math.hypot(nodes[-1].x - origin.x, nodes[-1].y - origin.y) * (1.15 + 0.05 * i)
        edges.append(AccessibilityEdge(f"origin_direct_to_{pudo}", "origin", pudo, direct_len, width, slope, cross, "paved", curb, step, obstacle, "day", i % 2 == 0, conf, [[origin.x, origin.y], [nodes[-1].x, nodes[-1].y]], source="synthetic_local"))
        last_spine = spine
    last_node = next(n for n in nodes if n.node_id == last_spine)
    edges.append(AccessibilityEdge(f"{last_spine}_to_destination", last_spine, "destination", math.hypot(destination.x - last_node.x, destination.y - last_node.y), 1.2, 0.035, 0.018, "paved", True, True, False, "day", True, 0.90, [[last_node.x, last_node.y], [destination.x, destination.y]], source="synthetic_local"))
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
        edges.append(AccessibilityEdge(f"pudo_{i}_to_destination", f"pudo_{i}", "destination", math.hypot(destination.x - pudo_node.x, destination.y - pudo_node.y) * (1.05 + 0.03 * i), width, slope, cross, "paved", curb, step, obstacle, "day", i % 2 == 1, conf, [[pudo_node.x, pudo_node.y], [destination.x, destination.y]], source="synthetic_local"))
    return AccessibilityGraph(episode_id=episode_id, nodes=nodes, edges=edges, metadata={"source": "synthetic_local", "seed": seed})


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
    curb_vals = [e.curb_ramp for e in edges if e.crossing_type == "curb" or "curb" in e.edge_id]
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
