"""Accessibility graph generation and loading."""
from __future__ import annotations

import random
from typing import List

from capplan.data.schemas import AccessibilityEdge, AccessibilityGraph, AccessibilityNode


def synthetic_accessibility_graph(episode_id: str, seed: int = 0, n_pudo: int = 4) -> AccessibilityGraph:
    rng = random.Random(seed)
    nodes: List[AccessibilityNode] = [
        AccessibilityNode("origin", 0.0, 0.0, "entrance", 0.98),
        AccessibilityNode("destination", 100.0, 10.0, "entrance", 0.98),
    ]
    edges: List[AccessibilityEdge] = []
    for i in range(n_pudo):
        pu = f"pudo_{i}"
        x = 20.0 + 20.0 * i
        y = rng.uniform(-5, 5)
        nodes.append(AccessibilityNode(pu, x, y, "pudo", 0.85 + 0.03 * i))
        # One PUDO is deliberately challenging to create infeasible episodes.
        width = 1.6 - 0.18 * i
        slope = 0.025 + 0.015 * i
        curb = i != 2
        step_free = i != 3
        obstacle = i == 3
        conf = 0.93 - 0.05 * i
        edges.append(AccessibilityEdge(f"origin_to_{pu}", "origin", pu, 65.0 + 35.0 * i, width, slope, 0.015 + 0.005 * i, "paved", curb, step_free, obstacle, "day", i % 2 == 0, conf))
        edges.append(AccessibilityEdge(f"{pu}_to_destination", pu, "destination", 70.0 + 35.0 * (n_pudo - i), max(0.7, width - 0.05), slope, 0.015 + 0.005 * i, "paved", curb, step_free, obstacle, "day", i % 2 == 1, conf))
    return AccessibilityGraph(episode_id=episode_id, nodes=nodes, edges=edges)
