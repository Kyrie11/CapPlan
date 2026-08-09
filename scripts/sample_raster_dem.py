#!/usr/bin/env python
"""Sample local GeoTIFF/COG DEM tiles at external-layer vertices.

This is the preferred paper-scale alternative to point-by-point web APIs.  The
output contains terrain elevations at the exact WGS84 vertices used to build
the pedestrian graph.  It is a terrain prior, not a measurement of curb-ramp
running slope, cross-slope, landing slope, or curb reveal.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from sample_dem_elevation_jsonl import collect_points


def _sample(
    points: Sequence[Tuple[float, float]],
    rasters: Sequence[Path],
    nodata_tolerance: float = 1e-9,
) -> Tuple[List[Optional[float]], List[Optional[str]], List[Optional[float]], List[Optional[str]]] :
    try:
        import rasterio  # type: ignore
        from rasterio.warp import transform  # type: ignore
    except ImportError as exc:  # pragma: no cover - optional data dependency
        raise RuntimeError("rasterio is required: pip install 'rasterio>=1.3'") from exc

    values: List[Optional[float]] = [None] * len(points)
    source_tiles: List[Optional[str]] = [None] * len(points)
    resolutions: List[Optional[float]] = [None] * len(points)
    resolution_units: List[Optional[str]] = [None] * len(points)
    remaining = set(range(len(points)))
    for raster_path in rasters:
        if not remaining:
            break
        with rasterio.open(raster_path) as ds:
            if ds.crs is None:
                raise RuntimeError(f"raster has no CRS: {raster_path}")
            ids = sorted(remaining)
            lons = [points[i][0] for i in ids]
            lats = [points[i][1] for i in ids]
            xs, ys = transform("EPSG:4326", ds.crs, lons, lats)
            inside_ids: List[int] = []
            xy: List[Tuple[float, float]] = []
            for i, x, y in zip(ids, xs, ys):
                if ds.bounds.left <= x <= ds.bounds.right and ds.bounds.bottom <= y <= ds.bounds.top:
                    inside_ids.append(i)
                    xy.append((x, y))
            if not xy:
                continue
            samples = ds.sample(xy, indexes=1, masked=True)
            resolution = float(max(abs(ds.res[0]), abs(ds.res[1])))
            if bool(getattr(ds.crs, "is_geographic", False)):
                resolution_unit = "degree"
            else:
                resolution_unit = str(getattr(ds.crs, "linear_units", None) or "projected_crs_unit")
            for i, sample in zip(inside_ids, samples):
                if getattr(sample, "mask", False) is True or len(sample) == 0:
                    continue
                raw = sample[0]
                if hasattr(raw, "mask") and bool(raw.mask):
                    continue
                try:
                    z = float(raw)
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(z):
                    continue
                if ds.nodata is not None and abs(z - float(ds.nodata)) <= nodata_tolerance:
                    continue
                values[i] = z
                source_tiles[i] = str(raster_path)
                resolutions[i] = resolution
                resolution_units[i] = resolution_unit
                remaining.discard(i)
    return values, source_tiles, resolutions, resolution_units


def _write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(path.suffix + ".part")
    n = 0
    with part.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            n += 1
    if n <= 0:
        part.unlink(missing_ok=True)
        raise RuntimeError("no DEM samples were written; check raster coverage and CRS")
    part.replace(path)
    return n


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--external_root", required=True, help=".../CapPlan/data/external")
    p.add_argument("--city", required=True, choices=["boston", "pittsburgh", "vegas", "singapore"])
    p.add_argument("--rasters", nargs="+", required=True, help="One or more local .tif/.tiff/.img DEM tiles; let the shell expand globs.")
    p.add_argument("--output", default=None)
    p.add_argument("--vertical_datum", default="unknown", help="Record the source vertical datum, e.g. NAVD88 or EGM2008.")
    p.add_argument("--source_name", default="local_raster_dem")
    p.add_argument("--nominal_resolution_m", type=float, default=None,
                   help="Optional product-level nominal ground resolution in metres (e.g. 1 for USGS 1m, 30 for COP-DEM GLO-30).")
    p.add_argument("--max_points", type=int, default=0, help="0 means all unique vertices.")
    p.add_argument("--stride_m", type=float, default=0.0, help="Use 0 for exact graph vertices; positive values thin dense input.")
    p.add_argument("--precision", type=int, default=7)
    p.add_argument("--include_city_gis", action="store_true")
    p.add_argument("--allow_partial_coverage", action="store_true")
    args = p.parse_args()

    external_root = Path(args.external_root).expanduser().resolve()
    normalized_root = external_root / "normalized"
    # Deterministic ordering matters when 3DEP tiles overlap. Overlap is not
    # an error for this terrain-prior use case; the first valid tile in this
    # stable order supplies the sample and source_tile records provenance.
    rasters = sorted({Path(x).expanduser().resolve() for x in args.rasters})
    missing = [str(x) for x in rasters if not x.exists()]
    if missing:
        raise RuntimeError("missing raster files: " + ", ".join(missing))
    points = collect_points(
        normalized_root,
        args.city,
        precision=args.precision,
        max_points=args.max_points,
        stride_m=args.stride_m,
        include_city_gis=args.include_city_gis,
    )
    if not points:
        raise RuntimeError("no WGS84 points were found in normalized OSM/OSW/entrance/curb layers")
    values, tiles, resolutions, resolution_units = _sample(points, rasters)
    rows: List[Dict[str, Any]] = []
    missing_count = 0
    for i, ((lon, lat), z, tile, resolution, resolution_unit) in enumerate(zip(points, values, tiles, resolutions, resolution_units)):
        if z is None:
            missing_count += 1
            continue
        rows.append({
            "id": f"{args.city}_dem_{i:08d}",
            "lon": lon,
            "lat": lat,
            "frame": "wgs84",
            "elevation_m": round(z, 3),
            "source": args.source_name,
            "source_tile": tile,
            "source_resolution": resolution,
            "source_resolution_unit": resolution_unit,
            "nominal_resolution_m": args.nominal_resolution_m,
            "vertical_datum": args.vertical_datum,
            "evidence_tier": "C_derived_terrain_prior",
            "authoritative": False,
            "confidence": 0.85,
            "scope_note": "terrain elevation only; not curb-ramp/cross-slope ground truth",
        })
    coverage = len(rows) / len(points)
    if coverage < 0.99 and not args.allow_partial_coverage:
        raise RuntimeError(f"DEM coverage is only {coverage:.3%}; add tiles or pass --allow_partial_coverage for bootstrap diagnostics")
    out = Path(args.output).expanduser().resolve() if args.output else external_root / "normalized" / "dem" / f"{args.city}.jsonl"
    written = _write_jsonl(out, rows)
    report = {
        "city": args.city,
        "candidate_points": len(points),
        "written_rows": written,
        "missing_points": missing_count,
        "coverage": coverage,
        "rasters": [str(x) for x in rasters],
        "vertical_datum": args.vertical_datum,
        "nominal_resolution_m": args.nominal_resolution_m,
        "resolution_units": sorted({u for u in resolution_units if u}),
        "output": str(out),
        "publication_note": "terrain elevation is auxiliary; critical PUDO slopes still require authoritative data or manual audit",
        "status": "PASS" if coverage >= 0.99 or args.allow_partial_coverage else "FAIL",
    }
    report_path = external_root / "reports" / f"local_dem_sampling_{args.city}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    print(f"DEM_SAMPLING_CHECK={report['status']}")


if __name__ == "__main__":
    main()
