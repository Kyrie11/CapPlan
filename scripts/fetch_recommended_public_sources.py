#!/usr/bin/env python
"""Fetch the recommended *raw/candidate* public GIS layers for AbilityBench-AV.

This utility deliberately does not manufacture paper ground truth. It downloads
official public layers where stable machine APIs exist, normalizes them into
conservative candidate/topology layers, and records failures with manual fallback
instructions. DEM and sources requiring interactive/login flows remain manual.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

try:
    import yaml  # type: ignore
except Exception as exc:  # pragma: no cover
    raise RuntimeError("pyyaml is required") from exc

from download_arcgis_layer import download_layer

PROJECT_ROOT = Path(__file__).resolve().parents[1]
BOSTON_BASE = "https://gisportal.boston.gov/arcgis/rest/services/Infrastructure/OpenData/MapServer"
BOSTON_PWD_BASE = "https://gisportal.boston.gov/ArcGIS/rest/services/PWD/Cartegraph_PWD_readonly/MapServer"
VEGAS_TAXI = "https://mapdata.lasvegasnevada.gov/clvgis/rest/services/Transportation/CLV_ParkingServices_ParkingZones/MapServer/4"
PASDA_ALLEGHENY_ADDRESS_POINTS = "https://mapservices.pasda.psu.edu/server/rest/services/pasda/AlleghenyCounty/MapServer/32"
WPRDC = "https://data.wprdc.org"


def expand_path(value: str) -> Path:
    p = Path(str(value).format(project_root=str(PROJECT_ROOT))).expanduser()
    return p if p.is_absolute() else PROJECT_ROOT / p


def run(cmd: List[str]) -> None:
    subprocess.check_call(cmd, cwd=PROJECT_ROOT)


def normalize(input_path: Path, output_path: Path, profile: str, source: str, extra: List[str] | None = None) -> None:
    cmd = [
        sys.executable, "scripts/normalize_accessibility_evidence.py",
        "--input", str(input_path), "--output", str(output_path),
        "--profile", profile, "--source", source,
    ]
    cmd.extend(extra or [])
    run(cmd)


def ckan(package: str, resource: str, out_dir: Path, output_name: str, force: bool, *, resource_id: str | None = None) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    existing = sorted(
        [p for p in out_dir.glob(output_name + ".*") if not p.name.endswith(".provenance.json") and not p.name.endswith(".part")],
        key=lambda p: p.stat().st_mtime, reverse=True,
    )
    if existing and not force:
        return existing[0]
    cmd = [
        sys.executable, "scripts/download_ckan_resource.py",
        "--portal", WPRDC, "--package_id", package,
        "--output_dir", str(out_dir), "--output_name", output_name,
    ]
    if resource_id:
        cmd.extend(["--resource_id", resource_id])
    else:
        cmd.extend(["--resource_name", resource])
    if force:
        cmd.append("--force")
    run(cmd)
    # download_ckan_resource infers extension; find newest exact stem.
    matches = sorted(out_dir.glob(output_name + ".*"), key=lambda p: p.stat().st_mtime, reverse=True)
    data = [p for p in matches if not p.name.endswith(".provenance.json") and not p.name.endswith(".part")]
    if not data:
        raise RuntimeError(f"CKAN download succeeded but output not found for {output_name}")
    return data[0]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", default="configs/abilitybench_nuplan_real.yaml")
    p.add_argument("--cities", default="boston,pittsburgh,vegas,singapore")
    p.add_argument("--force", action="store_true")
    p.add_argument("--strict", action="store_true", help="Exit non-zero if any automatable source fails.")
    args = p.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8")) or {}
    external = expand_path(cfg["external_root"])
    cities = [x.strip() for x in args.cities.replace("+", ",").split(",") if x.strip()]
    results: List[Dict[str, Any]] = []
    failures = 0

    def attempt(city: str, name: str, fn, fallback: str) -> None:
        nonlocal failures
        try:
            out = fn()
            results.append({"city": city, "source": name, "status": "PASS", "output": str(out) if out else None})
        except Exception as exc:
            import traceback
            failures += 1
            results.append({
                "city": city, "source": name, "status": "FAIL",
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(limit=20),
                "manual_fallback": fallback,
            })

    if "boston" in cities:
        bbox = cfg["cities"]["boston"]["bbox"]
        raw = external / "raw" / "arcgis" / "boston"
        norm = external / "normalized" / "city_gis" / "boston"
        specs = [
            ("sidewalk_inventory", 0, "boston_sidewalk", "sidewalk_inventory.geojson"),
            ("ramp_inventory", 3, "boston_ramp", "ramp_inventory.jsonl"),
            ("sidewalk_centerline", 5, "boston_sidewalk_centerline", "sidewalk_centerline.geojson"),
            ("curbs", 6, "boston_curb", "curbs.geojson"),
        ]
        for label, layer_id, profile, norm_name in specs:
            def work(label=label, layer_id=layer_id, profile=profile, norm_name=norm_name):
                rp = raw / f"{label}.geojson"
                if args.force or not rp.exists():
                    download_layer(f"{BOSTON_BASE}/{layer_id}", rp, bbox=bbox)
                np = norm / norm_name
                # Unit-sensitive physical fields remain unknown by default. Raw
                # values are preserved and can be re-normalized later once units
                # are documented/verified for the selected records.
                normalize(rp, np, profile, f"City of Boston Infrastructure OpenData layer {layer_id}")
                return np
            attempt("boston", label, work, f"Open {BOSTON_BASE}/{layer_id}, use Query with outSR=4326/f=geojson, save to {raw}/{label}.geojson")

        # PWD Cartegraph publishes explicit per-record Width_unit/Slope_unit
        # fields. Prefer these layers for paper physical accessibility attributes
        # because normalization can convert units without guessing. They do not
        # establish autonomous-mobility stopping legality or deployment clearance.
        pwd_specs = [
            ("pwd_ada_ramps", 0, "boston_pwd_ada_ramp", "pwd_ada_ramps.jsonl"),
            ("pwd_sidewalks", 7, "boston_pwd_sidewalk", "pwd_sidewalks.geojson"),
        ]
        for label, layer_id, profile, norm_name in pwd_specs:
            def pwd_work(label=label, layer_id=layer_id, profile=profile, norm_name=norm_name):
                rp = raw / f"{label}.geojson"
                if args.force or not rp.exists():
                    download_layer(f"{BOSTON_PWD_BASE}/{layer_id}", rp, bbox=bbox)
                np = norm / norm_name
                normalize(rp, np, profile, f"City of Boston PWD Cartegraph layer {layer_id}")
                return np
            attempt("boston", label, pwd_work, f"Open {BOSTON_PWD_BASE}/{layer_id}, Query the Boston AOI with outSR=4326/f=geojson, save to {raw}/{label}.geojson")

    if "pittsburgh" in cities:
        raw = external / "raw" / "wprdc" / "pittsburgh"
        raw.mkdir(parents=True, exist_ok=True)
        # Real pedestrian geometry, not blockgroup ratio.
        def sw():
            src = ckan("sidewalk-to-street-walkability-ratio", "Sidewalks and Steps SHP", raw, "sidewalks_and_steps", args.force)
            dst = external / "normalized" / "city_gis" / "pittsburgh" / "sidewalks_steps.geojson"
            normalize(src, dst, "pittsburgh_sidewalks_steps", "WPRDC Sidewalks and Steps")
            return dst
        attempt("pittsburgh", "Sidewalks and Steps SHP", sw,
                "WPRDC dataset 'Sidewalk to Street \"Walkability\" Ratio' -> Data and Resources -> 'Sidewalks and Steps SHP'; save ZIP under data/external/raw/wprdc/pittsburgh/")

        # Stable WPRDC datastore resource IDs verified from the official CKAN
        # resource pages. Current Payment Points is the primary geometry source;
        # archive/rates are retained only as temporal/context metadata.
        parking_specs = [
            ("Current Payment Points", "payment_points_current", "9ed126cc-3c06-496e-bd08-b7b6b14b4109"),
            ("Payment Points (Archives)", "payment_points_archive", "db139ccd-6753-48ad-b3ff-118fe2223d55"),
            ("Payments Points + Rates", "payment_points_rates", "aefaf190-7f4c-4466-a28b-1b7ce039419d"),
        ]
        for resource, stem, resource_id in parking_specs:
            def pp(resource=resource, stem=stem, resource_id=resource_id):
                src = ckan(
                    "pittsburgh-parking-meters-and-payment-points", resource, raw, stem, args.force,
                    resource_id=resource_id,
                )
                if resource == "Current Payment Points":
                    dst = external / "normalized" / "candidates" / "pittsburgh" / "payment_points_current.jsonl"
                    # WPRDC includes a minority of current/non-spatial payment
                    # point rows whose latitude/longitude fields are blank.
                    # They are not PUDO coordinates and must be dropped rather
                    # than fabricated. The normalization report records exactly
                    # how many rows were skipped.
                    normalize(src, dst, "pittsburgh_parking_meter", "Pittsburgh Parking Authority via WPRDC", ["--skip_invalid"])
                    return dst
                return src
            attempt("pittsburgh", resource, pp,
                    f"WPRDC 'Pittsburgh Parking Meters and Payment Points' -> '{resource}'; save under {raw}/")

        def closures():
            src = ckan("street-closures", "Street Closures", raw, "street_closures", args.force)
            dst = external / "normalized" / "dynamic" / "pittsburgh" / "street_closures.jsonl"
            normalize(src, dst, "pittsburgh_street_closure", "City of Pittsburgh DOMI Street Closures via WPRDC", ["--skip_invalid"])
            return dst
        attempt("pittsburgh", "Street Closures", closures,
                "WPRDC dataset 'DOMI Street Closures For GIS Mapping' -> 'Street Closures'; save as data/external/raw/wprdc/pittsburgh/street_closures.csv")

        def addresses():
            # Prefer the current Allegheny County layer hosted by PASDA. Keep
            # PASDA raw data in its own provenance directory rather than under
            # WPRDC. The ArcGIS query explicitly requests outSR=4326 and crops
            # to the Pittsburgh AOI, avoiding a huge countywide file and
            # NAD83/WGS84 ambiguity in manually downloaded snapshots.
            pasda_raw = external / "raw" / "pasda" / "pittsburgh"
            pasda_raw.mkdir(parents=True, exist_ok=True)
            src = pasda_raw / "address_points_aoi_wgs84.geojson"
            if args.force or not src.exists():
                download_layer(PASDA_ALLEGHENY_ADDRESS_POINTS, src, bbox=cfg["cities"]["pittsburgh"]["bbox"])
            dst = external / "normalized" / "candidates" / "pittsburgh" / "address_points.geojson"
            normalize(src, dst, "pittsburgh_address_point", "Allegheny County Address Points via PASDA")
            return dst
        attempt("pittsburgh", "Address Points GeoJSON", addresses,
                f"PASDA Allegheny County ArcGIS layer {PASDA_ALLEGHENY_ADDRESS_POINTS}; query the Pittsburgh AOI with outSR=4326 and save as data/external/raw/pasda/pittsburgh/address_points_aoi_wgs84.geojson")

    if "vegas" in cities:
        bbox = cfg["cities"]["vegas"]["bbox"]
        def vegas_taxi():
            raw = external / "raw" / "arcgis" / "vegas" / "taxi_zones.geojson"
            if args.force or not raw.exists():
                download_layer(VEGAS_TAXI, raw, bbox=bbox)
            dst = external / "normalized" / "candidates" / "vegas" / "taxi_zones.jsonl"
            normalize(raw, dst, "vegas_parking_zone", "City of Las Vegas Taxi Zones")
            return dst
        attempt("vegas", "Taxi Zones", vegas_taxi,
                f"ArcGIS REST layer {VEGAS_TAXI}; Query -> GeoJSON; save to data/external/raw/arcgis/vegas/taxi_zones.geojson")

    if "singapore" in cities:
        # Discover the current release URLs from LTA's official static-data
        # catalogue instead of hard-coding release-month filenames. Passenger
        # Pickup Bay is the closest official PUDO candidate layer; Footpath and
        # Kerbline strengthen topology/curb geometry. Taxi Stand is secondary.
        lta_raw = external / "raw" / "lta" / "singapore"
        lta_specs = [
            ("Passenger Pickup Bay", "passenger_pickup_bay", "lta_passenger_pickup_bay", external / "normalized" / "candidates" / "singapore" / "passenger_pickup_bay.jsonl"),
            ("Taxi Stand", "taxi_stand", "lta_taxi_stand", external / "normalized" / "candidates" / "singapore" / "taxi_stand.jsonl"),
            ("Footpath", "footpath", "lta_footpath", external / "normalized" / "city_gis" / "singapore" / "footpath.geojson"),
            ("Kerbline", "kerbline", "lta_kerbline", external / "normalized" / "city_gis" / "singapore" / "kerbline.geojson"),
            ("Train Station Exit", "train_station_exit", "government_entrance", external / "normalized" / "entrances" / "singapore.geojson"),
        ]
        for label, dataset, profile, dst in lta_specs:
            def lta_work(label=label, dataset=dataset, profile=profile, dst=dst):
                cmd = [sys.executable, "scripts/download_lta_static_geospatial.py", "--dataset", dataset, "--output_dir", str(lta_raw)]
                if args.force:
                    cmd.append("--force")
                run(cmd)
                src = lta_raw / f"{dataset}.zip"
                normalize(src, dst, profile, f"Singapore LTA DataMall {label}")
                return dst
            attempt(
                "singapore", f"LTA {label}", lta_work,
                f"LTA DataMall -> Static Datasets -> '{label}' -> Whole Island (ESRI Shapefile). Save ZIP as {lta_raw}/{dataset}.zip and run normalize_accessibility_evidence.py with --profile {profile}.",
            )

    report = {
        "status": "PASS" if failures == 0 else ("FAIL" if args.strict else "PARTIAL"),
        "results": results,
        "note": "Most fetched layers are topology/candidate/dynamic evidence. Explicit authoritative source semantics (for example Singapore LTA Passenger Pickup Bay stopping use or government entrance layers) are preserved, but missing physical interface facts are never invented.",
    }
    path = external / "reports" / "recommended_public_sources.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    print(f"RECOMMENDED_PUBLIC_SOURCE_FETCH={report['status']}")
    if failures and args.strict:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
