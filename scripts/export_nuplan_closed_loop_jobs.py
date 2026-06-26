#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capplan.utils.serialization import dump_json, read_jsonl, write_jsonl


def export_jobs(dataset_dir: str | Path, output_dir: str | Path, limit: int | None = None) -> Dict[str, Any]:
    dataset_dir = Path(dataset_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    scenes = read_jsonl(dataset_dir / "scenes.jsonl")
    episodes = {e.get("episode_id"): e for e in read_jsonl(dataset_dir / "episodes.jsonl")}
    if limit is not None and limit > 0:
        scenes = scenes[:limit]
    rows: List[Dict[str, Any]] = []
    for s in scenes:
        eid = s.get("episode_id")
        ep = episodes.get(eid, {})
        rows.append({
            "episode_id": eid,
            "scenario_token": s.get("scenario_token") or ep.get("scenario_token"),
            "log_name": s.get("log_name") or ep.get("log_name"),
            "scenario_type": s.get("scenario_type"),
            "map_name": s.get("map_name") or ep.get("map_name"),
            "route_roadblock_ids": s.get("route_roadblock_ids") or ep.get("route_roadblock_ids") or [],
            "route_length_m": ep.get("route_length_m"),
        })
    tokens = [str(r["scenario_token"]) for r in rows if r.get("scenario_token")]
    logs = sorted({str(r["log_name"]) for r in rows if r.get("log_name")})
    maps = sorted({str(r["map_name"]) for r in rows if r.get("map_name")})
    types = sorted({str(r["scenario_type"]) for r in rows if r.get("scenario_type")})
    write_jsonl(output_dir / "capplan_episode_mapping.jsonl", rows)
    (output_dir / "scenario_tokens.txt").write_text("\n".join(tokens) + ("\n" if tokens else ""), encoding="utf-8")
    (output_dir / "log_names.txt").write_text("\n".join(logs) + ("\n" if logs else ""), encoding="utf-8")
    selection = {
        "dataset_dir": str(dataset_dir),
        "num_episodes": len(rows),
        "scenario_tokens": tokens,
        "log_names": logs,
        "map_names": maps,
        "scenario_types": types,
        "episode_mapping_jsonl": str(output_dir / "capplan_episode_mapping.jsonl"),
    }
    dump_json(output_dir / "nuplan_scenario_selection.json", selection)
    template = f'''#!/usr/bin/env bash
set -euo pipefail
# Fill in the planner/simulation config names that match your installed nuPlan-devkit.
# This job file is intentionally data-bound: it points nuPlan to exactly the scenario
# tokens/logs present in the CapPlan dataset so vehicle metrics can be imported back.
DATASET_DIR="{dataset_dir}"
JOB_DIR="{output_dir}"
SCENARIO_TOKENS_FILE="$JOB_DIR/scenario_tokens.txt"
LOG_NAMES_FILE="$JOB_DIR/log_names.txt"
NUPLAN_EXP_DIR="$JOB_DIR/nuplan_simulation"

# Example hydra shape; adjust config names to your nuPlan install if needed:
# python -m nuplan.planning.script.run_simulation \
#   +simulation=closed_loop_nonreactive_agents \
#   planner=YOUR_PLANNER \
#   scenario_filter.scenario_tokens="$(paste -sd, "$SCENARIO_TOKENS_FILE")" \
#   scenario_filter.log_names="$(paste -sd, "$LOG_NAMES_FILE")" \
#   group=$NUPLAN_EXP_DIR

# After nuPlan finishes, import its metric files back into the dataset:
# python scripts/import_nuplan_vehicle_metrics.py \
#   --dataset_dir "$DATASET_DIR" \
#   --metrics_source "$NUPLAN_EXP_DIR" \
#   --output_jsonl "$DATASET_DIR/nuplan_vehicle_metrics.jsonl"
'''
    sh = output_dir / "run_nuplan_template.sh"
    sh.write_text(template, encoding="utf-8")
    sh.chmod(0o755)
    return {"output_dir": str(output_dir), "num_episodes": len(rows), "scenario_tokens": len(tokens), "log_names": len(logs), "maps": maps, "template": str(sh)}


def main() -> None:
    p = argparse.ArgumentParser(description="Export a CapPlan dataset as a nuPlan closed-loop scenario selection job.")
    p.add_argument("--dataset_dir", required=True)
    p.add_argument("--output_dir", required=True)
    p.add_argument("--limit", type=int, default=None)
    args = p.parse_args()
    print(json.dumps(export_jobs(args.dataset_dir, args.output_dir, args.limit), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
