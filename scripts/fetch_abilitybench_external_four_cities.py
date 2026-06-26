#!/usr/bin/env python
"""Fetch and normalize public external GIS evidence for AbilityBench-AV.

The script intentionally separates *real observed fields* from unknown fields. It
creates all paths expected by configs/abilitybench_nuplan_real.yaml, but it does
not fabricate curb height, deployment clearance, sidewalk width, or legal curb
regulations. Cities with only OSM-derived evidence will still fail paper-mode
quality checks until authoritative curb inventory/regulation/audit data are added.

Outputs under --external_root:
  osm/<city>_sidewalks.json
  opensidewalks/<city>.geojson             # minimal WGS84 pedestrian network candidate
  city_gis/<city>/*                        # raw municipal downloads when available
  curb_inventory/<city>.jsonl              # WGS84 point evidence, global/unbound
  curb_regulations/<city>.jsonl            # fail-closed unless authoritative data exists
  entrances/<city>.geojson                 # entrance candidates
  dem/<city>.jsonl                         # optional point DEM samples; empty by default
  reports/external_source_report.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

CITY_SPECS: Dict[str, Dict[str, Any]] = {
    "boston": {
        "name": "Boston",
        "bbox": [42.30, -71.15, 42.42, -70.98],
        "municipal": "analyze_boston_ckan",
    },
    "pittsburgh": {
        "name": "Pittsburgh",
        "bbox": [40.38, -80.04, 40.48, -79.88],
        "municipal": "osm_plus_optional_arcgis",
    },
    "vegas": {
        "name": "Las Vegas",
        "bbox": [36.07, -115.23, 36.20, -115.10],
        "municipal": "osm_plus_clark_county_manual_download",
    },
    "singapore": {
        "name": "Singapore",
        "bbox": [1.27, 103.75, 1.33, 103.82],
        "municipal": "osm_plus_sla_lta_manual_or_licensed_download",
    },
}

BOSTON_CKAN_PACKAGES = {
    "sidewalk_inventory": "sidewalk-inventory",
    "pedestrian_ramp_inventory": "pedestrian-ramp-inventory",
    "sam_addresses": "live-street-address-management-sam-addresses",
}


def log(msg: str) -> None:
    print(msg, flush=True)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            n += 1
    return n


def http_get(url: str, timeout: int = 180) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "AbilityBenchExternalFetcher/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - user-run data fetch utility
        return resp.read()


def http_post(url: str, data: Dict[str, str], timeout: int = 300) -> bytes:
    encoded = urllib.parse.urlencode(data).encode("utf-8")
    req = urllib.request.Request(url, data=encoded, headers={"User-Agent": "AbilityBenchExternalFetcher/0.1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - user-run data fetch utility
        return resp.read()


def overpass_query(bbox: List[float], timeout_s: int) -> str:
    south, west, north, east = bbox
    bb = f"{south},{west},{north},{east}"
    return f"""
[out:json][timeout:{timeout_s}];
(
  way["highway"~"footway|path|pedestrian|steps"]({bb});
  way["footway"~"sidewalk|crossing|access_aisle"]({bb});
  way["sidewalk"~"yes|both|left|right|separate"]({bb});
  way["highway"="crossing"]({bb});
  node["kerb"]({bb});
  node["curb"]({bb});
  node["curb_ramp"]({bb});
  node["barrier"="kerb"]({bb});
  node["entrance"]({bb});
  node["highway"="bus_stop"]({bb});
  node["public_transport"~"platform|stop_position"]({bb});
);
out body geom;
>;
out skel qt;
""".strip()


def download_overpass(city: str, spec: Dict[str, Any], endpoint: str, timeout_s: int, force: bool) -> Path:
    out = ROOT / "osm" / f"{city}_sidewalks.json"
    if out.exists() and not force:
        log(f"[skip] {out}")
        return out
    query = overpass_query(spec["bbox"], timeout_s)
    log(f"[overpass] {city} -> {out}")
    payload = http_post(endpoint, {"data": query}, timeout=max(timeout_s + 30, 300))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(payload)
    # Validate early; Overpass sometimes returns HTML error pages.
    obj = read_json(out)
    if not isinstance(obj, dict) or "elements" not in obj:
        raise RuntimeError(f"Overpass response for {city} is not an OSM JSON object with elements")
    return out


def tags(el: Dict[str, Any]) -> Dict[str, Any]:
    return el.get("tags") if isinstance(el.get("tags"), dict) else {}


def feature_kind(el: Dict[str, Any]) -> Optional[str]:
    t = tags(el)
    if el.get("type") == "node":
        if "entrance" in t:
            return "entrance"
        if t.get("kerb") or t.get("curb") or t.get("curb_ramp") or t.get("barrier") == "kerb":
            if str(t.get("kerb") or t.get("curb") or t.get("curb_ramp")).lower() in {"lowered", "flush", "yes", "ramp"}:
                return "curb_ramp"
            return "curb_interface"
        if t.get("highway") == "bus_stop" or str(t.get("public_transport", "")).lower() in {"platform", "stop_position"}:
            return "transit_stop"
        return None
    if el.get("type") == "way":
        if t.get("highway") == "crossing" or t.get("footway") == "crossing":
            return "crossing"
        if t.get("highway") in {"footway", "path", "pedestrian", "steps"} or t.get("sidewalk") or t.get("footway"):
            return "sidewalk"
    return None


def normalize_width(v: Any) -> Optional[float]:
    if v in (None, "", "unknown", "n/a"):
        return None
    try:
        s = str(v).lower().replace("meters", "").replace("meter", "").replace("m", "").strip()
        return float(s)
    except Exception:
        return None


def osm_feature(el: Dict[str, Any], city: str) -> Optional[Dict[str, Any]]:
    kind = feature_kind(el)
    if kind is None:
        return None
    t = tags(el)
    props: Dict[str, Any] = {
        "_id": f"osm/{el.get('type')}/{el.get('id')}",
        "kind": kind,
        "source": "OpenStreetMap Overpass",
        "source_city": city,
        "confidence": 0.65,
        "osm_id": el.get("id"),
        "osm_type": el.get("type"),
    }
    for src, dst in [
        ("surface", "surface"),
        ("incline", "slope"),
        ("kerb", "kerb"),
        ("curb", "curb"),
        ("tactile_paving", "tactile_paving"),
        ("entrance", "entrance"),
        ("name", "name"),
    ]:
        if t.get(src) is not None:
            props[dst] = t[src]
    width = normalize_width(t.get("width") or t.get("sidewalk:width"))
    if width is not None:
        props["width_m"] = width
        props["sidewalk_width_m"] = width
    kerb_h = normalize_width(t.get("kerb:height") or t.get("curb:height"))
    if kerb_h is not None:
        props["curb_height_m"] = kerb_h
    if el.get("type") == "node" and el.get("lon") is not None and el.get("lat") is not None:
        geom = {"type": "Point", "coordinates": [float(el["lon"]), float(el["lat"])]}
    elif el.get("type") == "way" and isinstance(el.get("geometry"), list) and len(el["geometry"]) >= 2:
        geom = {"type": "LineString", "coordinates": [[float(p["lon"]), float(p["lat"])] for p in el["geometry"] if p.get("lon") is not None and p.get("lat") is not None]}
    else:
        return None
    return {"type": "Feature", "geometry": geom, "properties": props}


def osm_to_geojson_and_evidence(city: str, osm_json: Path) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]:
    obj = read_json(osm_json)
    features: List[Dict[str, Any]] = []
    curbs: List[Dict[str, Any]] = []
    entrances: List[Dict[str, Any]] = []
    for el in obj.get("elements", []):
        feat = osm_feature(el, city)
        if not feat:
            continue
        features.append(feat)
        geom = feat["geometry"]
        props = feat["properties"]
        if geom["type"] == "Point" and props.get("kind") in {"curb_interface", "curb_ramp"}:
            lon, lat = geom["coordinates"][:2]
            curbs.append({
                "id": props["_id"],
                "lon": lon,
                "lat": lat,
                "frame": "wgs84",
                "kind": props.get("kind"),
                "curb_ramp": props.get("kind") == "curb_ramp",
                "curb_height_m": props.get("curb_height_m"),
                "sidewalk_width_m": props.get("sidewalk_width_m"),
                "deployment_clearance_m": None,
                "surface": props.get("surface"),
                "source": "OpenStreetMap Overpass curb/kerb tags",
                "confidence": 0.55,
                "audited": False,
            })
        if geom["type"] == "Point" and props.get("kind") == "entrance":
            lon, lat = geom["coordinates"][:2]
            entrances.append({"type": "Feature", "geometry": geom, "properties": {
                "entrance_id": props["_id"],
                "kind": "entrance",
                "source": "OpenStreetMap entrance tag",
                "confidence": 0.55,
                "audited": False,
            }})
    fc = {"type": "FeatureCollection", "features": features, "properties": {"schema_variant": "osw_minimal_candidate", "source": "OpenStreetMap Overpass"}}
    return fc, curbs, entrances


def ckan_resource_url(package_id: str, preferred_formats: Tuple[str, ...] = ("geojson", "json", "csv")) -> Optional[str]:
    url = f"https://data.boston.gov/api/3/action/package_show?id={urllib.parse.quote(package_id)}"
    payload = json.loads(http_get(url).decode("utf-8"))
    resources = payload.get("result", {}).get("resources", [])
    for fmt in preferred_formats:
        for r in resources:
            if str(r.get("format", "")).lower() == fmt and r.get("url"):
                return str(r["url"])
    for r in resources:
        if r.get("url"):
            return str(r["url"])
    return None


def download_boston_city_gis(force: bool) -> Dict[str, Optional[Path]]:
    out_dir = ROOT / "city_gis" / "boston"
    out_dir.mkdir(parents=True, exist_ok=True)
    downloaded: Dict[str, Optional[Path]] = {}
    for label, package in BOSTON_CKAN_PACKAGES.items():
        dest = out_dir / f"{label}.geojson"
        try:
            url = ckan_resource_url(package)
            if not url:
                downloaded[label] = None
                continue
            if dest.exists() and not force:
                downloaded[label] = dest
                continue
            log(f"[ckan] {package} -> {dest}")
            dest.write_bytes(http_get(url, timeout=300))
            downloaded[label] = dest
        except Exception as e:
            log(f"[warn] Boston CKAN {package} failed: {e}")
            downloaded[label] = None
    return downloaded


def representative_lonlat(geom: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    coords = geom.get("coordinates")
    if geom.get("type") == "Point" and isinstance(coords, list) and len(coords) >= 2:
        return float(coords[0]), float(coords[1])
    # For LineString/Polygon/Multi*, use the mean of all visible vertices as a
    # conservative representative point; this is only for candidate matching.
    pts: List[Tuple[float, float]] = []
    def walk(x: Any) -> None:
        if isinstance(x, list) and len(x) >= 2 and all(isinstance(v, (int, float)) for v in x[:2]):
            pts.append((float(x[0]), float(x[1])))
        elif isinstance(x, list):
            for y in x:
                walk(y)
    walk(coords)
    if not pts:
        return None
    return sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts)


def append_boston_gis_evidence(downloaded: Dict[str, Optional[Path]], curbs: List[Dict[str, Any]], entrances: List[Dict[str, Any]]) -> None:
    ramp_path = downloaded.get("pedestrian_ramp_inventory")
    if ramp_path and ramp_path.exists():
        try:
            payload = read_json(ramp_path)
            for i, feat in enumerate(payload.get("features", [])):
                xy = representative_lonlat(feat.get("geometry") or {})
                if not xy:
                    continue
                props = feat.get("properties") if isinstance(feat.get("properties"), dict) else {}
                curbs.append({
                    "id": props.get("OBJECTID") or props.get("objectid") or f"boston_ramp_{i:06d}",
                    "lon": xy[0],
                    "lat": xy[1],
                    "frame": "wgs84",
                    "kind": "curb_ramp",
                    "curb_ramp": True,
                    "curb_height_m": normalize_width(props.get("curb_height_m") or props.get("CURBHEIGHT")),
                    "deployment_clearance_m": normalize_width(props.get("landing_width_m") or props.get("clear_width_m") or props.get("WIDTH")),
                    "sidewalk_width_m": normalize_width(props.get("sidewalk_width_m") or props.get("SW_WIDTH")),
                    "source": "City of Boston Pedestrian Ramp Inventory",
                    "confidence": 0.75,
                    "audited": False,
                    "raw_properties": props,
                })
        except Exception as e:
            log(f"[warn] could not parse Boston ramp inventory: {e}")
    addr_path = downloaded.get("sam_addresses")
    if addr_path and addr_path.exists():
        try:
            payload = read_json(addr_path)
            for i, feat in enumerate(payload.get("features", [])[:200000]):
                geom = feat.get("geometry") or {}
                if geom.get("type") != "Point":
                    continue
                props = feat.get("properties") if isinstance(feat.get("properties"), dict) else {}
                entrances.append({"type": "Feature", "geometry": geom, "properties": {
                    "entrance_id": props.get("SAM_ADDRESS_ID") or props.get("ADDRESS_ID") or f"boston_sam_{i:06d}",
                    "kind": "entrance_candidate",
                    "source": "City of Boston SAM address point; snapped/audit required before paper evaluation",
                    "confidence": 0.45,
                    "audited": False,
                }})
        except Exception as e:
            log(f"[warn] could not parse Boston SAM addresses: {e}")


def write_city_outputs(city: str, osw: Dict[str, Any], curbs: List[Dict[str, Any]], entrances: List[Dict[str, Any]], force: bool) -> Dict[str, Any]:
    outputs = {
        "opensidewalks": ROOT / "opensidewalks" / f"{city}.geojson",
        "curb_inventory": ROOT / "curb_inventory" / f"{city}.jsonl",
        "curb_regulations": ROOT / "curb_regulations" / f"{city}.jsonl",
        "entrances": ROOT / "entrances" / f"{city}.geojson",
        "dem": ROOT / "dem" / f"{city}.jsonl",
    }
    write_json(outputs["opensidewalks"], osw)
    n_curbs = write_jsonl(outputs["curb_inventory"], curbs)
    # Do not synthesize legal curb regulations. An empty file is fail-closed and
    # makes the missing authoritative source visible in the audit.
    if force or not outputs["curb_regulations"].exists():
        write_jsonl(outputs["curb_regulations"], [])
    write_json(outputs["entrances"], {"type": "FeatureCollection", "features": entrances})
    if force or not outputs["dem"].exists():
        write_jsonl(outputs["dem"], [])
    return {
        "city": city,
        "files": {k: str(v) for k, v in outputs.items()},
        "opensidewalks_features": len(osw.get("features", [])),
        "curb_inventory_rows": n_curbs,
        "entrance_candidates": len(entrances),
        "curb_regulations_rows": sum(1 for _ in outputs["curb_regulations"].open("r", encoding="utf-8")) if outputs["curb_regulations"].exists() else 0,
        "dem_rows": sum(1 for _ in outputs["dem"].open("r", encoding="utf-8")) if outputs["dem"].exists() else 0,
        "paper_ready": False,
        "paper_blockers": [
            "curb_regulations are fail-closed unless authoritative CDS/municipal loading-zone data are added",
            "curb_height_m/deployment_clearance_m/sidewalk_width_m may remain null unless present in municipal/audited source",
            "entrance candidates require snapping and audit before publication-ready evaluation",
            "DEM sampling file is empty unless a raster/point sampler is run",
        ],
    }


def fetch_city(city: str, args: argparse.Namespace) -> Dict[str, Any]:
    spec = CITY_SPECS[city]
    osm_path = ROOT / "osm" / f"{city}_sidewalks.json"
    if args.skip_overpass:
        if not osm_path.exists():
            raise FileNotFoundError(f"--skip_overpass requested but {osm_path} does not exist")
    else:
        osm_path = download_overpass(city, spec, args.overpass_endpoint, args.overpass_timeout_s, args.force)
        if args.overpass_sleep_s > 0:
            time.sleep(args.overpass_sleep_s)
    osw, curbs, entrances = osm_to_geojson_and_evidence(city, osm_path)
    if city == "boston" and not args.skip_municipal:
        downloaded = download_boston_city_gis(args.force)
        append_boston_gis_evidence(downloaded, curbs, entrances)
    city_gis_dir = ROOT / "city_gis" / city
    city_gis_dir.mkdir(parents=True, exist_ok=True)
    (city_gis_dir / "README.external_sources.md").write_text(
        f"# {city} external GIS notes\n\n"
        f"Municipal mode: {spec['municipal']}\n\n"
        "This folder stores raw municipal downloads when the script can retrieve them. "
        "OSM-derived files are real public data but are not a substitute for audited curb "
        "inventory, authoritative curb regulations, or entrance validation.\n",
        encoding="utf-8",
    )
    return write_city_outputs(city, osw, curbs, entrances, args.force)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Fetch public external files for AbilityBench-AV four-city preparation.")
    p.add_argument("--external_root", default="/data0/senzeyu2/dataset/abilitybench_external")
    p.add_argument("--cities", default="boston,pittsburgh,vegas,singapore", help="comma-separated subset")
    p.add_argument("--overpass_endpoint", default="https://overpass-api.de/api/interpreter")
    p.add_argument("--overpass_timeout_s", type=int, default=300)
    p.add_argument("--overpass_sleep_s", type=float, default=8.0)
    p.add_argument("--skip_overpass", action="store_true", help="reuse existing osm/<city>_sidewalks.json")
    p.add_argument("--skip_municipal", action="store_true", help="download only OSM-derived sources")
    p.add_argument("--force", action="store_true")
    return p.parse_args()


ROOT: Path


def main() -> None:
    global ROOT
    args = parse_args()
    ROOT = Path(args.external_root).expanduser()
    selected = [c.strip().lower() for c in args.cities.split(",") if c.strip()]
    unknown = [c for c in selected if c not in CITY_SPECS]
    if unknown:
        raise SystemExit(f"unknown cities: {unknown}; valid={sorted(CITY_SPECS)}")
    for sub in ["osm", "opensidewalks", "city_gis", "curb_inventory", "curb_regulations", "entrances", "dem", "reports"]:
        (ROOT / sub).mkdir(parents=True, exist_ok=True)
    reports = []
    for city in selected:
        try:
            reports.append(fetch_city(city, args))
        except (urllib.error.URLError, TimeoutError, RuntimeError, FileNotFoundError, json.JSONDecodeError) as e:
            reports.append({"city": city, "error": str(e), "paper_ready": False})
            log(f"[error] {city}: {e}")
    report = {"external_root": str(ROOT), "cities": reports}
    write_json(ROOT / "reports" / "external_source_report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
