#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capplan.data.capability_contracts import load_profiles
from capplan.data.passenger_service_layer import load_fleet_interfaces, validate_service_request
from capplan.utils.serialization import dump_json, read_jsonl, write_jsonl


def _read_records(path: str | None, key_hint: str | None = None) -> List[Dict[str, Any]]:
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    if p.suffix.lower() in {".yaml", ".yml"}:
        payload = yaml.safe_load(p.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            if key_hint and isinstance(payload.get(key_hint), list):
                return [dict(x) for x in payload[key_hint]]
            for key in ["service_requests", "requests", "records"]:
                if isinstance(payload.get(key), list):
                    return [dict(x) for x in payload[key]]
            return [payload]
        return [dict(x) for x in payload or []]
    return read_jsonl(p)


def build(args: argparse.Namespace) -> Dict[str, Any]:
    if args.service_requests_jsonl:
        rows = [validate_service_request(r) for r in _read_records(args.service_requests_jsonl, "service_requests")]
    elif args.demand_sources_config:
        # This is a schema/dry-run builder: it accepts an already materialized
        # request list inside a demand config.  It does not synthesize OD demand.
        rows = [validate_service_request(r) for r in _read_records(args.demand_sources_config, "service_requests")]
    else:
        raise RuntimeError("paper service layer requires --service_requests_jsonl or a demand config with materialized service_requests; no synthetic OD sampler is used")
    if args.capability_profiles_jsonl:
        profiles = load_profiles(args.capability_profiles_jsonl)
        profile_ids = {str(p.get("profile_id") or p.get("passenger_id")) for p in profiles}
        missing_profiles = sorted({str(r.get("passenger_profile_id")) for r in rows} - profile_ids)
        if missing_profiles:
            raise RuntimeError(f"service requests reference missing capability profile ids: {missing_profiles}")
    else:
        profiles = []
    if args.fleet_jsonl:
        fleet = load_fleet_interfaces(args.fleet_jsonl)
        vehicle_ids = {v.vehicle_id for vs in fleet.values() for v in vs}
        missing_vehicles = sorted({str(r.get("vehicle_id") or r.get("fleet_vehicle_id")) for r in rows if r.get("vehicle_id") or r.get("fleet_vehicle_id")} - vehicle_ids)
        if missing_vehicles:
            raise RuntimeError(f"service requests reference missing fleet vehicle ids: {missing_vehicles}")
    else:
        fleet = {}
    write_jsonl(args.output_service_requests_jsonl, rows)
    report = {"service_requests": len(rows), "profiles_checked": len(profiles), "fleet_episodes_checked": len(fleet), "source": "real_jsonl_or_calibrated_materialized"}
    if args.report_json:
        dump_json(args.report_json, report)
    return report


def main() -> None:
    p = argparse.ArgumentParser(description="Validate/build the passenger-service request layer for paper-mode CapPlan datasets.")
    p.add_argument("--scene_dataset_dir", default=None)
    p.add_argument("--accessibility_graph_dir", default=None)
    p.add_argument("--demand_sources_config", default=None)
    p.add_argument("--service_requests_jsonl", default=None)
    p.add_argument("--capability_profiles_jsonl", default=None)
    p.add_argument("--fleet_jsonl", default=None)
    p.add_argument("--output_service_requests_jsonl", required=True)
    p.add_argument("--report_json", default=None)
    args = p.parse_args()
    print(json.dumps(build(args), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
