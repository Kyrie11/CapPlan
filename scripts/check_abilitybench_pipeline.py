#!/usr/bin/env python
"""Run the high-level AbilityBench data gates and print one final PASS/FAIL token."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]


def run_step(name: str, cmd: List[str]) -> Dict[str, Any]:
    proc = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    result = {
        "name": name,
        "command": cmd,
        "returncode": proc.returncode,
        "status": "PASS" if proc.returncode == 0 else "FAIL",
        "stdout_tail": proc.stdout[-6000:],
        "stderr_tail": proc.stderr[-6000:],
    }
    marker_lines = [line for line in proc.stdout.splitlines() if line.endswith("=PASS") or line.endswith("=FAIL")]
    if marker_lines:
        result["markers"] = marker_lines[-8:]
    return result


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", default="configs/abilitybench_nuplan_real.yaml")
    p.add_argument("--source_policy", choices=["bootstrap", "paper"], default="bootstrap")
    p.add_argument("--cities", default="boston,pittsburgh,vegas,singapore")
    p.add_argument("--splits", default="train,val,test", help="Comma-separated subset of train,val,test")
    p.add_argument("--dataset_dir", default=None, help="If set, also run schema + quality gates on the built dataset.")
    p.add_argument("--report_json", default=None)
    p.add_argument("--skip_db_city_check", action="store_true")
    args = p.parse_args()

    steps: List[Dict[str, Any]] = []
    py = sys.executable
    if not args.skip_db_city_check:
        for split in [x.strip() for x in args.splits.split(",") if x.strip()]:
            if split not in {"train", "val", "test"}:
                raise SystemExit(f"unsupported split: {split}")
            steps.append(run_step(
                f"nuplan_db_city:{split}",
                [py, "scripts/inspect_nuplan_db_cities.py", "--config", args.config, "--split", split, "--fail_on_unknown"],
            ))

    steps.append(run_step(
        f"external_sources:{args.source_policy}",
        [py, "scripts/validate_external_sources.py", "--config", args.config, "--cities", args.cities, "--source_policy", args.source_policy],
    ))

    if args.dataset_dir:
        steps.append(run_step(
            "dataset_schema",
            [py, "scripts/validate_dataset.py", "--dataset_dir", args.dataset_dir, "--strict"],
        ))
        audit_cmd = [py, "scripts/audit_dataset_quality.py", "--dataset_dir", args.dataset_dir]
        if args.source_policy == "paper":
            audit_cmd += ["--paper_mode", "--fail_if_not_publication_ready"]
        steps.append(run_step("dataset_quality", audit_cmd))

    status = "PASS" if all(x["returncode"] == 0 for x in steps) else "FAIL"
    payload = {"status": status, "config": args.config, "source_policy": args.source_policy, "steps": steps}
    if args.report_json:
        out = Path(args.report_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    print(f"ABILITYBENCH_PIPELINE_CHECK={status}")
    if status != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
