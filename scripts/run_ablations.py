#!/usr/bin/env python
from __future__ import annotations

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

import argparse
from capplan.evaluation.experiment_runner import run_ablation_table


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--dataset_dir", default="outputs/datasets/synthetic")
    p.add_argument("--output_dir", default="outputs/tables")
    args = p.parse_args()
    rows = run_ablation_table(args.dataset_dir, args.output_dir)
    for k, v in rows.items():
        print(k, v)

if __name__ == "__main__":
    main()
