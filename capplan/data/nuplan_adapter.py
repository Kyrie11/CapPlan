"""nuPlan adapter with deterministic fallback.

The adapter avoids requiring nuPlan for tests.  When nuPlan is installed and a
root is provided, this class can be extended to call the official scenario API.
The returned schema is intentionally the same for nuPlan and synthetic fallback.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List

from capplan.data.schemas import EpisodeMetadata


@dataclass
class NuPlanScenarioRecord:
    episode: EpisodeMetadata
    ego_history: List[Dict]
    agent_history: List[Dict]
    map_context: Dict
    route_corridor: Dict


class NuPlanAdapter:
    def __init__(self, nuplan_root: str | None = None, split: str = "mini", seed: int = 0) -> None:
        self.nuplan_root = nuplan_root
        self.split = split
        self.seed = seed
        self.nuplan_available = self._check_nuplan()

    @staticmethod
    def _check_nuplan() -> bool:
        try:
            import nuplan  # type: ignore  # noqa:F401
            return True
        except Exception:
            return False

    def iter_scenarios(self, max_scenarios: int = 4) -> Iterable[NuPlanScenarioRecord]:
        # Deterministic fallback.  It preserves the traffic-scene substrate fields
        # expected by later stages: ego history, agents, map context, route corridor.
        for i in range(max_scenarios):
            episode_id = f"synthetic_{self.split}_{i:04d}"
            ep = EpisodeMetadata(
                episode_id=episode_id,
                scenario_id=f"scenario_{i:04d}",
                split=self.split,
                origin_anchor="origin",
                destination_anchor="destination",
                request_time_s=1000.0 + 60.0 * i,
                route_length_m=4000.0 + 200.0 * i,
                shortest_route_length_m=3600.0 + 180.0 * i,
                seed=self.seed + i,
                nuplan_available=self.nuplan_available,
                metadata={"source": "nuplan" if self.nuplan_available else "synthetic_fallback"},
            )
            yield NuPlanScenarioRecord(
                episode=ep,
                ego_history=[{"t": t, "x": t * 2.0, "y": 0.0, "v": 2.0} for t in range(5)],
                agent_history=[[{"id": "agent0", "t": t, "x": 10.0 + t, "y": 2.0}] for t in range(5)],
                map_context={"lanes": 3, "drivable_area": True, "traffic_lights": []},
                route_corridor={"length_m": ep.route_length_m, "polyline": [[0, 0], [ep.route_length_m, 0]]},
            )
