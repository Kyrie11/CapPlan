#!/usr/bin/env python
"""Fail-closed preflight for publication-grade reference service vehicles."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from capplan.data.passenger_service_layer import _read_records
from capplan.utils.serialization import dump_json

CORE = {
    "door_side", "ramp", "lift", "low_floor", "door_width_m",
    "deployment_clearance_m", "notification_modes", "dwell_time_s", "kneeling",
}


def _bad_source(value: Any) -> bool:
    s = str(value or "").strip().lower()
    return (not s) or s.startswith("synthetic") or "proxy" in s or "example" in s or s in {"toy", "mock", "default", "unknown"}


def validate(path: str | Path) -> Dict[str, Any]:
    rows = _read_records(path)
    issues: List[Dict[str, Any]] = []
    sources = set()
    for i, row in enumerate(rows):
        vid = str(row.get("vehicle_id") or row.get("interface_spec_id") or f"row:{i}")
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        source = row.get("source") or meta.get("source")
        sources.add(str(source))
        if not row.get("episode_id"):
            issues.append({"vehicle_id": vid, "issue": "missing_episode_id_use_*_for_global"})
        if _bad_source(source):
            issues.append({"vehicle_id": vid, "issue": "unverified_or_example_source", "source": source})
        supplied = set(str(x) for x in (meta.get("provided_interface_fields") or [])) | set(str(k) for k in row.keys())
        missing = sorted(CORE - supplied)
        if missing:
            issues.append({"vehicle_id": vid, "issue": "core_fields_not_explicit", "missing": missing})
        if row.get("door_side") not in {"left", "right", "both"}:
            issues.append({"vehicle_id": vid, "issue": "invalid_door_side", "value": row.get("door_side")})
        try:
            if float(row.get("door_width_m")) <= 0:
                raise ValueError
        except Exception:
            issues.append({"vehicle_id": vid, "issue": "invalid_door_width_m", "value": row.get("door_width_m")})
        try:
            if float(row.get("deployment_clearance_m")) < 0:
                raise ValueError
        except Exception:
            issues.append({"vehicle_id": vid, "issue": "invalid_deployment_clearance_m", "value": row.get("deployment_clearance_m")})
        try:
            if float(row.get("dwell_time_s")) <= 0:
                raise ValueError
        except Exception:
            issues.append({"vehicle_id": vid, "issue": "invalid_dwell_time_s", "value": row.get("dwell_time_s")})
        modes = row.get("notification_modes")
        if not isinstance(modes, list) or not modes:
            issues.append({"vehicle_id": vid, "issue": "notification_modes_must_be_nonempty_list"})
    return {
        "ok": bool(rows) and not issues,
        "fleet_path": str(path),
        "num_vehicle_rows": len(rows),
        "sources": sorted(sources),
        "issues": issues,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--fleet_jsonl", required=True)
    p.add_argument("--output", required=True)
    args = p.parse_args()
    report = validate(args.fleet_jsonl)
    dump_json(args.output, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    print("CAPPLAN_FLEET_CHECK", "PASS" if report["ok"] else "FAIL")
    if not report["ok"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
