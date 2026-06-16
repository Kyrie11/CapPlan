#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capplan.utils.serialization import dump_json, read_jsonl, write_jsonl

CORE = ["curb_height_m", "deployment_clearance_m", "sidewalk_width_m"]


def _read(path: str | None) -> List[Dict[str, Any]]:
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    if p.suffix.lower() == ".json":
        payload = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            for key in ["pudo_evidence", "candidates", "records", "features"]:
                if isinstance(payload.get(key), list):
                    return [dict(x) for x in payload[key]]
            return [payload]
        return [dict(x) for x in payload]
    return read_jsonl(p)


def _source_bad(src: Any) -> bool:
    s = str(src or "").lower()
    return s.startswith("synthetic") or "proxy" in s or s in {"toy", "mock"}


def normalize(row: Dict[str, Any], default_source: str) -> Dict[str, Any]:
    anchor_id = row.get("anchor_id") or row.get("pudo_id") or row.get("id")
    if not anchor_id:
        raise ValueError(f"PUDO evidence row missing anchor_id/pudo_id/id: {row}")
    if not row.get("episode_id"):
        raise ValueError(f"PUDO evidence row missing episode_id: {anchor_id}")
    source = row.get("source") or row.get("evidence_source") or default_source
    if _source_bad(source):
        raise ValueError(f"PUDO evidence rejects synthetic/proxy source for {anchor_id}: {source}")
    out = dict(row)
    out["anchor_id"] = str(anchor_id)
    out["pudo_id"] = str(anchor_id)
    out["episode_id"] = str(row["episode_id"])
    out["source"] = str(source)
    out.setdefault("legal_stop", bool(row.get("legal_stop", row.get("vehicle_stop_feasible", False))))
    out.setdefault("legal_stop_source", row.get("regulation_id") or row.get("curb_regulation_source") or source)
    out.setdefault("side", row.get("side", "unknown"))
    if "availability" in row and "dynamic_confidence" not in out:
        out["dynamic_confidence"] = max(0.0, min(1.0, float(row["availability"])))
    if "curb_occupancy" in row and "blockage_risk" not in out:
        out["blockage_risk"] = max(0.0, min(1.0, float(row["curb_occupancy"])))
    out.setdefault("map_confidence", row.get("confidence", 1.0))
    out.setdefault("dynamic_confidence", 1.0 - float(out.get("blockage_risk", 0.0)))
    for k in ["curb_height_m", "deployment_clearance_m", "sidewalk_width_m", "blockage_risk", "map_confidence", "dynamic_confidence"]:
        if out.get(k) is not None:
            out[k] = float(out[k])
    return out


def build(args: argparse.Namespace) -> Dict[str, Any]:
    rows = []
    for p in [args.input_pudo_evidence_jsonl, args.curb_inventory_jsonl, args.curb_regulation_jsonl]:
        rows.extend(_read(p))
    if not rows:
        raise RuntimeError("PUDO evidence build requires real curb/PUDO evidence JSONL; no synthetic fallback is available")
    out_rows = [normalize(r, args.source_name) for r in rows]
    total = max(1, len(out_rows))
    missing = {k: sum(1 for r in out_rows if r.get(k) is None) for k in CORE}
    if args.fail_on_missing_core_evidence:
        bad = {k: v / total for k, v in missing.items() if v / total > args.max_core_missing_rate}
        if bad:
            raise RuntimeError(f"core PUDO evidence missing rate too high: {bad}; threshold={args.max_core_missing_rate}")
    write_jsonl(args.output_pudo_evidence_jsonl, out_rows)
    report = {"rows": len(out_rows), "missing_core_counts": missing, "missing_core_rates": {k: v / total for k, v in missing.items()}, "source": args.source_name}
    if args.report_json:
        dump_json(args.report_json, report)
    return report


def main() -> None:
    p = argparse.ArgumentParser(description="Build audited PUDO evidence JSONL for paper-mode dataset construction.")
    p.add_argument("--scene_dataset_dir", default=None)
    p.add_argument("--accessibility_graph_dir", default=None)
    p.add_argument("--nuplan_map_root", default=None)
    p.add_argument("--curb_regulation_dir", default=None)
    p.add_argument("--city_gis_dir", default=None)
    p.add_argument("--input_pudo_evidence_jsonl", default=None)
    p.add_argument("--curb_inventory_jsonl", default=None)
    p.add_argument("--curb_regulation_jsonl", default=None)
    p.add_argument("--output_pudo_evidence_jsonl", required=True)
    p.add_argument("--candidate_radius_m", type=float, default=250.0)
    p.add_argument("--max_route_deviation_m", type=float, default=300.0)
    p.add_argument("--source_name", default="city_curb_regulation+sidewalk_inventory")
    p.add_argument("--fail_on_missing_core_evidence", action="store_true")
    p.add_argument("--max_core_missing_rate", type=float, default=0.05)
    p.add_argument("--report_json", default=None)
    args = p.parse_args()
    print(json.dumps(build(args), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
