#!/usr/bin/env python
"""Prefill a PUDO/entrance audit worklist from already-normalized evidence.

This script is deliberately conservative.  It may copy a field only when the
source is marked authoritative/Tier-A and the field has the same semantic
meaning.  It never treats a parking meter, taxi zone, pickup-bay candidate, OSM
kerb tag, DEM value, or nearest address point as autonomous-mobility stopping
legality / doorway truth / deployment clearance.

The output is still a *review worklist*, not publication ground truth.  Fields
that cannot be supported by explicit evidence stay blank and are listed in
``remaining_required_fields``.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

EARTH_R_M = 6371008.8
CORE_FIELDS = ["curb_height_m", "sidewalk_width_m", "deployment_clearance_m"]
AUTO_FIELDS = CORE_FIELDS + ["curb_ramp", "running_slope", "cross_slope", "surface"]
PAPER_REQUIRED = CORE_FIELDS + [
    "curb_ramp", "legal_stop", "legal_basis", "entrance_id", "entrance_lon", "entrance_lat",
]


def _canon(v: Any) -> str:
    return str(v or "").strip().lower()


def _flatten(row: Dict[str, Any]) -> Dict[str, Any]:
    props = row.get("properties") if isinstance(row.get("properties"), dict) else {}
    return {**props, **{k: v for k, v in row.items() if k not in {"properties", "geometry", "type"}}}


def _iter_rows(path: Path) -> Iterator[Dict[str, Any]]:
    if not path.exists():
        return
    if path.is_dir():
        for child in sorted(path.rglob("*")):
            if not child.is_file() or child.name.endswith((".report.json", ".manifest.json", ".provenance.json")):
                continue
            if child.suffix.lower() in {".json", ".jsonl", ".geojson", ".csv"}:
                yield from _iter_rows(child)
        return
    suffix = path.suffix.lower()
    if suffix in {".json", ".geojson"}:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and payload.get("type") == "FeatureCollection":
            for row in payload.get("features") or []:
                if isinstance(row, dict):
                    yield row
        elif isinstance(payload, list):
            for row in payload:
                if isinstance(row, dict):
                    yield row
        elif isinstance(payload, dict):
            yield payload
        return
    if suffix == ".jsonl":
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    row = json.loads(line)
                    if isinstance(row, dict):
                        yield row
        return
    if suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            yield from csv.DictReader(f)
        return


def _authoritative(row: Dict[str, Any]) -> bool:
    d = _flatten(row)
    tier = _canon(d.get("evidence_tier"))
    return bool(d.get("authoritative") is True or d.get("audited") is True or tier.startswith("a_"))


def _as_float(v: Any) -> Optional[float]:
    if v in (None, "", "unknown", "n/a", "null", "NULL"):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _as_bool(v: Any) -> Optional[bool]:
    if isinstance(v, bool):
        return v
    s = _canon(v)
    if s in {"true", "1", "yes", "y"}:
        return True
    if s in {"false", "0", "no", "n"}:
        return False
    return None


def _evidence_source(row: Dict[str, Any]) -> str:
    d = _flatten(row)
    return str(d.get("source") or d.get("evidence_source") or d.get("dataset") or d.get("name") or "")


def _evidence_tier(row: Dict[str, Any]) -> str:
    d = _flatten(row)
    return str(d.get("evidence_tier") or ("A_authoritative" if _authoritative(row) else "unknown"))


def _evidence_time(row: Dict[str, Any]) -> str:
    d = _flatten(row)
    for key in ["observed_at", "inspection_date", "valid_from", "source_date", "retrieved_at"]:
        if d.get(key) not in (None, ""):
            return str(d[key])
    return ""


def _lonlat_point(row: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    d = _flatten(row)
    geom = row.get("geometry") if isinstance(row.get("geometry"), dict) else None
    if geom and geom.get("type") == "Point":
        coords = geom.get("coordinates")
        if isinstance(coords, list) and len(coords) >= 2:
            try:
                lon, lat = float(coords[0]), float(coords[1])
                if -180 <= lon <= 180 and -90 <= lat <= 90:
                    return lon, lat
            except Exception:
                pass
    for lon_key, lat_key in [("lon", "lat"), ("longitude", "latitude")]:
        if d.get(lon_key) is not None and d.get(lat_key) is not None:
            try:
                lon, lat = float(d[lon_key]), float(d[lat_key])
                if -180 <= lon <= 180 and -90 <= lat <= 90:
                    return lon, lat
            except Exception:
                pass
    return None


def _project(lon: float, lat: float, lon0: float, lat0: float) -> Tuple[float, float]:
    x = math.radians(lon - lon0) * EARTH_R_M * math.cos(math.radians(lat0))
    y = math.radians(lat - lat0) * EARTH_R_M
    return x, y


def _unproject(x: float, y: float, lon0: float, lat0: float) -> Tuple[float, float]:
    lon = lon0 + math.degrees(x / (EARTH_R_M * max(math.cos(math.radians(lat0)), 1e-9)))
    lat = lat0 + math.degrees(y / EARTH_R_M)
    return lon, lat


def _coord_sequences(geom: Dict[str, Any]) -> List[List[Sequence[float]]]:
    typ = geom.get("type")
    c = geom.get("coordinates")
    if not isinstance(c, list):
        return []
    if typ == "LineString":
        return [c]
    if typ == "MultiLineString":
        return [x for x in c if isinstance(x, list)]
    if typ == "Polygon":
        return [x for x in c if isinstance(x, list)]
    if typ == "MultiPolygon":
        out: List[List[Sequence[float]]] = []
        for poly in c:
            if isinstance(poly, list):
                out.extend(x for x in poly if isinstance(x, list))
        return out
    return []


def _distance_to_row(lon: float, lat: float, row: Dict[str, Any]) -> Tuple[float, Optional[Tuple[float, float]]]:
    pt = _lonlat_point(row)
    if pt:
        x, y = _project(pt[0], pt[1], lon, lat)
        return math.hypot(x, y), pt
    geom = row.get("geometry") if isinstance(row.get("geometry"), dict) else {}
    best_d = float("inf")
    best_xy: Optional[Tuple[float, float]] = None
    for seq in _coord_sequences(geom):
        pts: List[Tuple[float, float]] = []
        ll: List[Tuple[float, float]] = []
        for p in seq:
            if isinstance(p, (list, tuple)) and len(p) >= 2:
                try:
                    plon, plat = float(p[0]), float(p[1])
                except Exception:
                    continue
                pts.append(_project(plon, plat, lon, lat)); ll.append((plon, plat))
        if not pts:
            continue
        if len(pts) == 1:
            d = math.hypot(*pts[0])
            if d < best_d:
                best_d, best_xy = d, ll[0]
            continue
        for i in range(len(pts) - 1):
            ax, ay = pts[i]; bx, by = pts[i + 1]
            vx, vy = bx - ax, by - ay
            den = vx * vx + vy * vy
            t = max(0.0, min(1.0, (-(ax * vx + ay * vy)) / den)) if den > 0 else 0.0
            qx, qy = ax + t * vx, ay + t * vy
            d = math.hypot(qx, qy)
            if d < best_d:
                best_d = d; best_xy = _unproject(qx, qy, lon, lat)
    return best_d, best_xy


def _nearest_with_field(lon: float, lat: float, rows: Iterable[Dict[str, Any]], field: str, max_m: float) -> Optional[Tuple[Any, Dict[str, Any], float]]:
    best = None
    for row in rows:
        if not _authoritative(row):
            continue
        d = _flatten(row)
        value = d.get(field)
        if field in {"curb_height_m", "sidewalk_width_m", "deployment_clearance_m", "running_slope", "cross_slope"}:
            value = _as_float(value)
        elif field == "curb_ramp":
            value = _as_bool(value)
        elif field == "surface":
            value = None if value in (None, "") else str(value)
        if value is None:
            continue
        dist, _ = _distance_to_row(lon, lat, row)
        if dist <= max_m and (best is None or dist < best[2]):
            best = (value, row, dist)
    return best


def _service_class_ok(value: Any) -> bool:
    s = _canon(value)
    return s in {"autonomous_mobility", "all", "all_vehicles", "general_passenger_loading"}


def _nearest_regulation(lon: float, lat: float, rows: Iterable[Dict[str, Any]], max_m: float) -> Optional[Tuple[bool, str, Dict[str, Any], float]]:
    best = None
    for row in rows:
        d = _flatten(row)
        if not _authoritative(row):
            continue
        legal = _as_bool(d.get("legal_stop"))
        basis = str(d.get("legal_basis") or "").strip()
        if legal is None or not basis or not _service_class_ok(d.get("service_class") or "autonomous_mobility"):
            continue
        dist, _ = _distance_to_row(lon, lat, row)
        if dist <= max_m and (best is None or dist < best[3]):
            best = (legal, basis, row, dist)
    return best


def _nearest_entrance(lon: float, lat: float, rows: Iterable[Dict[str, Any]], max_m: float) -> Optional[Tuple[str, float, float, Dict[str, Any], float]]:
    best = None
    for row in rows:
        d = _flatten(row)
        if not _authoritative(row) or bool(d.get("is_proxy")) or _canon(d.get("kind")) == "entrance_proxy":
            continue
        eid = str(d.get("entrance_id") or d.get("id") or d.get("feature_id") or "").strip()
        pt = _lonlat_point(row)
        if not eid or not pt:
            continue
        dist, _ = _distance_to_row(lon, lat, row)
        if dist <= max_m and (best is None or dist < best[4]):
            best = (eid, pt[0], pt[1], row, dist)
    return best


def _blank(v: Any) -> bool:
    return v is None or str(v).strip() == ""


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input_csv", required=True)
    p.add_argument("--city", required=True, choices=["boston", "pittsburgh", "vegas", "singapore"])
    p.add_argument("--external_root", required=True)
    p.add_argument("--output_csv", required=True)
    p.add_argument("--report_json", default=None)
    p.add_argument("--physical_match_m", type=float, default=15.0)
    p.add_argument("--regulation_match_m", type=float, default=12.0)
    p.add_argument("--entrance_candidate_m", type=float, default=80.0)
    p.add_argument("--accept_verified_nearest_entrance", action="store_true", help="Fill entrance_id/lon/lat only from an authoritative non-proxy entrance layer. Keep off when trip entrance identity still needs human/service-request review.")
    args = p.parse_args()

    root = Path(args.external_root)
    city_gis = root / "normalized" / "city_gis" / args.city
    curb_inventory = root / "normalized" / "curb_inventory" / f"{args.city}.jsonl"
    regulations_path = root / "normalized" / "curb_regulations" / f"{args.city}.jsonl"
    entrances_path = root / "normalized" / "entrances" / f"{args.city}.geojson"

    physical_rows = list(_iter_rows(city_gis)) + list(_iter_rows(curb_inventory))
    regulation_rows = list(_iter_rows(regulations_path))
    entrance_rows = list(_iter_rows(entrances_path))

    with Path(args.input_csv).open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        input_fields = list(reader.fieldnames or [])
        rows = [dict(x) for x in reader]
    if not rows:
        raise RuntimeError("input audit shortlist is empty")

    extra_fields = []
    for field in AUTO_FIELDS:
        extra_fields += [f"{field}_source", f"{field}_evidence_tier", f"{field}_match_distance_m", f"{field}_evidence_as_of"]
    extra_fields += [
        "legal_stop_source", "legal_stop_evidence_tier", "legal_stop_match_distance_m", "legal_stop_evidence_as_of",
        "entrance_candidate_id", "entrance_candidate_lon", "entrance_candidate_lat", "entrance_candidate_source",
        "entrance_candidate_evidence_tier", "entrance_candidate_match_distance_m", "entrance_candidate_evidence_as_of",
        "auto_filled_fields", "remaining_required_fields", "audit_work_status",
    ]
    out_fields = input_fields + [x for x in extra_fields if x not in input_fields]

    counts = {"rows": len(rows), "source_prefilled_rows": 0, "source_complete_rows": 0, "manual_required_rows": 0}
    field_counts = {x: 0 for x in PAPER_REQUIRED}
    entrance_candidates = 0
    out_rows = []
    for row in rows:
        try:
            lon, lat = float(row["lon"]), float(row["lat"])
        except Exception as exc:
            raise RuntimeError(f"audit row {row.get('audit_id')} has invalid lon/lat") from exc
        auto = []
        for field in AUTO_FIELDS:
            if not _blank(row.get(field)):
                continue
            hit = _nearest_with_field(lon, lat, physical_rows, field, args.physical_match_m)
            if hit is None:
                continue
            value, evidence, dist = hit
            row[field] = str(value).lower() if isinstance(value, bool) else value
            row[f"{field}_source"] = _evidence_source(evidence)
            row[f"{field}_evidence_tier"] = _evidence_tier(evidence)
            row[f"{field}_match_distance_m"] = f"{dist:.3f}"
            row[f"{field}_evidence_as_of"] = _evidence_time(evidence)
            auto.append(field)

        if _blank(row.get("legal_stop")) or _blank(row.get("legal_basis")):
            hit = _nearest_regulation(lon, lat, regulation_rows, args.regulation_match_m)
            if hit is not None:
                legal, basis, evidence, dist = hit
                row["legal_stop"] = str(legal).lower()
                row["legal_basis"] = basis
                row["legal_stop_source"] = _evidence_source(evidence)
                row["legal_stop_evidence_tier"] = _evidence_tier(evidence)
                row["legal_stop_match_distance_m"] = f"{dist:.3f}"
                row["legal_stop_evidence_as_of"] = _evidence_time(evidence)
                auto += ["legal_stop", "legal_basis"]

        ent = _nearest_entrance(lon, lat, entrance_rows, args.entrance_candidate_m)
        if ent is not None:
            eid, elon, elat, evidence, dist = ent
            entrance_candidates += 1
            row["entrance_candidate_id"] = eid
            row["entrance_candidate_lon"] = f"{elon:.8f}"
            row["entrance_candidate_lat"] = f"{elat:.8f}"
            row["entrance_candidate_source"] = _evidence_source(evidence)
            row["entrance_candidate_evidence_tier"] = _evidence_tier(evidence)
            row["entrance_candidate_match_distance_m"] = f"{dist:.3f}"
            row["entrance_candidate_evidence_as_of"] = _evidence_time(evidence)
            if args.accept_verified_nearest_entrance and all(_blank(row.get(k)) for k in ["entrance_id", "entrance_lon", "entrance_lat"]):
                row["entrance_id"] = eid; row["entrance_lon"] = f"{elon:.8f}"; row["entrance_lat"] = f"{elat:.8f}"
                auto += ["entrance_id", "entrance_lon", "entrance_lat"]

        remaining = [f for f in PAPER_REQUIRED if _blank(row.get(f))]
        # observed_at and auditor_id are evidence of a human review, not physical
        # quantities. They remain required for the default manual-audit importer.
        for f in ["observed_at", "auditor_id"]:
            if _blank(row.get(f)):
                remaining.append(f)
        row["auto_filled_fields"] = ";".join(sorted(set(auto)))
        row["remaining_required_fields"] = ";".join(remaining)
        if auto:
            counts["source_prefilled_rows"] += 1
        if not [f for f in PAPER_REQUIRED if _blank(row.get(f))]:
            counts["source_complete_rows"] += 1
            row["audit_work_status"] = "SOURCE_EVIDENCE_COMPLETE_REVIEW_REQUIRED"
        else:
            counts["manual_required_rows"] += 1
            row["audit_work_status"] = "MANUAL_OR_BETTER_SOURCE_REQUIRED"
        for f in PAPER_REQUIRED:
            if not _blank(row.get(f)):
                field_counts[f] += 1
        out_rows.append(row)

    out = Path(args.output_csv); out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=out_fields, extrasaction="ignore")
        w.writeheader(); w.writerows(out_rows)

    report = {
        "status": "PASS",
        "city": args.city,
        **counts,
        "field_coverage": {k: field_counts[k] / max(len(rows), 1) for k in field_counts},
        "entrance_candidate_rows": entrance_candidates,
        "authoritative_physical_records_loaded": sum(1 for x in physical_rows if _authoritative(x)),
        "authoritative_regulation_records_loaded": sum(1 for x in regulation_rows if _authoritative(x)),
        "authoritative_entrance_records_loaded": sum(1 for x in entrance_rows if _authoritative(x)),
        "output_csv": str(out),
        "interpretation": "Autofill is evidence-preserving only. Blank fields are intentional and must not be guessed for paper labels.",
    }
    if args.report_json:
        rp = Path(args.report_json); rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    print("PUDO_AUDIT_PREFILL_CHECK=PASS")


if __name__ == "__main__":
    main()
