"""nuPlan adapter with explicit scene-source modes.

Real nuPlan mode never falls back to synthetic data.  Synthetic mode is provided
only for deterministic smoke tests and marks records as ``source='synthetic'``.
"""
from __future__ import annotations

import glob
import hashlib
import importlib.util
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Sequence

from capplan.data.schemas import EpisodeMetadata, Pose2D, SceneRecord


@dataclass
class NuPlanScenarioRecord:
    episode: EpisodeMetadata
    scene: SceneRecord
    ego_history: List[Dict]
    agent_history: List[Dict]
    map_context: Dict
    route_corridor: Dict


def _split_path_list(value: str | Sequence[str] | None) -> List[str]:
    """Split CLI-style nuPlan DB inputs into path-like tokens.

    Supported forms:
    - one absolute/relative path string;
    - comma-separated paths;
    - plus-separated paths, e.g. ``train_boston+train_pittsburgh``;
    - a Python sequence already produced by argparse.
    """
    if value is None:
        return []
    raw = list(value) if isinstance(value, (list, tuple, set)) else [str(value)]
    pieces: List[str] = []
    for item in raw:
        for part in re.split(r"[,+]", str(item)):
            part = part.strip()
            if part:
                pieces.append(part)
    return pieces


def expand_nuplan_db_files(db_files: str | Sequence[str] | None) -> List[str]:
    """Expand nuPlan DB file/folder/glob inputs into concrete ``.db`` files.

    The official nuPlan scenario builder expects database files in most devkit
    versions.  This helper accepts either individual ``.db`` files or folders
    containing DB sets, including folders such as ``train_boston`` and
    ``train_pittsburgh`` under a cache root.  It is intentionally strict: an
    existing folder with no ``.db`` files is treated as a configuration error
    instead of silently producing an empty/synthetic dataset.
    """
    expanded: List[str] = []
    for token in _split_path_list(db_files):
        matches = sorted(glob.glob(token)) if any(ch in token for ch in "*?[]") else [token]
        if not matches:
            raise RuntimeError(f"nuPlan DB pattern matched no files: {token}")
        for match in matches:
            path = Path(match)
            if path.is_dir():
                dbs = sorted(path.rglob("*.db"))
                if not dbs:
                    raise RuntimeError(f"nuPlan DB directory contains no .db files: {path}")
                expanded.extend(str(p) for p in dbs)
            else:
                expanded.append(str(path))
    # De-duplicate while preserving deterministic order.
    seen = set()
    out: List[str] = []
    for item in expanded:
        if item not in seen:
            out.append(item)
            seen.add(item)
    return out


def safe_call(obj: Any, names: List[str], default: Any = None) -> Any:
    for name in names:
        if hasattr(obj, name):
            attr = getattr(obj, name)
            try:
                return attr() if callable(attr) else attr
            except TypeError:
                continue
            except Exception:
                continue
    return default


class NuPlanAdapter:
    def __init__(
        self,
        scene_source: str = "synthetic",
        data_root: str | None = None,
        map_root: str | None = None,
        sensor_root: str | None = None,
        db_files: str | Sequence[str] | None = None,
        map_version: str | None = None,
        split: str = "mini",
        seed: int = 0,
        allow_synthetic_fallback: bool = False,
        # Legacy alias accepted for older scripts; does not imply fallback.
        nuplan_root: str | None = None,
    ) -> None:
        if scene_source not in {"synthetic", "nuplan"}:
            raise ValueError("scene_source must be 'synthetic' or 'nuplan'")
        if nuplan_root and not data_root:
            data_root = nuplan_root
        self.scene_source = scene_source
        self.data_root = data_root
        self.map_root = map_root
        self.sensor_root = sensor_root
        self.db_files_requested = db_files
        self.db_files = expand_nuplan_db_files(db_files) if scene_source == "nuplan" and db_files else ([] if scene_source == "nuplan" else db_files)
        self.map_version = map_version
        self.split = split
        self.seed = seed
        self.allow_synthetic_fallback = allow_synthetic_fallback
        self.nuplan_available = self._check_nuplan()
        self._builder = None
        if self.scene_source == "nuplan":
            self._init_nuplan_or_raise()
        elif self.scene_source == "synthetic":
            self._init_synthetic()
        else:  # defensive
            raise RuntimeError("unreachable scene source")

    @staticmethod
    def _check_nuplan() -> bool:
        return importlib.util.find_spec("nuplan") is not None

    def _init_synthetic(self) -> None:
        self._builder = None

    def _init_nuplan_or_raise(self) -> None:
        missing_args = [name for name, value in {
            "nuplan_data_root": self.data_root,
            "nuplan_map_root": self.map_root,
            "nuplan_db_files": self.db_files,
            "nuplan_map_version": self.map_version,
        }.items() if not value]
        if missing_args:
            raise RuntimeError(f"scene_source=nuplan requires real nuPlan paths: missing {', '.join(missing_args)}")
        if not self.nuplan_available:
            raise RuntimeError("scene_source=nuplan requested, but the nuPlan devkit is not installed; no synthetic fallback is allowed")
        for label, path in [("nuplan_data_root", self.data_root), ("nuplan_map_root", self.map_root)]:
            if path and not Path(path).exists():
                raise RuntimeError(f"scene_source=nuplan requested, but {label} does not exist: {path}")
        for db_path in self.db_files:
            if not Path(db_path).exists():
                raise RuntimeError(f"scene_source=nuplan requested, but nuPlan DB file does not exist: {db_path}")
        try:
            from nuplan.planning.scenario_builder.nuplan_db.nuplan_scenario_builder import NuPlanScenarioBuilder  # type: ignore
        except Exception as e:  # pragma: no cover - depends on local devkit
            raise RuntimeError(f"nuPlan devkit import failed: {e}") from e
        try:  # pragma: no cover - depends on local devkit
            self._builder = NuPlanScenarioBuilder(
                data_root=self.data_root,
                map_root=self.map_root,
                sensor_root=self.sensor_root,
                db_files=self.db_files,
                map_version=self.map_version,
            )
        except TypeError:  # older devkit signature
            self._builder = NuPlanScenarioBuilder(self.data_root, self.map_root, self.sensor_root, self.db_files, self.map_version)
        except Exception as e:
            raise RuntimeError(f"failed to initialize NuPlanScenarioBuilder: {e}") from e

    def iter_scenarios(self, max_scenarios: int = 4) -> Iterable[NuPlanScenarioRecord]:
        if self.scene_source == "synthetic":
            yield from self._iter_synthetic(max_scenarios)
            return
        yield from self._iter_real_nuplan(max_scenarios)

    def _iter_synthetic(self, max_scenarios: int) -> Iterator[NuPlanScenarioRecord]:
        for i in range(max_scenarios):
            eid = f"synthetic_{i:04d}"
            route_length = 1800.0 + 140.0 * i
            initial = Pose2D(0.0, 0.0, 0.0, "local")
            goal = Pose2D(route_length, 0.0, 0.0, "local")
            scene = SceneRecord(
                episode_id=eid,
                source="synthetic",
                split=self.split,
                scenario_token=f"synthetic_token_{i:04d}",
                log_name=f"synthetic_log_{self.split}",
                scenario_type="synthetic_smoke",
                map_name="synthetic_local_map",
                map_version="v0",
                initial_ego_pose=initial,
                mission_goal=goal,
                route_roadblock_ids=[f"rb_{j}" for j in range(4)],
                ego_history=[{"t": float(t), "x": 2.0 * t, "y": 0.0, "v": 2.0, "a": 0.0} for t in range(6)],
                agent_history=[{"t": float(t), "objects": [{"id": "agent0", "type": "vehicle", "x": 20.0 + t, "y": 4.0, "v": 1.0}]} for t in range(6)],
                traffic_light_history=[{"t": float(t), "statuses": []} for t in range(6)],
                route_corridor={"length_m": route_length, "polyline": [[0.0, 0.0], [route_length, 0.0]], "drivable_polygon": [[-5, -10], [route_length + 5, -10], [route_length + 5, 10], [-5, 10]]},
                timestamps_s=[float(t) for t in range(6)],
                metadata={"source": "synthetic", "seed": self.seed + i, "vehicle_safe": True},
            )
            ep = EpisodeMetadata(
                episode_id=eid,
                scenario_id=f"synthetic_scenario_{i:04d}",
                split=self.split,
                origin_anchor="origin",
                destination_anchor="destination",
                request_time_s=1000.0 + 60.0 * i,
                route_length_m=route_length,
                shortest_route_length_m=route_length * 0.92,
                seed=self.seed + i,
                nuplan_available=False,
                scene_source="synthetic",
                map_name=scene.map_name,
                map_version=scene.map_version,
                scenario_token=scene.scenario_token,
                log_name=scene.log_name,
                route_roadblock_ids=scene.route_roadblock_ids,
                metadata={"source": "synthetic", "route_corridor": scene.route_corridor, "vehicle_safe": True},
            )
            yield NuPlanScenarioRecord(ep, scene, scene.ego_history, scene.agent_history, {"map_name": scene.map_name, "map_version": scene.map_version}, scene.route_corridor)

    def _iter_real_nuplan(self, max_scenarios: int) -> Iterator[NuPlanScenarioRecord]:  # pragma: no cover - requires nuPlan installation/data
        assert self._builder is not None
        try:
            from nuplan.planning.scenario_builder.scenario_filter import ScenarioFilter  # type: ignore
        except Exception as e:
            raise RuntimeError(f"failed to import nuPlan ScenarioFilter: {e}") from e
        try:
            # API shapes differ across devkit versions.  Try common call forms.
            scenario_filter = ScenarioFilter(
                scenario_types=None,
                scenario_tokens=None,
                log_names=None,
                map_names=None,
                num_scenarios_per_type=None,
                limit_total_scenarios=max_scenarios,
                timestamp_threshold_s=None,
                ego_displacement_minimum_m=None,
                expand_scenarios=False,
                remove_invalid_goals=True,
                shuffle=False,
            )
        except TypeError:
            scenario_filter = ScenarioFilter(None, None, None, None, None, max_scenarios, None, None, False, True, False)
        scenarios = None
        for method in ["get_scenarios", "get_scenario_tokens"]:
            if hasattr(self._builder, method):
                try:
                    scenarios = getattr(self._builder, method)(scenario_filter, None)
                    break
                except TypeError:
                    try:
                        scenarios = getattr(self._builder, method)(scenario_filter)
                        break
                    except Exception:
                        continue
        if scenarios is None:
            raise RuntimeError("nuPlan scenario builder did not expose a usable get_scenarios API")
        for idx, scenario in enumerate(list(scenarios)[:max_scenarios]):
            yield self._extract_real_scenario(scenario, idx)

    def _extract_real_scenario(self, scenario: Any, idx: int) -> NuPlanScenarioRecord:  # pragma: no cover - requires nuPlan installation/data
        token = safe_call(scenario, ["token", "scenario_token", "get_token"], None)
        log_name = safe_call(scenario, ["log_name", "get_log_name"], None)
        scenario_type = safe_call(scenario, ["scenario_type", "get_scenario_type"], None)
        map_api = safe_call(scenario, ["map_api", "get_map_api"], None)
        map_name = safe_call(map_api, ["map_name", "get_map_name"], None) if map_api else None
        route_ids = safe_call(scenario, ["get_route_roadblock_ids", "route_roadblock_ids"], None)
        if not route_ids:
            raise RuntimeError(f"nuPlan scenario {token or idx} is missing route roadblock IDs")
        ego0 = safe_call(scenario, ["get_ego_state_at_iteration"], None)
        if callable(getattr(scenario, "get_ego_state_at_iteration", None)):
            ego0 = scenario.get_ego_state_at_iteration(0)
        pose0 = self._pose_from_ego(ego0)
        mission_goal_obj = safe_call(scenario, ["get_mission_goal", "mission_goal"], None)
        mission_goal = self._pose_from_any(mission_goal_obj) if mission_goal_obj is not None else None
        iterations = int(safe_call(scenario, ["get_number_of_iterations", "number_of_iterations"], 20) or 20)
        sample_iters = list(range(0, max(iterations, 1), max(1, iterations // 20)))[:20]
        ego_hist = []
        agents = []
        tls = []
        times = []
        for it in sample_iters:
            ego = scenario.get_ego_state_at_iteration(it) if hasattr(scenario, "get_ego_state_at_iteration") else None
            pose = self._pose_from_ego(ego)
            t_s = float(safe_call(ego, ["time_seconds"], it) or it)
            times.append(t_s)
            ego_hist.append({"iteration": it, "t": t_s, "x": pose.x, "y": pose.y, "heading": pose.heading, "v": self._ego_velocity(ego), "a": self._ego_accel(ego)})
            tracked = safe_call(scenario, ["get_tracked_objects_at_iteration"], None)
            try:
                tracked = scenario.get_tracked_objects_at_iteration(it)
            except Exception:
                tracked = None
            agents.append({"iteration": it, "objects": self._tracked_to_records(tracked)})
            try:
                tl = scenario.get_traffic_light_status_at_iteration(it)
            except Exception:
                tl = []
            tls.append({"iteration": it, "statuses": [str(x) for x in (tl or [])]})
        route_len = self._estimate_route_length(route_ids, map_api)
        stable = hashlib.sha1(f"{log_name}:{token}".encode()).hexdigest()[:12]
        eid = f"nuplan_{stable}"
        if not map_name or not token or not log_name:
            raise RuntimeError(f"nuPlan scenario missing critical identifiers: token={token}, log={log_name}, map={map_name}")
        scene = SceneRecord(
            episode_id=eid,
            source="nuplan",
            split=self.split,
            scenario_token=str(token),
            log_name=str(log_name),
            scenario_type=str(scenario_type) if scenario_type else None,
            map_name=str(map_name),
            map_version=self.map_version,
            initial_ego_pose=pose0,
            mission_goal=mission_goal,
            route_roadblock_ids=list(route_ids),
            ego_history=ego_hist,
            agent_history=agents,
            traffic_light_history=tls,
            route_corridor={"roadblock_ids": list(route_ids), "length_m": route_len, "polyline": [[pose0.x, pose0.y], [mission_goal.x, mission_goal.y]] if mission_goal else []},
            timestamps_s=times,
            metadata={"source": "nuplan", "data_root": self.data_root, "db_files": self.db_files},
        )
        ep = EpisodeMetadata(eid, str(token), self.split, "origin", "destination", times[0] if times else 0.0, route_len, max(route_len * 0.9, 1.0), self.seed + idx, True, "nuplan", str(map_name), self.map_version, str(token), str(log_name), list(route_ids), {"source": "nuplan", "route_corridor": scene.route_corridor})
        # Keep the map API only in the in-memory record.  The serializable SceneRecord
        # above intentionally stores only map identifiers and route metadata.
        return NuPlanScenarioRecord(ep, scene, ego_hist, agents, {"map_name": map_name, "map_version": self.map_version, "map_api": map_api}, scene.route_corridor)

    @staticmethod
    def _pose_from_any(obj: Any) -> Pose2D:
        if obj is None:
            return Pose2D(0.0, 0.0)
        for attr in ["rear_axle", "center", "point"]:
            if hasattr(obj, attr):
                obj = getattr(obj, attr)
                break
        x = float(safe_call(obj, ["x"], 0.0) or 0.0)
        y = float(safe_call(obj, ["y"], 0.0) or 0.0)
        heading = float(safe_call(obj, ["heading"], 0.0) or 0.0)
        return Pose2D(x, y, heading, "map")

    @staticmethod
    def _pose_from_ego(ego: Any) -> Pose2D:
        if ego is None:
            return Pose2D(0.0, 0.0, 0.0, "map")
        axle = safe_call(ego, ["rear_axle", "center"], ego)
        return NuPlanAdapter._pose_from_any(axle)

    @staticmethod
    def _ego_velocity(ego: Any) -> float:
        dyn = safe_call(ego, ["dynamic_car_state"], None)
        speed = safe_call(dyn, ["speed"], None) if dyn else None
        if speed is not None:
            return float(speed)
        return float(safe_call(ego, ["speed"], 0.0) or 0.0)

    @staticmethod
    def _ego_accel(ego: Any) -> float:
        dyn = safe_call(ego, ["dynamic_car_state"], None)
        acc = safe_call(dyn, ["acceleration"], None) if dyn else None
        try:
            return float(acc)
        except Exception:
            return 0.0

    @staticmethod
    def _tracked_to_records(tracked: Any) -> List[Dict[str, Any]]:
        objs = []
        raw = safe_call(tracked, ["tracked_objects", "get_tracked_objects"], []) if tracked is not None else []
        for o in raw or []:
            pose = NuPlanAdapter._pose_from_any(safe_call(o, ["center", "box"], o))
            objs.append({"type": str(safe_call(o, ["tracked_object_type"], "unknown")), "x": pose.x, "y": pose.y, "heading": pose.heading})
        return objs

    @staticmethod
    def _estimate_route_length(route_ids: List[str], map_api: Any) -> float:
        # Prefer conservative real map lengths if available, otherwise derive a
        # nonzero route length from the number of roadblocks without inventing map
        # geometry.  Missing route IDs are rejected before this method.
        total = 0.0
        if map_api is not None:
            for rid in route_ids:
                try:
                    obj = map_api.get_map_object(rid, None)
                    bl = safe_call(obj, ["baseline_path"], None)
                    length = safe_call(bl, ["length"], None)
                    if length:
                        total += float(length)
                except Exception:
                    continue
        return total if total > 0 else max(100.0, 120.0 * len(route_ids))
