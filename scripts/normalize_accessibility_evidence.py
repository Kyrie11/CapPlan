#!/usr/bin/env python
"""Normalize public GIS layers into conservative CapPlan evidence schemas.

Key rule: never infer an accessibility quantity from a semantically different
field. Unknown source units stay unknown unless the caller supplies an explicit
unit. Candidate/proxy layers remain labelled as such and are not promoted to
paper ground truth by this script.

Vector inputs supported directly: GeoJSON/JSON/JSONL/CSV plus SHP, GPKG and ZIP
(shapefile archives) when `ogr2ogr` from GDAL is installed.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple


def _ogr2ogr_geojsonseq(path: Path, *, layer: str | None = None, source_crs: str | None = None) -> Path:
    ogr2ogr = shutil.which("ogr2ogr")
    if not ogr2ogr:
        raise RuntimeError(
            f"{path.suffix} input requires GDAL ogr2ogr. Install `gdal-bin`, "
            "or convert the source to EPSG:4326 GeoJSON first."
        )
    tmp_root = Path(tempfile.mkdtemp(prefix="capplan_vector_"))
    src = path
    if path.suffix.lower() == ".zip":
        if not zipfile.is_zipfile(path):
            raise RuntimeError(f"not a valid ZIP archive: {path}")
        with zipfile.ZipFile(path) as zf:
            zf.extractall(tmp_root / "unzipped")
        shapefiles = sorted((tmp_root / "unzipped").rglob("*.shp"))
        if not shapefiles:
            raise RuntimeError(f"ZIP contains no .shp: {path}")
        if len(shapefiles) > 1 and not layer:
            names = [x.stem for x in shapefiles]
            raise RuntimeError(f"ZIP contains multiple shapefiles {names}; specify --layer")
        if layer:
            matches = [x for x in shapefiles if x.stem == layer]
            if not matches:
                raise RuntimeError(f"layer {layer!r} not found in {path}; choices={[x.stem for x in shapefiles]}")
            src = matches[0]
        else:
            src = shapefiles[0]
    out = tmp_root / "converted.geojsonl"
    cmd = [ogr2ogr, "-f", "GeoJSONSeq"]
    if source_crs:
        cmd.extend(["-s_srs", source_crs])
    cmd.extend(["-t_srs", "EPSG:4326", str(out), str(src)])
    if layer and path.suffix.lower() in {".gpkg", ".sqlite"}:
        cmd.append(layer)
    subprocess.check_call(cmd)
    return out


def read_rows(path: Path, *, layer: str | None = None, input_crs: str | None = None) -> Iterator[Dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix in {".shp", ".gpkg", ".sqlite", ".zip"}:
        converted = _ogr2ogr_geojsonseq(path, layer=layer, source_crs=input_crs)
        yield from read_rows(converted)
        return
    if suffix == ".geojson" and input_crs and input_crs.upper() != "EPSG:4326":
        converted = _ogr2ogr_geojsonseq(path, layer=layer, source_crs=input_crs)
        yield from read_rows(converted)
        return
    if suffix == ".geojson" and path.stat().st_size > 64 * 1024 * 1024 and shutil.which("ogr2ogr"):
        # Large citywide GeoJSON (e.g. county address points) can be hundreds
        # of MB. Convert to newline-delimited GeoJSON features so normalization
        # stays streaming instead of loading the entire FeatureCollection.
        converted = _ogr2ogr_geojsonseq(path, layer=layer, source_crs=input_crs)
        yield from read_rows(converted)
        return
    if suffix in {".json", ".geojson"}:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and payload.get("type") == "FeatureCollection":
            yield from payload.get("features") or []
        elif isinstance(payload, list):
            yield from payload
        elif isinstance(payload, dict):
            yield payload
        return
    if suffix in {".jsonl", ".ndjson", ".geojsonl"}:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    yield json.loads(line)
        return
    if suffix == ".csv":
        with path.open("rb") as bf:
            head = bf.read(512).lstrip().lower()
        if head.startswith((b"<html", b"<!doctype html")):
            raise RuntimeError(f"CSV input is actually an HTML page/error response: {path}")
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            if not reader.fieldnames:
                raise RuntimeError(f"CSV has no header row: {path}")
            yield from reader
        return
    raise ValueError(f"unsupported input suffix: {suffix}")


def props(row: Dict[str, Any]) -> Dict[str, Any]:
    p = row.get("properties") if isinstance(row.get("properties"), dict) else {}
    return {**p, **{k: v for k, v in row.items() if k not in {"properties", "geometry", "type"}}}


def _canon_key(value: Any) -> str:
    text = str(value).replace("\ufeff", "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text


def first(d: Dict[str, Any], *keys: str) -> Any:
    # Public CSV headers frequently contain BOMs, spaces, dashes or case
    # differences. Canonicalize only the *field name*; never reinterpret the
    # field value or its units.
    lower = {_canon_key(k): v for k, v in d.items()}
    for key in keys:
        value = lower.get(_canon_key(key))
        if value not in (None, "", "unknown", "n/a", "NULL", "null"):
            return value
    return None


def number(value: Any) -> Optional[float]:
    if value in (None, "", "unknown", "n/a", "NULL", "null"):
        return None
    try:
        text = str(value).strip().lower().replace(",", "")
        for suffix in ("meters", "meter", "metres", "metre", "feet", "foot", "ft", "inches", "inch", "degrees", "degree", "deg", "in", "m", "%"):
            if text.endswith(suffix):
                text = text[: -len(suffix)].strip()
                break
        return float(text)
    except Exception:
        return None


def length_to_m(value: Any, unit: str) -> Optional[float]:
    x = number(value)
    if x is None or unit == "unknown":
        return None
    return x if unit == "m" else x * 0.3048 if unit == "feet" else x * 0.0254 if unit == "inches" else None


def slope_to_ratio(value: Any, unit: str) -> Optional[float]:
    x = number(value)
    if x is None or unit == "unknown":
        return None
    if unit == "ratio":
        return x
    if unit == "percent":
        return x / 100.0
    if unit == "degrees":
        return math.tan(math.radians(x))
    return None


def _geometry_from_text(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        obj = json.loads(text)
        if isinstance(obj, dict) and obj.get("type") and obj.get("coordinates") is not None:
            return obj
    except Exception:
        pass
    # Minimal, deterministic WKT support for public CSV layers. We only parse
    # geometry types whose coordinate structure is unambiguous here; other WKT
    # is left unknown rather than guessed. Z/M ordinates are accepted but only
    # X/Y are retained because the fusion pipeline is 2-D.
    m = re.fullmatch(r"POINT(?:\s+(?:Z|M|ZM))?\s*\(\s*([-+0-9.eE]+)\s+([-+0-9.eE]+)(?:\s+[-+0-9.eE]+){0,2}\s*\)", text, flags=re.I)
    if m:
        return {"type": "Point", "coordinates": [float(m.group(1)), float(m.group(2))]}

    m = re.fullmatch(r"LINESTRING(?:\s+(?:Z|M|ZM))?\s*\((.*)\)", text, flags=re.I)
    if m:
        coords = []
        try:
            for token in m.group(1).split(","):
                values = token.strip().split()
                if len(values) < 2:
                    return None
                coords.append([float(values[0]), float(values[1])])
        except ValueError:
            return None
        if len(coords) >= 2:
            return {"type": "LineString", "coordinates": coords}
    return None


def geometry(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if isinstance(row.get("geometry"), dict):
        return row["geometry"]
    d = props(row)
    return _geometry_from_text(first(d, "geometry", "geom", "wkt"))


def _valid_wgs84(lon: Any, lat: Any) -> Optional[Tuple[float, float]]:
    try:
        x, y = float(str(lon).strip()), float(str(lat).strip())
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(x) and math.isfinite(y)):
        return None
    if not (-180.0 <= x <= 180.0 and -90.0 <= y <= 90.0):
        return None
    return x, y


def representative_point(row: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    geom = geometry(row)
    if geom:
        coords = geom.get("coordinates")
        points: List[Tuple[float, float]] = []
        def walk(v: Any) -> None:
            if isinstance(v, list) and len(v) >= 2 and all(isinstance(x, (int, float)) for x in v[:2]):
                points.append((float(v[0]), float(v[1])))
            elif isinstance(v, list):
                for item in v:
                    walk(item)
        walk(coords)
        if points:
            xy = (sum(x for x, _ in points) / len(points), sum(y for _, y in points) / len(points))
            if _valid_wgs84(*xy):
                return xy
    d = props(row)
    # Explicit WGS84 aliases used by WPRDC and similar public tabular layers.
    # Plain projected x/y values are intentionally *not* accepted here.
    lon = first(d, "lon", "longitude", "lng", "long", "long_dd", "longitude_dd", "point_x")
    lat = first(d, "lat", "latitude", "lat_dd", "latitude_dd", "point_y")
    if lon is not None and lat is not None:
        return _valid_wgs84(lon, lat)
    return None


def standard_feature(row: Dict[str, Any], updates: Dict[str, Any], *, source: str, authoritative: bool, tier: str) -> Dict[str, Any]:
    p = props(row)
    p.update(updates)  # keep explicit None: missingness is meaningful evidence
    p["source"] = source
    p["authoritative"] = authoritative
    p["evidence_tier"] = tier
    p.setdefault("confidence", 0.9 if authoritative else 0.65)
    return {"type": "Feature", "geometry": geometry(row), "properties": p}


def normalize(profile: str, row: Dict[str, Any], index: int, source: str, args: argparse.Namespace) -> Dict[str, Any]:
    d = props(row)
    if profile == "boston_sidewalk":
        raw_w = first(d, "SWK_WIDTH")
        raw_s = first(d, "SWK_SLOPE")
        return standard_feature(row, {
            "feature_id": str(first(d, "SWK_ID", "OBJECTID") or f"boston_sidewalk_{index}"),
            # Boston Sidewalk Inventory is a polygon layer. Do not route along
            # polygon boundaries; the official Sidewalk Centerline layer is the
            # routable topology source.
            "kind": "sidewalk_inventory_area",
            "source_role": "physical_attribute_inventory",
            "width_m": length_to_m(raw_w, args.width_unit),
            "sidewalk_width_m": length_to_m(raw_w, args.width_unit),
            "slope": slope_to_ratio(raw_s, args.slope_unit),
            "surface": first(d, "MATERIAL"),
            "inspection_date": first(d, "INSP_DATE", "new_insp_d"),
            "raw_SWK_WIDTH": raw_w,
            "raw_SWK_SLOPE": raw_s,
            "unit_mapping": {"SWK_WIDTH": args.width_unit, "SWK_SLOPE": args.slope_unit},
        }, source=source, authoritative=True, tier="A_authoritative_city_gis")

    if profile == "boston_sidewalk_centerline":
        code = str(first(d, "TYPE") or "").strip().upper()
        kind = {"SWALK-CL": "sidewalk", "CWALK-CL": "crossing", "PWALK-CL": "private_walk"}.get(code, "unknown_linear")
        return standard_feature(row, {
            "feature_id": str(first(d, "OBJECTID") or f"boston_centerline_{index}"),
            "kind": kind,
            "centerline_type": code or None,
            "source_role": "pedestrian_topology",
            "public_routable": kind in {"sidewalk", "crossing"},
        }, source=source, authoritative=True, tier="A_authoritative_city_gis")

    if profile == "boston_curb":
        return standard_feature(row, {
            "feature_id": str(first(d, "OBJECTID") or f"boston_curb_{index}"),
            "kind": "curb_line",
            "source_role": "curb_geometry",
        }, source=source, authoritative=True, tier="A_authoritative_city_gis")

    if profile == "boston_ramp":
        xy = representative_point(row)
        if not xy:
            raise ValueError("Boston ramp feature has no geometry")
        raw_width = first(d, "SWK_WIDTH")
        raw_reveal = first(d, "REVEAL")
        raw_apron = first(d, "APRON_SL")
        raw_landing = first(d, "LANDING_SL")
        return {
            "id": str(first(d, "RAMP_ID", "OBJECTID", "ID2") or f"boston_ramp_{index}"),
            "lon": xy[0], "lat": xy[1], "frame": "wgs84", "kind": "curb_ramp", "curb_ramp": True,
            "curb_height_m": length_to_m(raw_reveal, args.reveal_unit),
            "sidewalk_width_m": length_to_m(raw_width, args.width_unit),
            # Critical: nominal sidewalk width is NOT ramp/lift deployment clearance.
            "deployment_clearance_m": None,
            "ramp_slope": slope_to_ratio(raw_apron, args.slope_unit),
            "landing_slope": slope_to_ratio(raw_landing, args.slope_unit),
            "surface": first(d, "SWK_MATL", "MATL"), "condition": first(d, "COND"),
            "inspection_date": first(d, "INSP_DATE"), "source": source, "authoritative": True,
            "evidence_tier": "A_authoritative_city_gis", "confidence": 0.9,
            "raw_fields": {"SWK_WIDTH": raw_width, "REVEAL": raw_reveal, "APRON_SL": raw_apron, "LANDING_SL": raw_landing},
            "unit_mapping": {"SWK_WIDTH": args.width_unit, "REVEAL": args.reveal_unit, "APRON_SL/LANDING_SL": args.slope_unit},
            "requires_manual_deployment_clearance_audit": True,
        }

    if profile == "pittsburgh_sidewalks_steps":
        geom = geometry(row)
        if not isinstance(geom, dict) or geom.get("type") not in {"LineString", "MultiLineString"}:
            raise ValueError(
                "pittsburgh_sidewalks_steps must contain line pedestrian geometry; "
                f"got geometry_type={geom.get('type') if isinstance(geom, dict) else None}. "
                "Do not use the blockgroup/tract ratio table as sidewalk geometry."
            )
        type_name = str(first(d, "Type_Name", "TYPE_NAME", "TYPE", "FEATURE_TYPE") or "sidewalk").strip().lower()
        if "step" in type_name:
            kind = "steps"
        elif "cross" in type_name:
            kind = "crossing"
        elif "trail" in type_name:
            kind = "path"
        else:
            kind = "sidewalk"
        return standard_feature(row, {
            "feature_id": str(first(d, "OBJECTID", "id") or f"pittsburgh_walk_{index}"),
            "kind": kind,
            "source_role": "pedestrian_topology",
            "physical_attributes_authoritative": False,
        }, source=source, authoritative=False, tier="B_regional_pedestrian_topology")

    if profile == "pittsburgh_address_point":
        # Address points are useful OD anchors but are not verified door/entrance locations.
        return standard_feature(row, {
            "entrance_id": str(first(d, "ADDRESS_ID", "OBJECTID", "id") or f"address_{index}"),
            "kind": "entrance_proxy",
            "is_proxy": True,
            "requires_manual_entrance_audit": True,
            "full_address": first(d, "FULL_ADDRE", "FULL_ADDRESS"),
        }, source=source, authoritative=False, tier="C_address_point_proxy")

    if profile == "pittsburgh_street_closure":
        xy = representative_point(row)
        return {
            "closure_id": str(first(d, "closure_id", "CLOSURE_ID", "permit_id", "PERMIT_ID") or f"closure_{index}"),
            "permit_id": first(d, "permit_id", "PERMIT_ID"),
            "roadway_id": first(d, "roadway_id", "ROADWAY_ID"),
            "lon": xy[0] if xy else None, "lat": xy[1] if xy else None, "frame": "wgs84" if xy else None,
            "geometry": geometry(row), "start_time": first(d, "start_date", "start_datetime", "START_DATE", "from_date"),
            "end_time": first(d, "end_date", "end_datetime", "END_DATE", "to_date"),
            "kind": "temporary_street_closure", "dynamic_overlay": True,
            "source": source, "authoritative": True, "evidence_tier": "A_city_dynamic_regulation",
            "raw_properties": d,
        }

    if profile in {"vegas_parking_zone", "pittsburgh_parking_meter", "lta_taxi_stand", "lta_passenger_pickup_bay", "generic_pudo_candidate"}:
        xy = representative_point(row)
        if not xy:
            fields = sorted(str(k) for k in d.keys())[:80]
            coord_preview = {
                k: d.get(k) for k in d
                if any(token in _canon_key(k) for token in ("lat", "lon", "lng", "long", "coord", "geom", "wkt"))
            }
            raise ValueError(
                f"{profile} feature has no valid WGS84 geometry/coordinates; "
                f"available_fields={fields}; coordinate_like_values={coord_preview}"
            )
        rid = first(d, "OBJECTID", "UNITID", "METER_NO", "id", "TaxiCode", "TAXI_CODE") or f"{profile}_{index}"
        official_candidate = profile in {"vegas_parking_zone", "lta_taxi_stand", "lta_passenger_pickup_bay"}
        return {
            "regulation_id": str(rid), "lon": xy[0], "lat": xy[1], "frame": "wgs84",
            "legal_stop": False, "candidate_only": True, "requires_manual_legality_audit": True,
            "service_class": ("passenger_pickup" if profile == "lta_passenger_pickup_bay" else "taxi" if profile in {"lta_taxi_stand", "vegas_parking_zone"} else "unknown"),
            "side": first(d, "SIDE"), "hours": first(d, "HOURS"), "days": first(d, "DAYS"),
            "restrictions": first(d, "RESTRICTIONS", "DESCRIPTION", "LOCATION"), "source": source,
            "authoritative": official_candidate, "evidence_tier": "B_official_candidate_layer" if official_candidate else "C_parking_candidate_proxy",
            "confidence": 0.75 if official_candidate else 0.6, "raw_properties": d,
        }

    if profile == "lta_footpath":
        return standard_feature(row, {
            "feature_id": str(first(d, "OBJECTID", "id") or f"lta_footpath_{index}"),
            "kind": "sidewalk",
            "source_role": "pedestrian_topology",
            "physical_attributes_authoritative": False,
        }, source=source, authoritative=True, tier="A_official_pedestrian_topology")

    if profile == "lta_kerbline":
        return standard_feature(row, {
            "feature_id": str(first(d, "OBJECTID", "id") or f"lta_kerb_{index}"),
            "kind": "curb_line",
            "source_role": "curb_geometry",
        }, source=source, authoritative=True, tier="A_official_curb_geometry")

    if profile == "government_entrance":
        fid = str(first(d, "id", "OBJECTID", "EXIT_CODE", "STN_EXIT") or f"entrance_{index}")
        return standard_feature(row, {"entrance_id": fid, "kind": "entrance", "is_proxy": False}, source=source, authoritative=True, tier="A_government_entrance_layer")
    if profile == "generic_city_gis":
        return standard_feature(row, {"feature_id": str(first(d, "id", "OBJECTID") or f"feature_{index}")}, source=source, authoritative=False, tier="B_unmapped_city_gis")
    raise ValueError(f"unknown profile: {profile}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--layer", default=None, help="Layer name for multi-layer GPKG or ZIP archives containing multiple shapefiles.")
    p.add_argument("--profile", required=True, choices=[
        "boston_sidewalk", "boston_sidewalk_centerline", "boston_curb", "boston_ramp", "pittsburgh_sidewalks_steps", "pittsburgh_address_point",
        "pittsburgh_street_closure", "vegas_parking_zone", "pittsburgh_parking_meter", "lta_taxi_stand", "lta_passenger_pickup_bay",
        "lta_footpath", "lta_kerbline", "generic_pudo_candidate", "government_entrance", "generic_city_gis",
    ])
    p.add_argument("--source", required=True)
    p.add_argument("--input_crs", default=None, help="Optional source CRS override (e.g. EPSG:4269 for PASDA NAD83 GeoJSON); vector geometry is reprojected to EPSG:4326 via GDAL.")
    p.add_argument("--width_unit", choices=["unknown", "m", "feet", "inches"], default="unknown")
    p.add_argument("--reveal_unit", choices=["unknown", "m", "feet", "inches"], default="unknown")
    p.add_argument("--slope_unit", choices=["unknown", "ratio", "percent", "degrees"], default="unknown")
    p.add_argument("--skip_invalid", action="store_true")
    args = p.parse_args()

    rows: List[Dict[str, Any]] = []
    errors: List[str] = []
    for i, row in enumerate(read_rows(Path(args.input), layer=args.layer, input_crs=args.input_crs)):
        try:
            rows.append(normalize(args.profile, row, i, args.source, args))
        except Exception as exc:
            if not args.skip_invalid:
                raise
            errors.append(f"row {i}: {type(exc).__name__}: {exc}")
    if not rows:
        raise RuntimeError("normalization produced zero records")

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    geojson_profiles = {
        "boston_sidewalk", "boston_sidewalk_centerline", "boston_curb",
        "pittsburgh_sidewalks_steps", "pittsburgh_address_point",
        "lta_footpath", "lta_kerbline",
        "government_entrance", "generic_city_gis",
    }
    if args.profile in geojson_profiles:
        payload = {"type": "FeatureCollection", "features": rows, "properties": {"profile": args.profile, "source": args.source, "record_count": len(rows)}}
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    else:
        with out.open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    def missing(field: str) -> int:
        return sum(1 for r in rows if (r.get("properties", {}) if isinstance(r.get("properties"), dict) else r).get(field) is None)
    geometry_type_counts: Dict[str, int] = {}
    for r in rows:
        g = r.get("geometry") if isinstance(r, dict) else None
        gt = str(g.get("type")) if isinstance(g, dict) and g.get("type") else "None"
        geometry_type_counts[gt] = geometry_type_counts.get(gt, 0) + 1
    report = {
        "status": "PASS", "input": args.input, "output": str(out), "profile": args.profile,
        "records": len(rows), "skipped": len(errors), "errors": errors[:100],
        "geometry_type_counts": dict(sorted(geometry_type_counts.items())),
        "unit_mapping": {"width_unit": args.width_unit, "reveal_unit": args.reveal_unit, "slope_unit": args.slope_unit},
        "input_crs_override": args.input_crs,
        "output_geometry_crs": "EPSG:4326",
        "missing_counts": {k: missing(k) for k in ["sidewalk_width_m", "curb_height_m", "deployment_clearance_m", "slope", "ramp_slope"]},
    }
    out.with_suffix(out.suffix + ".report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
