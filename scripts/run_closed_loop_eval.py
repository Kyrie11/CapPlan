#!/usr/bin/env python
from __future__ import annotations

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

import argparse
from capplan.evaluation.closed_loop import ClosedLoopRunner
from capplan.planning.planner import PlannerConfig


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dataset_dir", default="outputs/datasets/synthetic")
    p.add_argument("--output_dir", default="outputs/metrics/closed_loop")
    args = p.parse_args()
    res = ClosedLoopRunner(PlannerConfig()).run_dataset(args.dataset_dir, args.output_dir)
    print(res["metrics"])

if __name__ == "__main__":
    main()
