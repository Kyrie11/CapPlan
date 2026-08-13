#!/usr/bin/env python
"""Validate gross spatial alignment between nuPlan map, AOI and external OSM.

`inspect_nuplan_map_crs.py` only proves that a projected CRS was read from the
nuPlan GPKG metadata. This script performs the second-stage spatial validation:

1. transform the configured WGS84 city AOI into the nuPlan local CRS;
2. read feature-layer extents from the nuPlan GeoPackage;
3. verify substantial AOI/map overlap;
4. verify the prepared external OSM layer is WGS84, overlaps the configured AOI,
   and its transformed extent overlaps the nuPlan map extent.

On PASS, `--write_georeference` updates spatial_alignment_validated=true and
records the evidence/thresholds. This is a gross map-level alignment check; the
per-episode graph builder remains the stronger scene-level validation.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    import yaml  # type: ignore
    from pyproj import CRS, Transformer  # type: ignore
except Exception as exc:  # pragma: no cover
    raise RuntimeError("pyyaml and pyproj>=3.6 are required") from exc

PROJECT_ROOT = Path(__file__).resolve().parents[1]
Bounds = Tuple[float, float, float, float]  # minx,miny,maxx,maxy


def expand_path(value: str) -> Path:
    p = Path(str(value).format(project_root=str(PROJECT_ROOT))).expanduser()
    return p if p.is_absolute() else PROJECT_ROOT / p


def intersect(a: Bounds, b: Bounds) -> Optional[Bounds]:
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    return (x0, y0, x1, y1) if x1 > x0 and y1 > y0 else None


def area(b: Optional[Bounds]) -> float:
    if not b:
        return 0.0
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def fraction_covered(target: Bounds, cover: Bounds) -> float:
    denom = area(target)
    return area(intersect(target, cover)) / denom if denom > 0 else 0.0


def _gpkg_srs_crs(conn: sqlite3.Connection, srs_id: int) -> CRS:
    """Resolve a GeoPackage ``srs_id`` to a pyproj CRS.

    ``gpkg_contents`` extents are expressed in the native CRS of each layer.
    nuPlan map layers are commonly stored with geographic extents in
    ``gpkg_contents`` even though the devkit exposes them in a projected local
    frame.  Comparing those degree-valued extents directly with UTM AOI bounds
    creates a guaranteed false FAIL, so every layer extent is transformed to
    the requested target CRS before unioning.
    """
    row = conn.execute(
        "SELECT organization,organization_coordsys_id,definition "
        "FROM gpkg_spatial_ref_sys WHERE srs_id=?",
        (int(srs_id),),
    ).fetchone()
    if not row:
        raise RuntimeError(f"GeoPackage SRS id {srs_id} is not registered")
    organization, organization_coordsys_id, definition = row
    if str(organization or "").upper() == "EPSG":
        try:
            return CRS.from_epsg(int(organization_coordsys_id))
        except Exception:
            pass
    if definition and str(definition).strip().lower() not in {"undefined", "none"}:
        try:
            return CRS.from_user_input(str(definition))
        except Exception:
            pass
    raise RuntimeError(
        f"cannot resolve GeoPackage SRS id {srs_id}: "
        f"organization={organization!r} coordsys={organization_coordsys_id!r}"
    )


def _transform_bounds_between(bounds: Bounds, src_crs: CRS, dst_crs: CRS) -> Bounds:
    if src_crs == dst_crs:
        return bounds
    tr = Transformer.from_crs(src_crs, dst_crs, always_xy=True)
    return tuple(float(x) for x in tr.transform_bounds(*bounds, densify_pts=21))  # type: ignore[return-value]


def gpkg_feature_bounds(path: Path, target_crs: str | CRS | None = None) -> Bounds:
    conn = sqlite3.connect(str(path))
    try:
        tables = {str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "gpkg_contents" not in tables:
            raise RuntimeError(f"GeoPackage has no gpkg_contents: {path}")
        rows = conn.execute(
            "SELECT table_name,min_x,min_y,max_x,max_y,srs_id FROM gpkg_contents "
            "WHERE data_type='features' AND min_x IS NOT NULL AND min_y IS NOT NULL "
            "AND max_x IS NOT NULL AND max_y IS NOT NULL"
        ).fetchall()
        valid: List[Tuple[str, float, float, float, float]] = []
        dst = CRS.from_user_input(target_crs) if target_crs is not None else None
        for name, minx, miny, maxx, maxy, srs_id in rows:
            vals = [float(minx), float(miny), float(maxx), float(maxy)]
            if all(math.isfinite(v) for v in vals) and vals[2] > vals[0] and vals[3] > vals[1]:
                b: Bounds = (vals[0], vals[1], vals[2], vals[3])
                if dst is not None:
                    src = _gpkg_srs_crs(conn, int(srs_id))
                    b = _transform_bounds_between(b, src, dst)
                valid.append((str(name), *b))
        if not valid:
            raise RuntimeError("nuPlan GPKG has no finite feature extents in gpkg_contents")
        return (
            min(r[1] for r in valid), min(r[2] for r in valid),
            max(r[3] for r in valid), max(r[4] for r in valid),
        )
    finally:
        conn.close()


def _walk_coords(value: Any) -> Iterable[Tuple[float, float]]:
    if isinstance(value, list) and len(value) >= 2 and all(isinstance(x, (int, float)) for x in value[:2]):
        yield float(value[0]), float(value[1])
    elif isinstance(value, list):
        for x in value:
            yield from _walk_coords(x)


def geojson_bounds(path: Path) -> Bounds:
    # Prepared OSM city AOIs are modest enough to parse directly. If a future
    # file becomes huge, convert it to GeoJSONSeq first with ogr2ogr.
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and payload.get("type") == "FeatureCollection":
        features = payload.get("features") or []
    elif isinstance(payload, list):
        features = payload
    else:
        features = [payload]
    xs: List[float] = []
    ys: List[float] = []
    for feat in features:
        if not isinstance(feat, dict):
            continue
        geom = feat.get("geometry") if feat.get("type") == "Feature" else feat.get("geometry", feat)
        if not isinstance(geom, dict):
            continue
        for x, y in _walk_coords(geom.get("coordinates")):
            if math.isfinite(x) and math.isfinite(y):
                xs.append(x); ys.append(y)
    if not xs:
        raise RuntimeError(f"no coordinates found in {path}")
    b = min(xs), min(ys), max(xs), max(ys)
    if not (-180 <= b[0] <= 180 and -180 <= b[2] <= 180 and -90 <= b[1] <= 90 and -90 <= b[3] <= 90):
        raise RuntimeError(f"external OSM does not look like WGS84 lon/lat: bounds={b}")
    return b


def transform_bounds(bounds: Bounds, dst_crs: str) -> Bounds:
    tr = Transformer.from_crs("EPSG:4326", dst_crs, always_xy=True)
    return tuple(float(x) for x in tr.transform_bounds(*bounds, densify_pts=21))  # type: ignore[return-value]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", default="configs/abilitybench_nuplan_real.yaml")
    p.add_argument("--cities", default="boston,pittsburgh,vegas,singapore")
    p.add_argument("--min_map_aoi_overlap", type=float, default=0.20)
    p.add_argument("--min_osm_aoi_overlap", type=float, default=0.20)
    p.add_argument("--min_osm_map_overlap", type=float, default=0.05)
    p.add_argument("--write_georeference", action="store_true")
    p.add_argument("--report_json", default=None)
    args = p.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8")) or {}
    cities = [x.strip() for x in args.cities.replace("+", ",").split(",") if x.strip()]
    city_reports: List[Dict[str, Any]] = []
    failures = 0

    for city in cities:
        ccfg = cfg["cities"][city]
        georef_path = expand_path(ccfg["georeference_json"])
        osm_path = expand_path(ccfg["osm_source"])
        try:
            g = json.loads(georef_path.read_text(encoding="utf-8"))
            local_crs = str(g["local_crs"])
            parsed = CRS.from_user_input(local_crs)
            if not parsed.is_projected:
                raise RuntimeError(f"local_crs is not projected: {local_crs}")
            map_gpkg = Path(g["map_gpkg"])
            if not map_gpkg.exists():
                raise FileNotFoundError(map_gpkg)
            if not osm_path.exists():
                raise FileNotFoundError(osm_path)

            south, west, north, east = [float(x) for x in ccfg["bbox"]]
            aoi_wgs: Bounds = (west, south, east, north)
            aoi_local = transform_bounds(aoi_wgs, local_crs)
            map_local = gpkg_feature_bounds(map_gpkg, local_crs)
            osm_wgs = geojson_bounds(osm_path)
            osm_local = transform_bounds(osm_wgs, local_crs)

            map_aoi = fraction_covered(aoi_local, map_local)
            osm_aoi = fraction_covered(aoi_wgs, osm_wgs)
            osm_map = fraction_covered(osm_local, map_local)
            status = "PASS" if (
                map_aoi >= args.min_map_aoi_overlap and
                osm_aoi >= args.min_osm_aoi_overlap and
                osm_map >= args.min_osm_map_overlap
            ) else "FAIL"
            if status != "PASS":
                failures += 1
            evidence = {
                "status": status,
                "city": city,
                "local_crs": local_crs,
                "map_gpkg": str(map_gpkg),
                "osm_source": str(osm_path),
                "aoi_wgs84_bounds": aoi_wgs,
                "aoi_local_bounds": aoi_local,
                "map_local_bounds": map_local,
                "osm_wgs84_bounds": osm_wgs,
                "osm_local_bounds": osm_local,
                "map_aoi_overlap_fraction": map_aoi,
                "osm_aoi_overlap_fraction": osm_aoi,
                "osm_map_overlap_fraction": osm_map,
                "thresholds": {
                    "min_map_aoi_overlap": args.min_map_aoi_overlap,
                    "min_osm_aoi_overlap": args.min_osm_aoi_overlap,
                    "min_osm_map_overlap": args.min_osm_map_overlap,
                },
                "validation_scope": "gross_map_aoi_and_external_osm_overlap",
                "note": "Per-episode accessibility graph construction is the stronger scene-level alignment check.",
            }
            if status == "PASS" and args.write_georeference:
                g["spatial_alignment_validated"] = True
                g["spatial_alignment_validation_scope"] = evidence["validation_scope"]
                g["spatial_alignment_evidence"] = evidence
                georef_path.write_text(json.dumps(g, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            city_reports.append(evidence)
        except Exception as exc:
            failures += 1
            city_reports.append({"status": "FAIL", "city": city, "error": f"{type(exc).__name__}: {exc}"})

    report = {"status": "PASS" if failures == 0 else "FAIL", "cities": city_reports}
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(text)
    print(f"GEOREFERENCE_SPATIAL_ALIGNMENT_CHECK={report['status']}")
    if args.report_json:
        out = Path(args.report_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
    if failures:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
