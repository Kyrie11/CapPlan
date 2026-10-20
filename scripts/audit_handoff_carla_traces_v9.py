#!/usr/bin/env python3
"""Offline, simulator-independent CARLA preflight diagnosis.

Produces an audit of uploaded Clear-A trajectories. No simulation is run;
no physical stopping, legal curb or human handoff is inferred.
"""
from __future__ import annotations
import argparse
import json
import math
from collections import defaultdict, Counter
from pathlib import Path


def read_jsonl(path):
    with Path(path).open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def diagnose(folder: Path, radius: float = 4.0, speed_threshold: float = 0.5, dwell_s: float = 2.0):
    if radius <= 0 or speed_threshold <= 0 or dwell_s < 0:
        raise ValueError("Invalid radius, stop speed or dwell")
    trials = []
    grouping = defaultdict(list)
    for file in sorted(Path(folder).glob("spawn*_asteps*/execution_report.json")):
        run_dir = file.parent
        report = json.loads(file.read_text(encoding="utf-8"))
        path = run_dir / "trajectory.jsonl"
        if not path.exists():
            trials.append({"trial": run_dir.name, "status": "MISSING_TRACE"})
            continue
        data = list(read_jsonl(path))
        if not data:
            trials.append({"trial": run_dir.name, "status": "EMPTY_TRACE"})
            continue
        idx = min(range(len(data)), key=lambda i: float(data[i]["distance_to_goal_m"]))
        gaps = [float(x["distance_to_goal_m"]) for x in data]
        times = [float(x["elapsed_s"]) for x in data]
        if any(times[i+1] < times[i] for i in range(len(times)-1)):
            raise ValueError(f"Non-monotone trace time {path}")
        best = 0.0
        ongoing = 0.0
        prev_t = None
        for row, gap, t in zip(data, gaps, times):
            dt = t-prev_t if prev_t is not None else float(report.get("sample_dt_s", 0.05))
            prev_t = t
            valid = gap <= radius and float(row["speed_mps"]) <= speed_threshold
            ongoing = ongoing + max(0.0, dt) if valid else 0.0
            best = max(best, ongoing)
        start, last, nearest = data[0], data[-1], data[idx]
        trial = {
            "trial": run_dir.name,
            "spawn_index": report.get("geometry_parameters", {}).get("spawn_index"),
            "a_steps": report.get("geometry_parameters", {}).get("a_steps"),
            "reference_goal_radius_m": radius,
            "start_goal_gap_m": round(gaps[0], 3),
            "min_goal_gap_m": round(gaps[idx], 3),
            "time_of_min_gap_s": round(times[idx], 3),
            "end_goal_gap_m": round(gaps[-1], 3),
            "distance_away_after_closest_m": round(gaps[-1] - gaps[idx], 3),
            "min_gap_below_radius": gaps[idx] <= radius,
            "best_stationary_dwell_s": round(best, 3),
            "valid_clear_stop": best + 1e-6 >= dwell_s and not report.get("collisions"),
            "moved_farther_after_closest": gaps[-1] > gaps[idx] + 2,
            "start_xy": [round(float(start[k]), 3) for k in ("x", "y")],
            "end_xy": [round(float(last[k]), 3) for k in ("x", "y")],
            "closest_xy": [round(float(nearest[k]), 3) for k in ("x", "y")],
            "no_collision_sensor_events_observed": report.get("collisions") == 0,
        }
        trials.append(trial)
        grouping[trial["spawn_index"]].append(trial)
    groups = []
    for spawn, rs in sorted(grouping.items(), key=lambda x: x[0]):
        eps = max(math.dist(a["end_xy"], b["end_xy"]) for a in rs for b in rs) if len(rs) > 1 else 0
        groups.append({
            "spawn_index": spawn, "n_distinct_goals": len(rs),
            "max_final_xy_spread_across_goals_m": round(eps, 4),
            "target_insensitive_endpoint_under_0_5_m": len(rs)>1 and eps<0.5,
            "interpretation": "Goal-insensitive terminal trajectories are diagnostic of route/control setup, not curb obstruction",
        })
    counts = Counter({"total":0,"valid_clear_stops":0,"moved_away_after_approach":0,"missing_or_empty_trace":0})
    for t in trials:
        counts["total"] += 1
        if t.get("valid_clear_stop"):
            counts["valid_clear_stops"] += 1
        if t.get("moved_farther_after_closest"):
            counts["moved_away_after_approach"] += 1
        if t.get("status"):
            counts["missing_or_empty_trace"] += 1
    return {
        "scope": "CARLA trajectory diagnostic; no independent vehicle/curb/boarding certificate",
        "counts": dict(counts), "trials": trials, "start_groups": groups,
        "preflight_gate": "PASS" if counts["valid_clear_stops"]>0 else "FAIL_NO_VALID_CLEAR_STOP",
        "next_experiment": "Check first-segment planned route, spawn heading, lane direction, and BasicAgent goal-passing behavior; pre-register arrival radius and stopping law before blocked-vs-clear",
    }


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--carla_dir",required=True)
    p.add_argument("--output",required=True)
    p.add_argument("--arrival_radius_m",type=float,default=4.0)
    p.add_argument("--stationary_speed_mps",type=float,default=0.5)
    p.add_argument("--required_dwell_s",type=float,default=2.0)
    args=p.parse_args()
    result=diagnose(Path(args.carla_dir),args.arrival_radius_m,args.stationary_speed_mps,args.required_dwell_s)
    out=Path(args.output);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"preflight_gate":result["preflight_gate"],"counts":result["counts"],"endpoint_insensitive_groups":sum(g["target_insensitive_endpoint_under_0_5_m"] for g in result["start_groups"])}))

if __name__=="__main__":
    main()
