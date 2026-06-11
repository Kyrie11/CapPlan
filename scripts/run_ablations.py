#!/usr/bin/env python
from __future__ import annotations

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

import argparse
from capplan.evaluation.experiment_runner import run_ablation_table


def main() -> None:
    p = argparse.ArgumentParser(description="Run CapPlan ablations over a saved dataset.")
    p.add_argument("--dataset_dir", default="outputs/datasets/synthetic")
    p.add_argument("--output_dir", default="outputs/eval/ablations")
    p.add_argument("--trajectory_mode", choices=["mock_strict", "nuplan_closed_loop"], default="mock_strict")
    args = p.parse_args()
    rows = run_ablation_table(args.dataset_dir, args.output_dir, trajectory_mode=args.trajectory_mode)
    for k, v in rows.items():
        print(k, v)


if __name__ == "__main__":
    main()
