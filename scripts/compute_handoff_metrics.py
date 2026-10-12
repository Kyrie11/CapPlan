#!/usr/bin/env python3
"""Compute handoff/recourse metrics from method rollout JSONL."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from capplan.evaluation.handoff_metrics import compute_handoff_metrics


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--rollouts", required=True, help="One executed handoff per JSONL row")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    rows = [json.loads(line) for line in Path(args.rollouts).read_text(encoding="utf-8").splitlines() if line.strip()]
    result = compute_handoff_metrics(rows)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"episodes": len(rows), "output": args.output}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

