#!/usr/bin/env python
"""Convert a human-audited CSV into curb, regulation, entrance and audit layers."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

REQUIRED = [
    "audit_id", "city", "lon", "lat", "curb_height_m", "sidewalk_width_m",
    "deployment_clearance_m", "curb_ramp", "legal_stop", "legal_basis", "observed_at", "auditor_id",
]


def as_float(v: str, name: str) -> float:
    try:
        return float(v)
    except Exception as exc:
        raise ValueError(f"{name} must be numeric, got {v!r}") from exc


def as_bool(v: str, name: str) -> bool:
    s = str(v).strip().lower()
    if s in {"1", "true", "yes", "y"}:
        return True
    if s in {"0", "false", "no", "n"}:
        return False
    raise ValueError(f"{name} must be true/false, got {v!r}")


def optional_float(v: Any) -> Optional[float]:
    return None if v in (None, "") else float(v)


def write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input_csv", required=True)
    p.add_argument("--city", required=True, choices=["boston", "pittsburgh", "vegas", "singapore"])
    p.add_argument("--external_root", required=True, help=".../CapPlan/data/external")
    p.add_argument("--allow_anonymous_auditor", action="store_true")
    args = p.parse_args()
    root = Path(args.external_root)
    with Path(args.input_csv).open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = [x for x in REQUIRED if x not in (reader.fieldnames or [])]
        if missing:
            raise RuntimeError("audit CSV missing columns: " + ", ".join(missing))
        raw_rows = list(reader)
    if not raw_rows:
        raise RuntimeError("audit CSV contains no observations")
    inventory: List[Dict[str, Any]] = []
    regulations: List[Dict[str, Any]] = []
    entrances: List[Dict[str, Any]] = []
    manifest: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for line_no, row in enumerate(raw_rows, 2):
        aid = row["audit_id"].strip()
        if not aid or aid in seen:
            raise RuntimeError(f"line {line_no}: missing/duplicate audit_id {aid!r}")
        seen.add(aid)
        if row["city"].strip().lower() != args.city:
            raise RuntimeError(f"line {line_no}: city is {row['city']!r}, expected {args.city!r}")
        auditor = row["auditor_id"].strip()
        if not auditor and not args.allow_anonymous_auditor:
            raise RuntimeError(f"line {line_no}: auditor_id is required")
        lon = as_float(row["lon"], "lon"); lat = as_float(row["lat"], "lat")
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            raise RuntimeError(f"line {line_no}: invalid WGS84 coordinate")
        observed_at = row["observed_at"].strip()
        if not observed_at:
            raise RuntimeError(f"line {line_no}: observed_at is required")
        source = f"manual_audit:{args.city}:{aid}"
        inv = {
            "id": aid, "lon": lon, "lat": lat, "frame": "wgs84", "kind": "curb_interface",
            "curb_height_m": as_float(row["curb_height_m"], "curb_height_m"),
            "sidewalk_width_m": as_float(row["sidewalk_width_m"], "sidewalk_width_m"),
            "deployment_clearance_m": as_float(row["deployment_clearance_m"], "deployment_clearance_m"),
            "curb_ramp": as_bool(row["curb_ramp"], "curb_ramp"),
            "running_slope": optional_float(row.get("running_slope")),
            "cross_slope": optional_float(row.get("cross_slope")),
            "surface": row.get("surface") or None,
            "source": source, "authoritative": True, "audited": True, "confidence": 1.0,
            "observed_at": observed_at, "auditor_id": auditor, "photo_ref": row.get("photo_ref") or None,
        }
        reg = {
            "regulation_id": aid, "lon": lon, "lat": lat, "frame": "wgs84",
            "legal_stop": as_bool(row["legal_stop"], "legal_stop"),
            "service_class": row.get("service_class") or "autonomous_mobility",
            "legal_basis": row["legal_basis"].strip(),
            "time_window": row.get("time_window") or "audited_snapshot",
            "source": source, "authoritative": True, "audited": True, "confidence": 1.0,
            "observed_at": observed_at, "auditor_id": auditor,
        }
        inventory.append(inv); regulations.append(reg)
        entrance_id = (row.get("entrance_id") or "").strip()
        if entrance_id:
            entrances.append({"type": "Feature", "geometry": {"type": "Point", "coordinates": [lon, lat]}, "properties": {
                "entrance_id": entrance_id, "kind": "entrance", "source": source, "authoritative": True,
                "audited": True, "confidence": 1.0, "observed_at": observed_at,
            }})
        manifest.append({
            "audit_id": aid, "city": args.city, "lon": lon, "lat": lat, "observed_at": observed_at,
            "auditor_id": auditor, "photo_ref": row.get("photo_ref") or None, "notes": row.get("notes") or None,
            "measurement_protocol_version": row.get("protocol_version") or "abilitybench_manual_audit_v1",
            "source": source, "authoritative": True,
        })
    write_jsonl(root / "normalized" / "curb_inventory" / f"{args.city}.jsonl", inventory)
    write_jsonl(root / "normalized" / "curb_regulations" / f"{args.city}.jsonl", regulations)
    if entrances:
        pth = root / "normalized" / "entrances" / f"{args.city}.geojson"
        pth.parent.mkdir(parents=True, exist_ok=True)
        pth.write_text(json.dumps({"type": "FeatureCollection", "features": entrances, "properties": {"source": "manual_audit", "authoritative": True}}, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_jsonl(root / "audits" / args.city / "manual_audit_manifest.jsonl", manifest)
    print(json.dumps({"city": args.city, "observations": len(manifest), "entrances": len(entrances), "external_root": str(root)}, indent=2))


if __name__ == "__main__":
    main()
