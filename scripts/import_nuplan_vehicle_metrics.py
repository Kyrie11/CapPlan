#!/usr/bin/env python
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capplan.utils.serialization import dump_json, read_jsonl, write_jsonl


def _safe_read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _flatten_metric_records(obj: Any, source: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if isinstance(obj, list):
        for x in obj:
            rows.extend(_flatten_metric_records(x, source))
        return rows
    if not isinstance(obj, dict):
        return rows
    # Common direct row shapes.
    id_keys = {"episode_id", "scenario_token", "token", "scenario_id", "log_name"}
    metric_keys = {"route_completion", "collision", "collisions", "drivable_area", "drivable_area_compliance", "traffic_rule_violation", "rule_violation", "travel_time_s", "distance_m", "score"}
    if any(k in obj for k in id_keys) and any(k in obj for k in metric_keys):
        r = dict(obj)
        r.setdefault("_metrics_source", source)
        rows.append(r)
    # nuPlan metric aggregator files often nest per-scenario rows under keys.
    for key in ["scenarios", "scenario_metrics", "metric_statistics", "results", "rows", "data", "metrics"]:
        val = obj.get(key)
        if isinstance(val, (list, dict)):
            rows.extend(_flatten_metric_records(val, source))
    # Some JSONs are dicts keyed by scenario token.
    for k, v in obj.items():
        if isinstance(v, dict) and ("route_completion" in v or "score" in v or "statistics" in v):
            row = dict(v)
            row.setdefault("scenario_token", k)
            row.setdefault("_metrics_source", source)
            rows.extend(_flatten_metric_records(row, source) or [row])
    return rows


def _read_metric_source(path: str | Path) -> List[Dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    files: List[Path]
    if p.is_dir():
        files = sorted([x for x in p.rglob("*") if x.suffix.lower() in {".json", ".jsonl", ".csv"}])
    else:
        files = [p]
    rows: List[Dict[str, Any]] = []
    for f in files:
        if f.suffix.lower() == ".jsonl":
            for row in read_jsonl(f):
                d = dict(row)
                d.setdefault("_metrics_source", str(f))
                rows.append(d)
        elif f.suffix.lower() == ".csv":
            with f.open("r", encoding="utf-8", newline="") as fh:
                for row in csv.DictReader(fh):
                    d = dict(row)
                    d.setdefault("_metrics_source", str(f))
                    rows.append(d)
        elif f.suffix.lower() == ".json":
            rows.extend(_flatten_metric_records(_safe_read_json(f), str(f)))
    return rows


def _as_float(row: Dict[str, Any], *keys: str, default: float = 0.0) -> float:
    for k in keys:
        if k in row and row[k] not in (None, ""):
            try:
                return float(row[k])
            except Exception:
                pass
    return default


def _as_bool(row: Dict[str, Any], *keys: str, default: bool = False) -> bool:
    for k in keys:
        if k in row and row[k] not in (None, ""):
            v = row[k]
            if isinstance(v, bool):
                return v
            if isinstance(v, (int, float)):
                return bool(v)
            s = str(v).strip().lower()
            if s in {"true", "1", "yes", "y", "pass", "passed", "ok"}:
                return True
            if s in {"false", "0", "no", "n", "fail", "failed"}:
                return False
    return default


def _score_as_fraction(x: float) -> float:
    if x > 1.0 and x <= 100.0:
        return x / 100.0
    return max(0.0, min(1.0, x))


def _scene_mapping(dataset_dir: Path) -> Dict[str, Dict[str, Any]]:
    scenes = read_jsonl(dataset_dir / "scenes.jsonl")
    episodes = {e.get("episode_id"): e for e in read_jsonl(dataset_dir / "episodes.jsonl")}
    out: Dict[str, Dict[str, Any]] = {}
    for s in scenes:
        eid = str(s.get("episode_id"))
        rec = {**episodes.get(eid, {}), **s}
        for key in [eid, str(s.get("scenario_token")), str(s.get("scenario_id")), str(s.get("log_name")) + ":" + str(s.get("scenario_token"))]:
            if key and key != "None":
                out[key] = rec
    return out


def _match_row(row: Dict[str, Any], mapping: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    keys = []
    for k in ["episode_id", "scenario_token", "token", "scenario_id"]:
        if row.get(k) not in (None, ""):
            keys.append(str(row[k]))
    if row.get("log_name") and (row.get("scenario_token") or row.get("token")):
        keys.append(str(row.get("log_name")) + ":" + str(row.get("scenario_token") or row.get("token")))
    for k in keys:
        if k in mapping:
            return mapping[k]
    return None


def normalize_vehicle_metric_row(row: Dict[str, Any], scene: Dict[str, Any]) -> Dict[str, Any]:
    rc = _score_as_fraction(_as_float(row, "route_completion", "rc", "RC", "ego_progress_along_expert_route", "route_completion_score", default=0.0))
    # Boolean metric names in nuPlan often encode pass=True, so invert them for failure booleans.
    no_collision = _as_bool(row, "no_ego_at_fault_collisions", "no_collisions", "collision_free", default=False)
    collision = _as_bool(row, "collision", "at_fault_collision", default=False) or _as_float(row, "collisions", "num_collisions", default=0.0) > 0
    if no_collision:
        collision = False
    drivable = _as_bool(row, "drivable_area", "drivable_area_compliance", "ego_is_in_drivable_area", default=True)
    if _as_float(row, "drivable_area_violation", "drivable_area_violation_count", default=0.0) > 0:
        drivable = False
    rule_violation = _as_bool(row, "rule_violation", "traffic_rule_violation", "trv", default=False)
    if _as_bool(row, "speed_limit_compliance", "stop_line_compliance", default=True) is False:
        rule_violation = True
    distance = _as_float(row, "distance_m", "vehicle_distance_m", "driven_distance_m", default=float(scene.get("route_length_m", 0.0) or 0.0) * rc)
    tt = _as_float(row, "travel_time_s", "tt_s", "TT", "duration_s", "simulation_duration_s", default=max(1.0, distance / 8.0 if distance else 1.0))
    return {
        "episode_id": scene.get("episode_id"),
        "scenario_token": scene.get("scenario_token"),
        "log_name": scene.get("log_name"),
        "map_name": scene.get("map_name"),
        "route_completion": rc,
        "collision": bool(collision),
        "drivable_area": bool(drivable),
        "rule_violation": bool(rule_violation),
        "distance_m": float(distance),
        "travel_time_s": float(tt),
        "source": "nuplan_closed_loop_import",
        "raw_metric_source": row.get("_metrics_source"),
    }


def import_metrics(dataset_dir: str | Path, metrics_source: str | Path, output_jsonl: str | Path | None = None, report_json: str | Path | None = None) -> Dict[str, Any]:
    dataset_dir = Path(dataset_dir)
    output_jsonl = Path(output_jsonl) if output_jsonl else dataset_dir / "nuplan_vehicle_metrics.jsonl"
    mapping = _scene_mapping(dataset_dir)
    raw_rows = _read_metric_source(metrics_source)
    normalized: List[Dict[str, Any]] = []
    unmatched = 0
    seen = set()
    for row in raw_rows:
        scene = _match_row(row, mapping)
        if not scene:
            unmatched += 1
            continue
        eid = str(scene.get("episode_id"))
        if eid in seen:
            continue
        normalized.append(normalize_vehicle_metric_row(row, scene))
        seen.add(eid)
    write_jsonl(output_jsonl, normalized)
    expected = sorted({v.get("episode_id") for v in mapping.values() if v.get("episode_id")})
    got = sorted({r.get("episode_id") for r in normalized})
    missing = [x for x in expected if x not in set(got)]
    report = {"metrics_source": str(metrics_source), "output_jsonl": str(output_jsonl), "raw_rows": len(raw_rows), "matched_episodes": len(got), "expected_episodes": len(expected), "unmatched_rows": unmatched, "missing_episodes": missing[:50], "coverage": len(got) / max(1, len(expected))}
    if report_json:
        dump_json(report_json, report)
    else:
        dump_json(output_jsonl.with_suffix(".report.json"), report)
    return report


def main() -> None:
    p = argparse.ArgumentParser(description="Import nuPlan closed-loop vehicle metrics and map them onto a CapPlan dataset by episode/scenario token.")
    p.add_argument("--dataset_dir", required=True)
    p.add_argument("--metrics_source", required=True, help="nuPlan metric JSON/JSONL/CSV file or directory")
    p.add_argument("--output_jsonl", default=None, help="Default: <dataset_dir>/nuplan_vehicle_metrics.jsonl")
    p.add_argument("--report_json", default=None)
    args = p.parse_args()
    print(json.dumps(import_metrics(args.dataset_dir, args.metrics_source, args.output_jsonl, args.report_json), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
