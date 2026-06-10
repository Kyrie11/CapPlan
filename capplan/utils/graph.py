from __future__ import annotations

import heapq
from collections import defaultdict
from typing import Dict, Iterable, List, Tuple


def shortest_path_lengths(edges: Iterable[Tuple[str, str, float]], source: str) -> Dict[str, float]:
    adj: Dict[str, List[Tuple[str, float]]] = defaultdict(list)
    for u, v, w in edges:
        adj[u].append((v, w))
        adj[v].append((u, w))
    dist = {source: 0.0}
    pq = [(0.0, source)]
    while pq:
        d, u = heapq.heappop(pq)
        if d != dist[u]:
            continue
        for v, w in adj.get(u, []):
            nd = d + w
            if nd < dist.get(v, float("inf")):
                dist[v] = nd
                heapq.heappush(pq, (nd, v))
    return dist
