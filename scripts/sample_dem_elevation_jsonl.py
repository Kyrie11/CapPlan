#!/usr/bin/env python
"""Sample point elevations and write AbilityBench DEM JSONL evidence.

This utility fills ``<external_root>/dem/<city>.jsonl`` with observed elevation
samples at WGS84 points extracted from the already downloaded pedestrian GIS
layers.  It does not fabricate slope: downstream graph construction computes
edge slope only when two neighboring nodes have sampled elevations.

Supported providers:
  - epqs: USGS Elevation Point Query Service, suitable for US cities.
  - opentopography: OpenTopography global DEM API, useful where a public
    national DEM is unavailable. Some datasets require an API key.
  - none: collect and de-duplicate points, but do not call a network service.

Example:
  python scripts/sample_dem_elevation_jsonl.py \
    --external_root /data0/senzeyu2/dataset/abilitybench_external \
    --city boston --provider epqs --max_points 20000 --force
"""
from __future__ import annotations

import argparse
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

USER_AGENT = "AbilityBenchDEMSampler/0.1 (local-research-script)"

CITY_BBOXES: Dict[str, Tuple[float, float, float, float]] = {
    # south, west, north, east; intentionally matches the external fetcher.
    "boston": (42.30, -71.15, 42.42, -70.98),
    "pittsburgh": (40.38, -80.04, 40.48, -79.88),
    "vegas": (36.07, -115.23, 36.20, -115.10),
    "singapore": (1.27, 103.75, 1.33, 103.82),
}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_jsonl(path: Path) -> Iterator[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                yield obj


def _points_from_geometry(geom: Any) -> Iterator[Tuple[float, float]]:
    if not isinstance(geom, dict):
        return
    typ = geom.get("type")
    coords = geom.get("coordinates")
    if typ == "Point" and isinstance(coords, list) and len(coords) >= 2:
        yield float(coords[0]), float(coords[1])
    elif typ == "LineString" and isinstance(coords, list):
        for c in coords:
            if isinstance(c, list) and len(c) >= 2:
                yield float(c[0]), float(c[1])
    elif typ == "MultiLineString" and isinstance(coords, list):
        for line in coords:
            if isinstance(line, list):
                for c in line:
                    if isinstance(c, list) and len(c) >= 2:
                        yield float(c[0]), float(c[1])
    elif typ == "Polygon" and isinstance(coords, list):
        for ring in coords[:1]:
            if isinstance(ring, list):
                for c in ring:
                    if isinstance(c, list) and len(c) >= 2:
                        yield float(c[0]), float(c[1])
    elif typ == "MultiPolygon" and isinstance(coords, list):
        for poly in coords:
            if isinstance(poly, list) and poly:
                for c in poly[0]:
                    if isinstance(c, list) and len(c) >= 2:
                        yield float(c[0]), float(c[1])


def _iter_points_from_geojson(path: Path) -> Iterator[Tuple[float, float]]:
    try:
        payload = _read_json(path)
    except Exception:
        return
    if isinstance(payload, dict) and payload.get("type") == "FeatureCollection":
        for feat in payload.get("features") or []:
            yield from _points_from_geometry(feat.get("geometry"))
    elif isinstance(payload, dict) and "elements" in payload:
        yield from _iter_points_from_overpass_payload(payload)
    elif isinstance(payload, dict):
        yield from _points_from_geometry(payload.get("geometry"))


def _iter_points_from_overpass_payload(payload: Dict[str, Any]) -> Iterator[Tuple[float, float]]:
    for el in payload.get("elements") or []:
        if not isinstance(el, dict):
            continue
        if el.get("type") == "node" and el.get("lon") is not None and el.get("lat") is not None:
            yield float(el["lon"]), float(el["lat"])
        elif el.get("type") == "way" and isinstance(el.get("geometry"), list):
            for p in el["geometry"]:
                if isinstance(p, dict) and p.get("lon") is not None and p.get("lat") is not None:
                    yield float(p["lon"]), float(p["lat"])


def _iter_points_from_row(row: Dict[str, Any]) -> Iterator[Tuple[float, float]]:
    if row.get("lon") is not None and row.get("lat") is not None:
        yield float(row["lon"]), float(row["lat"])
    elif row.get("longitude") is not None and row.get("latitude") is not None:
        yield float(row["longitude"]), float(row["latitude"])
    yield from _points_from_geometry(row.get("geometry"))


def _iter_candidate_paths(external_root: Path, city: str, include_city_gis: bool) -> Iterator[Path]:
    roots = [
        external_root / "osm" / f"{city}_sidewalks.json",
        external_root / "opensidewalks" / f"{city}.geojson",
        external_root / "entrances" / f"{city}.geojson",
        external_root / "curb_inventory" / f"{city}.jsonl",
    ]
    for p in roots:
        if p.exists():
            yield p
    if include_city_gis:
        root = external_root / "city_gis" / city
        if root.exists():
            for p in sorted(root.rglob("*")):
                if p.suffix.lower() in {".json", ".geojson", ".jsonl"}:
                    yield p


def _in_bbox(lon: float, lat: float, bbox: Optional[Tuple[float, float, float, float]]) -> bool:
    if bbox is None:
        return True
    south, west, north, east = bbox
    return south <= lat <= north and west <= lon <= east


def collect_points(external_root: Path, city: str, *, precision: int, max_points: int, stride_m: float, include_city_gis: bool) -> List[Tuple[float, float]]:
    bbox = CITY_BBOXES.get(city.lower())
    seen: set[Tuple[float, float]] = set()
    points: List[Tuple[float, float]] = []
    min_step_deg = max(0.0, float(stride_m)) / 111_320.0 if stride_m > 0 else 0.0
    last_kept: Optional[Tuple[float, float]] = None
    for path in _iter_candidate_paths(external_root, city, include_city_gis):
        if path.suffix.lower() == ".jsonl":
            raw_iter = (pt for row in _iter_jsonl(path) for pt in _iter_points_from_row(row))
        else:
            raw_iter = _iter_points_from_geojson(path)
        for lon, lat in raw_iter:
            if not (math.isfinite(lon) and math.isfinite(lat)):
                continue
            if not (-180 <= lon <= 180 and -90 <= lat <= 90):
                continue
            if not _in_bbox(lon, lat, bbox):
                continue
            key = (round(lon, precision), round(lat, precision))
            if key in seen:
                continue
            if min_step_deg and last_kept is not None:
                # Cheap global thinning to keep public APIs usable. This is not a
                # spatial index; it only avoids many consecutive samples on dense lines.
                if abs(key[0] - last_kept[0]) < min_step_deg and abs(key[1] - last_kept[1]) < min_step_deg:
                    continue
            seen.add(key)
            points.append(key)
            last_kept = key
            if max_points and len(points) >= max_points:
                return points
    return points


def _urlopen_json(url: str, timeout_s: int) -> Dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json, */*;q=0.8"})
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310 - explicit user-run data utility
        return json.loads(resp.read().decode("utf-8"))


def sample_epqs(lon: float, lat: float, *, timeout_s: int) -> Optional[float]:
    params = urllib.parse.urlencode({"x": lon, "y": lat, "units": "Meters", "wkid": 4326, "includeDate": "false"})
    url = f"https://epqs.nationalmap.gov/v1/json?{params}"
    data = _urlopen_json(url, timeout_s)
    value = data.get("value")
    if value is None and isinstance(data.get("USGS_Elevation_Point_Query_Service"), dict):
        value = data["USGS_Elevation_Point_Query_Service"].get("Elevation_Query", {}).get("Elevation")
    try:
        z = float(value)
    except (TypeError, ValueError):
        return None
    # EPQS sometimes returns sentinel values outside coverage.
    return z if math.isfinite(z) and -500.0 < z < 9000.0 else None


def sample_opentopography(lon: float, lat: float, *, dataset: str, api_key: Optional[str], timeout_s: int) -> Optional[float]:
    params = {"locations": f"{lat},{lon}", "demtype": dataset, "output": "json"}
    if api_key:
        params["API_Key"] = api_key
    url = f"https://portal.opentopography.org/API/globaldem?{urllib.parse.urlencode(params)}"
    data = _urlopen_json(url, timeout_s)
    results = data.get("results") or []
    if isinstance(results, list) and results:
        value = results[0].get("elevation")
    else:
        value = data.get("elevation")
    try:
        z = float(value)
    except (TypeError, ValueError):
        return None
    return z if math.isfinite(z) and -500.0 < z < 9000.0 else None


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--external_root", required=True)
    parser.add_argument("--city", required=True, choices=sorted(CITY_BBOXES))
    parser.add_argument("--provider", choices=["epqs", "opentopography", "none"], default="epqs")
    parser.add_argument("--opentopography_dataset", default="SRTMGL1", help="OpenTopography demtype, e.g. SRTMGL1, COP30, AW3D30 where available.")
    parser.add_argument("--opentopography_api_key", default=None)
    parser.add_argument("--max_points", type=int, default=20000)
    parser.add_argument("--stride_m", type=float, default=5.0, help="Thin consecutive dense line vertices by approximately this many metres before sampling.")
    parser.add_argument("--precision", type=int, default=6, help="Decimal places used for WGS84 de-duplication.")
    parser.add_argument("--sleep_s", type=float, default=0.05)
    parser.add_argument("--timeout_s", type=int, default=30)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--include_city_gis", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    root = Path(args.external_root)
    out_path = root / "dem" / f"{args.city}.jsonl"
    report_path = root / "reports" / f"dem_sampling_{args.city}.json"
    if out_path.exists() and out_path.stat().st_size > 0 and not args.force:
        raise SystemExit(f"{out_path} already exists; use --force to overwrite")

    points = collect_points(root, args.city, precision=args.precision, max_points=args.max_points, stride_m=args.stride_m, include_city_gis=args.include_city_gis)
    rows: List[Dict[str, Any]] = []
    failures = 0
    for i, (lon, lat) in enumerate(points):
        z: Optional[float] = None
        err: Optional[str] = None
        if args.provider == "none":
            rows.append({"id": f"{args.city}_dem_{i:07d}", "lon": lon, "lat": lat, "elevation_m": None, "source": "point_collection_only", "confidence": 0.0})
            continue
        for attempt in range(max(1, args.retries)):
            try:
                if args.provider == "epqs":
                    z = sample_epqs(lon, lat, timeout_s=args.timeout_s)
                else:
                    z = sample_opentopography(lon, lat, dataset=args.opentopography_dataset, api_key=args.opentopography_api_key, timeout_s=args.timeout_s)
                break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
                err = str(exc)
                time.sleep(args.sleep_s * (attempt + 1))
        if z is None:
            failures += 1
            if err:
                rows.append({"id": f"{args.city}_dem_{i:07d}", "lon": lon, "lat": lat, "elevation_m": None, "source": args.provider, "confidence": 0.0, "error": err[:200]})
            continue
        rows.append({"id": f"{args.city}_dem_{i:07d}", "lon": lon, "lat": lat, "elevation_m": round(float(z), 3), "source": args.provider, "confidence": 0.8})
        if args.sleep_s > 0:
            time.sleep(args.sleep_s)

    n = write_jsonl(out_path, rows)
    report = {
        "city": args.city,
        "provider": args.provider,
        "candidate_points": len(points),
        "written_rows": n,
        "successful_elevations": sum(1 for r in rows if r.get("elevation_m") is not None),
        "failed_samples": failures,
        "output": str(out_path),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
