#!/usr/bin/env python
"""Validate local DEM tiles against a configured city AOI.

The validator checks CRS, raster readability, approximate nominal resolution,
actual non-NoData coverage on a regular WGS84 AOI grid, and overlap frequency.
A physical merged GeoTIFF is intentionally not required. Optionally create a GDAL
VRT, which is a lightweight deterministic virtual mosaic referencing the source
tiles without duplicating raster data.
"""
from __future__ import annotations

import argparse
import json
import math
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Tuple

try:
    import yaml  # type: ignore
    import rasterio  # type: ignore
    from rasterio.warp import transform  # type: ignore
except Exception as exc:  # pragma: no cover
    raise RuntimeError("pyyaml and rasterio>=1.3 are required") from exc

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _sample_tile(ds, lon: float, lat: float) -> bool:
    xs, ys = transform("EPSG:4326", ds.crs, [lon], [lat])
    x, y = xs[0], ys[0]
    if not (ds.bounds.left <= x <= ds.bounds.right and ds.bounds.bottom <= y <= ds.bounds.top):
        return False
    sample = next(ds.sample([(x, y)], indexes=1, masked=True))
    if len(sample) == 0:
        return False
    raw = sample[0]
    if hasattr(raw, "mask") and bool(raw.mask):
        return False
    try:
        z = float(raw)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(z):
        return False
    if ds.nodata is not None and abs(z - float(ds.nodata)) <= 1e-9:
        return False
    return True


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", default="configs/abilitybench_nuplan_real.yaml")
    p.add_argument("--city", required=True, choices=["boston", "pittsburgh", "vegas", "singapore"])
    p.add_argument("--rasters", nargs="+", required=True)
    p.add_argument("--grid_size", type=int, default=41, help="Grid samples per axis across the configured AOI.")
    p.add_argument("--min_coverage", type=float, default=0.99)
    p.add_argument("--expected_resolution_m", type=float, default=None)
    p.add_argument("--resolution_tolerance", type=float, default=0.35,
                   help="Fractional tolerance for projected raster pixel size versus expected_resolution_m.")
    p.add_argument("--build_vrt", default=None, help="Optional output .vrt path; requires gdalbuildvrt.")
    p.add_argument("--report_json", default=None)
    args = p.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8")) or {}
    south, west, north, east = [float(x) for x in cfg["cities"][args.city]["bbox"]]
    paths = sorted({Path(x).expanduser().resolve() for x in args.rasters})
    if not paths:
        raise RuntimeError("no rasters supplied")
    missing = [str(x) for x in paths if not x.exists()]
    if missing:
        raise RuntimeError("missing raster files: " + ", ".join(missing))

    datasets = []
    tile_reports: List[Dict[str, Any]] = []
    issues: List[str] = []
    try:
        for path in paths:
            ds = rasterio.open(path)
            datasets.append(ds)
            if ds.crs is None:
                issues.append(f"missing_crs:{path}")
                continue
            rx, ry = abs(float(ds.res[0])), abs(float(ds.res[1]))
            geographic = bool(getattr(ds.crs, "is_geographic", False))
            unit = "degree" if geographic else str(getattr(ds.crs, "linear_units", None) or "projected_crs_unit")
            if args.expected_resolution_m is not None and not geographic and unit.lower() in {"metre", "meter", "metres", "meters", "m"}:
                rel = abs(max(rx, ry) - args.expected_resolution_m) / args.expected_resolution_m
                if rel > args.resolution_tolerance:
                    issues.append(f"unexpected_resolution:{path}:{max(rx, ry)}_{unit}")
            tile_reports.append({
                "path": str(path), "crs": str(ds.crs), "bounds": list(ds.bounds),
                "resolution_x": rx, "resolution_y": ry, "resolution_unit": unit,
                "nodata": ds.nodata, "width": ds.width, "height": ds.height,
            })

        n = max(3, args.grid_size)
        grid: List[Tuple[float, float]] = []
        for iy in range(n):
            lat = south + (north - south) * iy / (n - 1)
            for ix in range(n):
                lon = west + (east - west) * ix / (n - 1)
                grid.append((lon, lat))
        covered = overlap = 0
        max_overlap = 0
        for lon, lat in grid:
            hits = sum(1 for ds in datasets if ds.crs is not None and _sample_tile(ds, lon, lat))
            if hits > 0:
                covered += 1
            if hits > 1:
                overlap += 1
            max_overlap = max(max_overlap, hits)
        coverage = covered / len(grid)
        overlap_fraction = overlap / len(grid)
        if coverage < args.min_coverage:
            issues.append(f"aoi_non_nodata_coverage_below_threshold:{coverage:.6f}<{args.min_coverage:.6f}")

        if args.build_vrt:
            exe = shutil.which("gdalbuildvrt")
            if not exe:
                issues.append("gdalbuildvrt_not_found")
            else:
                vrt = Path(args.build_vrt).expanduser().resolve()
                vrt.parent.mkdir(parents=True, exist_ok=True)
                subprocess.check_call([exe, "-overwrite", str(vrt), *[str(x) for x in paths]])

        status = "PASS" if not issues else "FAIL"
        report = {
            "status": status,
            "city": args.city,
            "aoi_wgs84": [west, south, east, north],
            "tiles": tile_reports,
            "tile_count": len(tile_reports),
            "grid_size": n,
            "grid_points": len(grid),
            "covered_grid_points": covered,
            "coverage": coverage,
            "overlap_grid_points": overlap,
            "overlap_fraction": overlap_fraction,
            "max_simultaneous_tiles": max_overlap,
            "expected_resolution_m": args.expected_resolution_m,
            "issues": issues,
            "vrt": str(Path(args.build_vrt).expanduser().resolve()) if args.build_vrt else None,
            "note": "Overlapping tiles are acceptable for terrain-prior sampling; a physical merged GeoTIFF is not required.",
        }
        text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
        print(text)
        print(f"DEM_TILE_CHECK={status}")
        out = Path(args.report_json) if args.report_json else PROJECT_ROOT / "data" / "external" / "reports" / f"dem_tiles_{args.city}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text + "\n", encoding="utf-8")
        if status != "PASS":
            raise SystemExit(2)
    finally:
        for ds in datasets:
            ds.close()


if __name__ == "__main__":
    main()
