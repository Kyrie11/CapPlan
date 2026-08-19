#!/usr/bin/env python
"""Convert a reviewed PUDO/entrance audit CSV into normalized evidence layers.

The importer is intentionally fail-closed for paper use.  It never invents
measurements or entrance coordinates, preserves field-level source metadata,
and merges reviewed observations with existing normalized evidence instead of
silently replacing municipal/OSM-derived records.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

REQUIRED = [
    "audit_id", "city", "lon", "lat", "curb_height_m", "sidewalk_width_m",
    "deployment_clearance_m", "curb_ramp", "legal_stop", "legal_basis", "observed_at", "auditor_id",
]
PAPER_ENTRANCE = ["entrance_id", "entrance_lon", "entrance_lat"]
PHYSICAL_FIELDS = [
    "curb_height_m", "sidewalk_width_m", "deployment_clearance_m", "curb_ramp",
    "running_slope", "cross_slope", "surface",
]


def as_float(v: Any, name: str) -> float:
    try:
        x = float(v)
    except Exception as exc:
        raise ValueError(f"{name} must be numeric, got {v!r}") from exc
    if not math.isfinite(x):
        raise ValueError(f"{name} must be finite, got {v!r}")
    return x


def as_bool(v: Any, name: str) -> bool:
    s = str(v).strip().lower()
    if s in {"1", "true", "yes", "y"}:
        return True
    if s in {"0", "false", "no", "n"}:
        return False
    raise ValueError(f"{name} must be true/false, got {v!r}")


def optional_float(v: Any) -> Optional[float]:
    return None if v in (None, "") else as_float(v, "optional_float")


def _validate_timestamp(text: str, label: str) -> str:
    value = str(text or "").strip()
    if not value:
        raise RuntimeError(f"{label} is required")
    try:
        # ISO-8601 with a timezone is required for auditable paper snapshots.
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeError(f"{label} must be ISO-8601/RFC3339, got {value!r}") from exc
    if parsed.tzinfo is None:
        raise RuntimeError(f"{label} must include a UTC offset/timezone, got {value!r}")
    return value


def _audit_confidence(row: Dict[str, Any]) -> float:
    raw = row.get("audit_confidence")
    if raw in (None, ""):
        # Audited/reviewed evidence is high-confidence, but 1.0 is reserved for
        # logically exact facts; measurement and legal interpretation are not.
        return 0.95
    value = as_float(raw, "audit_confidence")
    if not (0.0 < value <= 1.0):
        raise RuntimeError(f"audit_confidence must be in (0, 1], got {value}")
    return value


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                row = json.loads(line)
                if isinstance(row, dict):
                    rows.append(row)
    return rows


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _field_sources(row: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for field in PHYSICAL_FIELDS:
        src = str(row.get(f"{field}_source") or "").strip()
        if not src:
            continue
        out[field] = {
            "source": src,
            "evidence_tier": str(row.get(f"{field}_evidence_tier") or "").strip() or None,
            "match_distance_m": optional_float(row.get(f"{field}_match_distance_m")),
            "evidence_as_of": str(row.get(f"{field}_evidence_as_of") or "").strip() or None,
        }
    legal_src = str(row.get("legal_stop_source") or "").strip()
    if legal_src:
        out["legal_stop"] = {
            "source": legal_src,
            "evidence_tier": str(row.get("legal_stop_evidence_tier") or "").strip() or None,
            "match_distance_m": optional_float(row.get("legal_stop_match_distance_m")),
            "evidence_as_of": str(row.get("legal_stop_evidence_as_of") or "").strip() or None,
        }
        out["legal_basis"] = dict(out["legal_stop"])
    ent_src = str(row.get("entrance_source") or row.get("entrance_candidate_source") or "").strip()
    if ent_src:
        out["entrance"] = {
            "source": ent_src,
            "evidence_tier": str(row.get("entrance_evidence_tier") or row.get("entrance_candidate_evidence_tier") or "").strip() or None,
            "match_distance_m": optional_float(row.get("entrance_match_distance_m") or row.get("entrance_candidate_match_distance_m")),
            "evidence_as_of": str(row.get("entrance_evidence_as_of") or row.get("entrance_candidate_evidence_as_of") or "").strip() or None,
        }
    return out


def _merge_jsonl(existing: List[Dict[str, Any]], new_rows: List[Dict[str, Any]], id_keys: Tuple[str, ...]) -> List[Dict[str, Any]]:
    """Merge without duplicating audit rows on rerun.

    Existing non-audit source rows are retained verbatim. A new row replaces an
    existing row only when one of the stable id keys matches exactly.
    """
    out = list(existing)
    index: Dict[Tuple[str, str], int] = {}
    for i, row in enumerate(out):
        for key in id_keys:
            value = row.get(key)
            if value not in (None, ""):
                index[(key, str(value))] = i
    for row in new_rows:
        replace_idx = None
        for key in id_keys:
            value = row.get(key)
            if value not in (None, "") and (key, str(value)) in index:
                replace_idx = index[(key, str(value))]
                break
        if replace_idx is None:
            replace_idx = len(out)
            out.append(row)
        else:
            out[replace_idx] = row
        for key in id_keys:
            value = row.get(key)
            if value not in (None, ""):
                index[(key, str(value))] = replace_idx
    return out


def _load_geojson_features(path: Path) -> tuple[List[Dict[str, Any]], Dict[str, Any]]:
    if not path.exists():
        return [], {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection":
        raise RuntimeError(f"existing entrance layer is not a FeatureCollection: {path}")
    return [x for x in payload.get("features", []) if isinstance(x, dict)], dict(payload.get("properties") or {})


def _entrance_id(feature: Dict[str, Any]) -> str:
    props = feature.get("properties") if isinstance(feature.get("properties"), dict) else {}
    return str(props.get("entrance_id") or props.get("id") or "")


def _merge_entrances(existing: List[Dict[str, Any]], new_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = list(existing)
    by_id = {_entrance_id(x): i for i, x in enumerate(out) if _entrance_id(x)}
    for row in new_rows:
        eid = _entrance_id(row)
        if eid and eid in by_id:
            out[by_id[eid]] = row
        else:
            if eid:
                by_id[eid] = len(out)
            out.append(row)
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input_csv", required=True)
    p.add_argument("--city", required=True, choices=["boston", "pittsburgh", "vegas", "singapore"])
    p.add_argument("--external_root", required=True, help=".../CapPlan/data/external")
    p.add_argument("--paper_mode", action="store_true", help="Require an independently identified entrance for every accepted paper PUDO row.")
    p.add_argument("--allow_anonymous_auditor", action="store_true", help="Bootstrap only. Paper mode always requires auditor_id.")
    p.add_argument("--replace_existing", action="store_true", help="Replace normalized city files instead of safely merging. Not recommended for paper builds.")
    p.add_argument("--report_json", default=None, help="Optional JSON report path under external/reports.")
    args = p.parse_args()
    if args.paper_mode and args.allow_anonymous_auditor:
        raise RuntimeError("paper_mode rejects --allow_anonymous_auditor")

    root = Path(args.external_root)
    input_path = Path(args.input_csv)
    with input_path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames or []
        missing = [x for x in REQUIRED if x not in fields]
        if args.paper_mode:
            missing += [x for x in PAPER_ENTRANCE if x not in fields]
        if missing:
            raise RuntimeError("audit CSV missing columns: " + ", ".join(sorted(set(missing))))
        raw_rows = list(reader)
    if not raw_rows:
        raise RuntimeError("audit CSV contains no observations")

    inventory: List[Dict[str, Any]] = []
    regulations: List[Dict[str, Any]] = []
    entrances: List[Dict[str, Any]] = []
    manifest: List[Dict[str, Any]] = []
    seen: set[str] = set()
    source_prefilled = 0
    for line_no, row in enumerate(raw_rows, 2):
        aid = str(row.get("audit_id") or "").strip()
        if not aid or aid in seen:
            raise RuntimeError(f"line {line_no}: missing/duplicate audit_id {aid!r}")
        seen.add(aid)
        if str(row.get("city") or "").strip().lower() != args.city:
            raise RuntimeError(f"line {line_no}: city is {row.get('city')!r}, expected {args.city!r}")
        auditor = str(row.get("auditor_id") or "").strip()
        if not auditor and (args.paper_mode or not args.allow_anonymous_auditor):
            raise RuntimeError(f"line {line_no}: auditor_id is required")
        lon = as_float(row.get("lon"), "lon")
        lat = as_float(row.get("lat"), "lat")
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            raise RuntimeError(f"line {line_no}: invalid WGS84 coordinate")
        observed_at = _validate_timestamp(str(row.get("observed_at") or ""), f"line {line_no}: observed_at")
        reviewed_at = str(row.get("reviewed_at") or "").strip() or None
        if reviewed_at:
            reviewed_at = _validate_timestamp(reviewed_at, f"line {line_no}: reviewed_at")
        review_method = str(row.get("review_method") or "").strip() or None
        observation_semantics = str(row.get("observation_semantics") or "").strip() or None
        confidence = _audit_confidence(row)
        legal_basis = str(row.get("legal_basis") or "").strip()
        if not legal_basis:
            raise RuntimeError(f"line {line_no}: legal_basis is required and must identify the audited rule/source")
        field_sources = _field_sources(row)
        if field_sources:
            source_prefilled += 1
        review_source = f"reviewed_audit:{args.city}:{aid}"
        auto_filled = [x for x in str(row.get("auto_filled_fields") or "").split(";") if x]

        inv = {
            "id": aid,
            "audit_id": aid,
            "lon": lon,
            "lat": lat,
            "frame": "wgs84",
            "kind": "curb_interface",
            "curb_height_m": as_float(row.get("curb_height_m"), "curb_height_m"),
            "sidewalk_width_m": as_float(row.get("sidewalk_width_m"), "sidewalk_width_m"),
            "deployment_clearance_m": as_float(row.get("deployment_clearance_m"), "deployment_clearance_m"),
            "curb_ramp": as_bool(row.get("curb_ramp"), "curb_ramp"),
            "running_slope": optional_float(row.get("running_slope")),
            "cross_slope": optional_float(row.get("cross_slope")),
            "surface": str(row.get("surface") or "").strip() or None,
            "source": review_source,
            "field_sources": {k: v for k, v in field_sources.items() if k in PHYSICAL_FIELDS},
            "authoritative": True,
            "audited": True,
            "confidence": confidence,
            "observed_at": observed_at,
            "auditor_id": auditor or None,
            "reviewed_at": reviewed_at,
            "review_method": review_method,
            "observation_semantics": observation_semantics,
            "photo_ref": str(row.get("photo_ref") or "").strip() or None,
            "protocol_version": str(row.get("protocol_version") or "abilitybench_site_audit_v2"),
            "auto_filled_fields_reviewed": auto_filled,
        }
        reg = {
            "regulation_id": aid,
            "audit_id": aid,
            "lon": lon,
            "lat": lat,
            "frame": "wgs84",
            "legal_stop": as_bool(row.get("legal_stop"), "legal_stop"),
            "service_class": str(row.get("service_class") or "autonomous_mobility"),
            "legal_basis": legal_basis,
            "time_window": str(row.get("time_window") or "audited_snapshot"),
            "source": review_source,
            "field_sources": {k: v for k, v in field_sources.items() if k in {"legal_stop", "legal_basis"}},
            "authoritative": True,
            "audited": True,
            "confidence": confidence,
            "observed_at": observed_at,
            "auditor_id": auditor or None,
            "reviewed_at": reviewed_at,
            "review_method": review_method,
            "observation_semantics": observation_semantics,
            "protocol_version": str(row.get("protocol_version") or "abilitybench_site_audit_v2"),
        }
        inventory.append(inv)
        regulations.append(reg)

        entrance_id = str(row.get("entrance_id") or "").strip()
        if args.paper_mode and not entrance_id:
            raise RuntimeError(f"line {line_no}: paper_mode requires entrance_id with independent entrance coordinates")
        if entrance_id:
            entrance_lon = optional_float(row.get("entrance_lon"))
            entrance_lat = optional_float(row.get("entrance_lat"))
            if entrance_lon is None or entrance_lat is None:
                raise RuntimeError(
                    f"line {line_no}: entrance_id={entrance_id!r} requires independent entrance_lon/entrance_lat; "
                    "never copy curb coordinates into an entrance label"
                )
            if not (-180 <= entrance_lon <= 180 and -90 <= entrance_lat <= 90):
                raise RuntimeError(f"line {line_no}: invalid entrance WGS84 coordinate")
            entrances.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [entrance_lon, entrance_lat]},
                "properties": {
                    "entrance_id": entrance_id,
                    "kind": "entrance",
                    "source": review_source,
                    "field_sources": {"entrance": field_sources.get("entrance")} if field_sources.get("entrance") else {},
                    "authoritative": True,
                    "audited": True,
                    "confidence": confidence,
                    "observed_at": observed_at,
                    "auditor_id": auditor or None,
                    "reviewed_at": reviewed_at,
                    "review_method": review_method,
                    "observation_semantics": observation_semantics,
                    "protocol_version": str(row.get("protocol_version") or "abilitybench_site_audit_v2"),
                },
            })

        manifest.append({
            "audit_id": aid,
            "city": args.city,
            "lon": lon,
            "lat": lat,
            "entrance_id": entrance_id or None,
            "observed_at": observed_at,
            "auditor_id": auditor or None,
            "reviewed_at": reviewed_at,
            "review_method": review_method,
            "observation_semantics": observation_semantics,
            "photo_ref": str(row.get("photo_ref") or "").strip() or None,
            "notes": str(row.get("notes") or "").strip() or None,
            "measurement_protocol_version": str(row.get("protocol_version") or "abilitybench_site_audit_v2"),
            "audit_confidence": confidence,
            "auto_filled_fields_reviewed": auto_filled,
            "field_sources": field_sources,
            "source": review_source,
            "source_worklist": str(input_path.resolve()),
            "authoritative": True,
            "review_complete": True,
        })

    inv_path = root / "normalized" / "curb_inventory" / f"{args.city}.jsonl"
    reg_path = root / "normalized" / "curb_regulations" / f"{args.city}.jsonl"
    ent_path = root / "normalized" / "entrances" / f"{args.city}.geojson"
    manifest_path = root / "audits" / args.city / "manual_audit_manifest.jsonl"

    existing_inv = [] if args.replace_existing else _read_jsonl(inv_path)
    existing_reg = [] if args.replace_existing else _read_jsonl(reg_path)
    existing_manifest = [] if args.replace_existing else _read_jsonl(manifest_path)
    merged_inv = _merge_jsonl(existing_inv, inventory, ("audit_id", "id"))
    merged_reg = _merge_jsonl(existing_reg, regulations, ("audit_id", "regulation_id"))
    merged_manifest = _merge_jsonl(existing_manifest, manifest, ("audit_id",))
    write_jsonl(inv_path, merged_inv)
    write_jsonl(reg_path, merged_reg)
    write_jsonl(manifest_path, merged_manifest)

    existing_features: List[Dict[str, Any]] = []
    existing_properties: Dict[str, Any] = {}
    if not args.replace_existing:
        existing_features, existing_properties = _load_geojson_features(ent_path)
    merged_entrances = _merge_entrances(existing_features, entrances)
    if merged_entrances:
        ent_path.parent.mkdir(parents=True, exist_ok=True)
        ent_path.write_text(json.dumps({
            "type": "FeatureCollection",
            "features": merged_entrances,
            "properties": {
                **existing_properties,
                "reviewed_manual_audit_merged": True,
                "authoritative_review_records": len(entrances),
            },
        }, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    report = {
        "status": "PASS",
        "city": args.city,
        "paper_mode": bool(args.paper_mode),
        "observations_imported": len(manifest),
        "entrances_imported": len(entrances),
        "rows_with_prefilled_field_provenance": source_prefilled,
        "merge_existing": not args.replace_existing,
        "existing_inventory_rows_retained_or_replaced": len(existing_inv),
        "final_inventory_rows": len(merged_inv),
        "existing_regulation_rows_retained_or_replaced": len(existing_reg),
        "final_regulation_rows": len(merged_reg),
        "existing_entrance_features": len(existing_features),
        "final_entrance_features": len(merged_entrances),
        "external_root": str(root),
        "curb_inventory": str(inv_path),
        "curb_regulations": str(reg_path),
        "entrance_layer": str(ent_path) if merged_entrances else None,
        "manual_audit_manifest": str(manifest_path),
        "interpretation": "Reviewed rows are merged into existing source layers; field_sources preserves any authoritative prefill lineage. Paper mode requires a distinct audited entrance anchor.",
    }
    if args.report_json:
        rp = Path(args.report_json)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    print("MANUAL_AUDIT_IMPORT_CHECK=PASS")


if __name__ == "__main__":
    main()
