#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capplan.evaluation.closed_loop import ClosedLoopRunner
from capplan.planning.planner import PlannerConfig
from capplan.utils.serialization import dump_json
from scripts.export_nuplan_closed_loop_jobs import export_jobs
from scripts.import_nuplan_vehicle_metrics import import_metrics


def main() -> None:
    p = argparse.ArgumentParser(description="Dataset-bound nuPlan closed-loop pipeline: export scenarios, optionally run nuPlan, import vehicle metrics, then compute passenger-complete metrics.")
    p.add_argument("--dataset_dir", required=True)
    p.add_argument("--output_dir", required=True)
    p.add_argument("--stages", default="export,import,eval", help="Comma list: export,run,import,eval")
    p.add_argument("--nuplan_run_command", default=None, help="Optional shell command executed at run stage. Placeholders: {job_dir}, {dataset_dir}, {output_dir}.")
    p.add_argument("--nuplan_metrics_source", default=None, help="Metric file/dir from nuPlan; required for import unless run command writes <output_dir>/nuplan_simulation.")
    p.add_argument("--casa_checkpoint", default=None)
    p.add_argument("--casa_mode", choices=["learned", "heuristic_oracle_baseline"], default="learned")
    p.add_argument("--limit", type=int, default=None)
    args = p.parse_args()

    stages = {x.strip() for x in args.stages.split(",") if x.strip()}
    dataset_dir = Path(args.dataset_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    job_dir = output_dir / "nuplan_job"
    summary = {"dataset_dir": str(dataset_dir), "output_dir": str(output_dir), "stages": sorted(stages)}

    if "export" in stages:
        summary["export"] = export_jobs(dataset_dir, job_dir, args.limit)

    if "run" in stages:
        if not args.nuplan_run_command:
            raise RuntimeError("stage run requires --nuplan_run_command. Use scripts/export_nuplan_closed_loop_jobs.py first if you need a template.")
        cmd = args.nuplan_run_command.format(job_dir=str(job_dir), dataset_dir=str(dataset_dir), output_dir=str(output_dir))
        summary["nuplan_run_command"] = cmd
        subprocess.check_call(cmd, shell=True)

    if "import" in stages:
        metrics_source = args.nuplan_metrics_source or (output_dir / "nuplan_simulation")
        if not Path(metrics_source).exists():
            raise RuntimeError(f"import stage requires --nuplan_metrics_source or existing {metrics_source}")
        summary["import"] = import_metrics(dataset_dir, metrics_source, dataset_dir / "nuplan_vehicle_metrics.jsonl", output_dir / "nuplan_vehicle_metrics_import_report.json")

    if "eval" in stages:
        if args.casa_mode == "learned" and not args.casa_checkpoint:
            raise RuntimeError("eval stage with --casa_mode learned requires --casa_checkpoint")
        cfg = PlannerConfig(trajectory_mode="nuplan_closed_loop", casa_mode=args.casa_mode, casa_checkpoint=args.casa_checkpoint)
        res = ClosedLoopRunner(cfg).run_dataset(dataset_dir, output_dir / "capplan_eval")
        summary["metrics"] = res["metrics"]

    dump_json(output_dir / "nuplan_closed_loop_pipeline_summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
