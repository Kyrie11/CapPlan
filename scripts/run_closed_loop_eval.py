#!/usr/bin/env python
from __future__ import annotations

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

import argparse
from capplan.evaluation.closed_loop import ClosedLoopRunner
from capplan.planning.planner import PlannerConfig


def main() -> None:
    p = argparse.ArgumentParser(description="Run CapPlan closed-loop/strict mock evaluation over a saved dataset.")
    p.add_argument("--dataset_dir", default="outputs/datasets/synthetic")
    p.add_argument("--output_dir", default="outputs/eval/closed_loop")
    p.add_argument("--trajectory_mode", choices=["mock_strict", "nuplan_closed_loop"], default="mock_strict")
    p.add_argument("--casa_mode", choices=["heuristic_oracle_baseline", "learned"], default="heuristic_oracle_baseline")
    p.add_argument("--casa_checkpoint", default=None, help="Checkpoint produced by scripts.train_casa; required for a meaningful learned CASA run.")
    args = p.parse_args()
    cfg = PlannerConfig(trajectory_mode=args.trajectory_mode, casa_mode=args.casa_mode, casa_checkpoint=args.casa_checkpoint)
    res = ClosedLoopRunner(cfg).run_dataset(args.dataset_dir, args.output_dir)
    print(res["metrics"])


if __name__ == "__main__":
    main()
