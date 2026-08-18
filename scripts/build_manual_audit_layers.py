#!/usr/bin/env python
"""Convert independently audited curb/PUDO/entrance observations into Tier-A layers.

The builder is deliberately fail-closed.  It validates timestamps, physical
ranges, legality basis and (in --paper_mode) the approach attributes required by
CapPlan contracts.  Existing normalized public layers are merged by default so
manual audit does not accidentally erase official topology/asset evidence.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capplan.data.evidence_policy import has_timezone_iso8601, validate_physical_ranges

REQUIRED = [
    "audit_id", "city", "lon", "lat", "curb_height_m", "sidewalk_width_m",
    "deployment_clearance_m", "curb_ramp", "legal_stop", "legal_basis", "observed_at", "auditor_id",
]
PAPER_PUDO_APPROACH = ["running_slope", "cross_slope", "surface"]
PAPER_ENTRANCE_APPROACH = [
    "entrance_access_width_m", "entrance_running_slope", "entrance_cross_slope",
    "entrance_surface", "entrance_step_free",
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


def optional_bool(v: Any) -> Optional[bool]:
    return None if v in (None, "") else as_bool(str(v), "optional_bool")


def _read_jsonl_if_exists(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _merge_jsonl(existing: List[Dict[str, Any]], audited: List[Dict[str, Any]], keys: tuple[str, ...]) -> List[Dict[str, Any]]:
    out = list(existing)
    index: Dict[str, int] = {}
    for i, row in enumerate(out):
        key = next((str(row.get(k)) for k in keys if row.get(k) not in (None, "")), "")
        if key:
            index[key] = i
    for row in audited:
        key = next((str(row.get(k)) for k in keys if row.get(k) not in (None, "")), "")
        if key and key in index:
            previous = dict(out[index[key]])
            merged = {**previous, **row}
            prev_prov = previous.get("field_provenance") if isinstance(previous.get("field_provenance"), dict) else {}
            new_prov = row.get("field_provenance") if isinstance(row.get("field_provenance"), dict) else {}
            if prev_prov or new_prov:
                merged["field_provenance"] = {**prev_prov, **new_prov}
            out[index[key]] = merged
        else:
            if key:
                index[key] = len(out)
            out.append(row)
    return out


def write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _field_provenance(source: str, observed_at: str, auditor: str, fields: List[str], row: Dict[str, Any] | None = None) -> Dict[str, Any]:
    row = row or {}
    out: Dict[str, Any] = {}
    for k in fields:
        fsrc = str(row.get(f"{k}_source") or source).strip()
        tier = str(row.get(f"{k}_tier") or "A_manual_audit").strip()
        out[k] = {"source": fsrc, "evidence_tier": tier}
        if observed_at:
            out[k]["observed_at"] = observed_at
        if auditor:
            out[k]["auditor_id"] = auditor
    return out


def _tier_a_value(row: Dict[str, Any], field: str) -> bool:
    return bool(str(row.get(field) or "").strip() and str(row.get(f"{field}_source") or "").strip() and str(row.get(f"{field}_tier") or "").strip().lower().startswith("a"))


def _automatic_tier_a_complete(row: Dict[str, Any], require_entrance: bool) -> bool:
    physical = ["curb_height_m", "sidewalk_width_m", "deployment_clearance_m", "curb_ramp", "running_slope", "cross_slope", "surface"]
    if not all(_tier_a_value(row, k) for k in physical):
        return False
    if not (_tier_a_value(row, "legal_stop") and str(row.get("legal_basis") or "").strip()):
        return False
    if require_entrance:
        if not all(str(row.get(k) or "").strip() for k in ["entrance_id", "entrance_lon", "entrance_lat"]):
            return False
        for k in PAPER_ENTRANCE_APPROACH:
            if not _tier_a_value(row, k):
                return False
    return True


def _load_existing_entrances(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [dict(x) for x in payload.get("features", [])]


def _merge_entrances(existing: List[Dict[str, Any]], audited: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = list(existing)
    idx: Dict[str, int] = {}
    for i, f in enumerate(out):
        p = f.get("properties", {}) if isinstance(f.get("properties"), dict) else {}
        eid = str(p.get("entrance_id") or p.get("feature_id") or "")
        if eid:
            idx[eid] = i
    for f in audited:
        p = f.get("properties", {}) if isinstance(f.get("properties"), dict) else {}
        eid = str(p.get("entrance_id") or "")
        if eid and eid in idx:
            previous = dict(out[idx[eid]])
            prev_props = previous.get("properties") if isinstance(previous.get("properties"), dict) else {}
            new_props = f.get("properties") if isinstance(f.get("properties"), dict) else {}
            prev_prov = prev_props.get("field_provenance") if isinstance(prev_props.get("field_provenance"), dict) else {}
            new_prov = new_props.get("field_provenance") if isinstance(new_props.get("field_provenance"), dict) else {}
            merged_props = {**prev_props, **new_props}
            if prev_prov or new_prov:
                merged_props["field_provenance"] = {**prev_prov, **new_prov}
            out[idx[eid]] = {**previous, **f, "properties": merged_props}
        else:
            if eid:
                idx[eid] = len(out)
            out.append(f)
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input_csv", required=True)
    p.add_argument("--city", required=True, choices=["boston", "pittsburgh", "vegas", "singapore"])
    p.add_argument("--external_root", required=True, help=".../CapPlan/data/external")
    p.add_argument("--allow_anonymous_auditor", action="store_true")
    p.add_argument("--paper_mode", action="store_true", help="Require all approach fields needed by paper capability contracts.")
    p.add_argument("--allow_automatic_tier_a", action="store_true", help="Allow a row without human auditor/timestamp only when every required field carries independent Tier-A field provenance. Retrieval/version time must still be frozen in provenance_registry.yaml.")
    p.add_argument("--replace_normalized_layers", action="store_true", help="Replace rather than merge existing official normalized layers. Not recommended.")
    p.add_argument("--report_json", default=None, help="Optional JSON report path under external/reports.")
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
    entrance_rows_by_id: Dict[str, Dict[str, Any]] = {}
    for line_no, row in enumerate(raw_rows, 2):
        aid = row["audit_id"].strip()
        if not aid or aid in seen:
            raise RuntimeError(f"line {line_no}: missing/duplicate audit_id {aid!r}")
        seen.add(aid)
        if row["city"].strip().lower() != args.city:
            raise RuntimeError(f"line {line_no}: city is {row['city']!r}, expected {args.city!r}")
        auditor = row["auditor_id"].strip()
        auto_complete = args.allow_automatic_tier_a and _automatic_tier_a_complete(row, require_entrance=args.paper_mode)
        if not auditor and not args.allow_anonymous_auditor and not auto_complete:
            raise RuntimeError(f"line {line_no}: auditor_id is required unless --allow_automatic_tier_a and all required fields have Tier-A field provenance")
        lon = as_float(row["lon"], "lon"); lat = as_float(row["lat"], "lat")
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            raise RuntimeError(f"line {line_no}: invalid WGS84 coordinate")
        observed_at = row["observed_at"].strip()
        if observed_at and not has_timezone_iso8601(observed_at):
            raise RuntimeError(f"line {line_no}: observed_at must be an ISO-8601 timestamp with timezone, got {observed_at!r}")
        if not observed_at and not auto_complete:
            raise RuntimeError(f"line {line_no}: observed_at is required for manual evidence; automatic Tier-A evidence may omit it because source retrieval/version time is tracked separately")
        legal_basis = row["legal_basis"].strip()
        if not legal_basis:
            raise RuntimeError(f"line {line_no}: legal_basis is required and must cite independent evidence")
        if args.paper_mode:
            missing_paper = [k for k in PAPER_PUDO_APPROACH if not str(row.get(k) or "").strip()]
            if missing_paper:
                raise RuntimeError(f"line {line_no}: paper_mode missing PUDO approach fields: {', '.join(missing_paper)}")

        audit_method = (row.get("audit_method") or "manual_audit").strip()
        source = f"evidence_fusion:{args.city}:{aid}" if auto_complete and not auditor else f"manual_audit:{args.city}:{aid}"
        record_audited = bool(auditor)
        record_tier = "A_manual_audit" if record_audited else "A_automatic_official_fusion"
        inv = {
            "id": aid, "site_id": aid, "lon": lon, "lat": lat, "frame": "wgs84", "kind": "curb_interface",
            "curb_height_m": as_float(row["curb_height_m"], "curb_height_m"),
            "sidewalk_width_m": as_float(row["sidewalk_width_m"], "sidewalk_width_m"),
            "deployment_clearance_m": as_float(row["deployment_clearance_m"], "deployment_clearance_m"),
            "curb_ramp": as_bool(row["curb_ramp"], "curb_ramp"),
            "running_slope": optional_float(row.get("running_slope")),
            "cross_slope": optional_float(row.get("cross_slope")),
            "surface": (row.get("surface") or "").strip() or None,
            "permanent_obstruction": optional_bool(row.get("permanent_obstruction")),
            "lighting": (row.get("lighting") or "").strip() or None,
            "shelter": optional_bool(row.get("shelter")),
            "source": source, "authoritative": True, "audited": record_audited, "evidence_tier": record_tier, "confidence": 1.0,
            "observed_at": observed_at, "auditor_id": auditor, "photo_ref": row.get("photo_ref") or None,
            "evidence_time_semantics": (row.get("evidence_time_semantics") or "static_reference_snapshot_not_historical_dynamic_truth").strip(),
        }
        range_errors = validate_physical_ranges(inv)
        if range_errors:
            raise RuntimeError(f"line {line_no}: invalid physical values: {', '.join(range_errors)}")
        inv["field_provenance"] = _field_provenance(source, observed_at, auditor, [
            "curb_height_m", "sidewalk_width_m", "deployment_clearance_m", "curb_ramp",
            "running_slope", "cross_slope", "surface", "permanent_obstruction", "lighting", "shelter",
        ], row)
        reg = {
            "regulation_id": aid, "site_id": aid, "lon": lon, "lat": lat, "frame": "wgs84",
            "legal_stop": as_bool(row["legal_stop"], "legal_stop"),
            "service_class": (row.get("service_class") or "autonomous_mobility").strip(),
            "legal_basis": legal_basis,
            "time_window": (row.get("time_window") or "audited_snapshot").strip(),
            "source": source, "authoritative": True, "audited": record_audited, "evidence_tier": record_tier, "confidence": 1.0,
            "observed_at": observed_at, "auditor_id": auditor,
            "evidence_time_semantics": (row.get("legal_time_semantics") or row.get("evidence_time_semantics") or "service_rule_snapshot_unless_validity_window_covers_scene").strip(),
            "candidate_only": False, "requires_manual_legality_audit": False,
            "field_provenance": {"legal_stop": {
                "source": str(row.get("legal_stop_source") or source),
                "evidence_tier": str(row.get("legal_stop_tier") or record_tier),
                "legal_basis": legal_basis, **({"observed_at": observed_at} if observed_at else {}),
                **({"auditor_id": auditor} if auditor else {}),
            }},
        }
        inventory.append(inv); regulations.append(reg)

        entrance_id = (row.get("entrance_id") or "").strip()
        if entrance_id:
            entrance_lon = optional_float(row.get("entrance_lon")); entrance_lat = optional_float(row.get("entrance_lat"))
            if entrance_lon is None or entrance_lat is None:
                raise RuntimeError(
                    f"line {line_no}: entrance_id={entrance_id!r} requires independent entrance_lon/entrance_lat; "
                    "leave entrance_id blank when the entrance has not been audited"
                )
            if not (-180 <= entrance_lon <= 180 and -90 <= entrance_lat <= 90):
                raise RuntimeError(f"line {line_no}: invalid entrance WGS84 coordinate")
            if args.paper_mode:
                missing_ent = [k for k in PAPER_ENTRANCE_APPROACH if not str(row.get(k) or "").strip()]
                if missing_ent:
                    raise RuntimeError(f"line {line_no}: paper_mode entrance {entrance_id!r} missing approach fields: {', '.join(missing_ent)}")
            ent_props = {
                "entrance_id": entrance_id, "kind": "entrance", "is_proxy": False, "source": source,
                "authoritative": True, "audited": record_audited, "evidence_tier": str(row.get("entrance_tier") or record_tier), "confidence": 1.0,
                "observed_at": observed_at, "auditor_id": auditor,
                "width_m": optional_float(row.get("entrance_access_width_m")),
                "sidewalk_width_m": optional_float(row.get("entrance_access_width_m")),
                "slope": optional_float(row.get("entrance_running_slope")),
                "running_slope": optional_float(row.get("entrance_running_slope")),
                "cross_slope": optional_float(row.get("entrance_cross_slope")),
                "surface": (row.get("entrance_surface") or "").strip() or None,
                "step_free": optional_bool(row.get("entrance_step_free")),
                "permanent_obstruction": optional_bool(row.get("entrance_permanent_obstruction")),
            }
            ent_range_errors = validate_physical_ranges({
                "sidewalk_width_m": ent_props["sidewalk_width_m"],
                "running_slope": ent_props["running_slope"], "cross_slope": ent_props["cross_slope"],
            })
            if ent_range_errors:
                raise RuntimeError(f"line {line_no}: invalid entrance approach values: {', '.join(ent_range_errors)}")
            ent_row = dict(row)
            # Map CSV entrance-specific provenance onto canonical graph fields.
            for canonical, csv_field in [("width_m","entrance_access_width_m"),("sidewalk_width_m","entrance_access_width_m"),("slope","entrance_running_slope"),("running_slope","entrance_running_slope"),("cross_slope","entrance_cross_slope"),("surface","entrance_surface"),("step_free","entrance_step_free")]:
                ent_row[f"{canonical}_source"] = row.get(f"{csv_field}_source") or row.get("entrance_source")
                ent_row[f"{canonical}_tier"] = row.get(f"{csv_field}_tier") or row.get("entrance_tier")
            ent_props["field_provenance"] = _field_provenance(str(row.get("entrance_source") or source), observed_at, auditor, [
                "width_m", "sidewalk_width_m", "slope", "running_slope", "cross_slope", "surface", "step_free", "permanent_obstruction",
            ], ent_row)
            entrance_feature = {"type": "Feature", "geometry": {"type": "Point", "coordinates": [entrance_lon, entrance_lat]}, "properties": ent_props}
            previous = entrance_rows_by_id.get(entrance_id)
            if previous is None:
                entrance_rows_by_id[entrance_id] = entrance_feature
                entrances.append(entrance_feature)
            else:
                prev_xy = previous.get("geometry", {}).get("coordinates", [])
                if len(prev_xy) < 2 or abs(float(prev_xy[0]) - entrance_lon) > 1e-7 or abs(float(prev_xy[1]) - entrance_lat) > 1e-7:
                    raise RuntimeError(
                        f"line {line_no}: entrance_id {entrance_id!r} is reused with conflicting coordinates; "
                        "the same audited physical entrance may serve multiple PUDOs, but its identity/geometry must agree"
                    )
                # Reuse is valid and expected: several audited curb sites can serve
                # the same doorway.  Keep one canonical entrance feature instead
                # of treating that service relation as a duplicate-data error.

        manifest.append({
            "audit_id": aid, "city": args.city, "lon": lon, "lat": lat, "observed_at": observed_at,
            "auditor_id": auditor, "photo_ref": row.get("photo_ref") or None, "notes": row.get("notes") or None,
            "measurement_protocol_version": row.get("protocol_version") or "abilitybench_manual_audit_v2",
            "audit_method": audit_method, "manual_confirmed": bool(auditor),
            "source": source, "authoritative": True, "audited": record_audited, "evidence_tier": record_tier,
            "candidate_anchor_ids": row.get("candidate_anchor_ids") or None,
            "episode_ids": row.get("episode_ids") or None,
        })

    inv_path = root / "normalized" / "curb_inventory" / f"{args.city}.jsonl"
    reg_path = root / "normalized" / "curb_regulations" / f"{args.city}.jsonl"
    ent_path = root / "normalized" / "entrances" / f"{args.city}.geojson"
    if args.replace_normalized_layers:
        inv_out, reg_out, ent_out = inventory, regulations, entrances
    else:
        inv_out = _merge_jsonl(_read_jsonl_if_exists(inv_path), inventory, ("id", "site_id", "feature_id"))
        reg_out = _merge_jsonl(_read_jsonl_if_exists(reg_path), regulations, ("regulation_id", "site_id", "id"))
        ent_out = _merge_entrances(_load_existing_entrances(ent_path), entrances)
    write_jsonl(inv_path, inv_out)
    write_jsonl(reg_path, reg_out)
    if ent_out:
        ent_path.parent.mkdir(parents=True, exist_ok=True)
        ent_path.write_text(json.dumps({
            "type": "FeatureCollection", "features": ent_out,
            "properties": {"source": "merged_public_plus_manual_audit", "manual_audit_present": bool(entrances)},
        }, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    write_jsonl(root / "audits" / args.city / "manual_audit_manifest.jsonl", manifest)
    report = {
        "status": "PASS", "city": args.city, "paper_mode": bool(args.paper_mode),
        "observations": len(manifest), "audited_entrances": len(entrances),
        "inventory_records_after_merge": len(inv_out), "regulation_records_after_merge": len(reg_out),
        "entrance_records_after_merge": len(ent_out), "replace_normalized_layers": bool(args.replace_normalized_layers),
        "external_root": str(root), "curb_inventory": str(inv_path), "curb_regulations": str(reg_path),
        "entrance_layer": str(ent_path) if ent_out else None,
        "manual_audit_manifest": str(root / "audits" / args.city / "manual_audit_manifest.jsonl"),
    }
    if args.report_json:
        rp = Path(args.report_json); rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
