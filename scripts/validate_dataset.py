#!/usr/bin/env python
from __future__ import annotations

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

import argparse
from capplan.data.validate_dataset import validate_dataset
from capplan.utils.serialization import dump_json


def main() -> None:
    p = argparse.ArgumentParser(description="Validate a canonical CapPlan dataset.")
    p.add_argument("--dataset_dir", required=True)
    p.add_argument("--strict", action="store_true")
    args = p.parse_args()
    result = validate_dataset(args.dataset_dir, strict=args.strict)
    dump_json(_Path(args.dataset_dir) / "validation_report.json", result)
    print(result)
    errors = result.get("errors", []) if isinstance(result, dict) else []
    print(f"DATASET_SCHEMA_CHECK={'PASS' if not errors else 'FAIL'}")


if __name__ == "__main__":
    main()
