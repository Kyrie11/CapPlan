#!/usr/bin/env python
from __future__ import annotations

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

import argparse
from capplan.evaluation.metrics import compute_all_metrics
from capplan.utils.serialization import dump_json, read_jsonl


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--episode_metrics", required=True)
    p.add_argument("--counterfactual_pairs", default=None)
    p.add_argument("--output", required=True)
    args = p.parse_args()
    pairs = read_jsonl(args.counterfactual_pairs) if args.counterfactual_pairs else []
    metrics = compute_all_metrics(read_jsonl(args.episode_metrics), pairs)
    dump_json(args.output, metrics)
    print(metrics)


if __name__ == "__main__":
    main()
