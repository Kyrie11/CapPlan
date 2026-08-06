#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import List

DEFAULT_FILTERS = [
    "w/highway=footway,path,pedestrian,steps,crossing",
    "w/footway=sidewalk,crossing,access_aisle",
    "w/sidewalk",
    "n/kerb",
    "n/curb",
    "n/curb_ramp",
    "n/entrance",
    "n/highway=crossing,bus_stop",
    "n/public_transport=platform,stop_position",
]


def run(cmd: List[str]) -> None:
    print(" ".join(cmd))
    subprocess.check_call(cmd)


def main() -> None:
    p = argparse.ArgumentParser(description="Crop a local OSM PBF to a nuPlan city bbox and export pedestrian/accessibility features as GeoJSON. Requires osmium-tool.")
    p.add_argument("--input_pbf", required=True)
    p.add_argument("--bbox", required=True, help="south,west,north,east")
    p.add_argument("--output", required=True)
    p.add_argument("--overwrite", action="store_true")
    args = p.parse_args()

    osmium = shutil.which("osmium")
    if not osmium:
        raise SystemExit("osmium not found. Install Ubuntu package `osmium-tool` before running this command.")
    src = Path(args.input_pbf)
    if not src.exists() or src.stat().st_size == 0:
        raise SystemExit(f"missing or empty input PBF: {src}")
    south, west, north, east = [float(x) for x in args.bbox.split(",")]
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists() and not args.overwrite:
        raise SystemExit(f"output exists: {out}; use --overwrite")

    with tempfile.TemporaryDirectory(prefix="capplan_osm_") as tmp_dir:
        tmp = Path(tmp_dir)
        crop = tmp / "crop.osm.pbf"
        filtered = tmp / "pedestrian.osm.pbf"
        exported = tmp / "pedestrian.geojson"
        # osmium uses xmin,ymin,xmax,ymax = west,south,east,north.
        run([osmium, "extract", "-b", f"{west},{south},{east},{north}", str(src), "-o", str(crop), "--overwrite"])
        run([osmium, "tags-filter", str(crop), *DEFAULT_FILTERS, "-o", str(filtered), "--overwrite"])
        run([osmium, "export", str(filtered), "-f", "geojson", "-o", str(exported), "--overwrite"])
        payload = json.loads(exported.read_text(encoding="utf-8"))
        features = payload.get("features") if isinstance(payload, dict) else None
        if payload.get("type") != "FeatureCollection" or not isinstance(features, list) or not features:
            raise RuntimeError("osmium export produced no GeoJSON features")
        props = payload.setdefault("properties", {})
        props.update({
            "source": "OpenStreetMap local PBF extract",
            "source_pbf": str(src),
            "retrieved_or_processed_at": datetime.now(timezone.utc).isoformat(),
            "schema_variant": "osm_geojson",
            "evidence_tier": "B_community_mapped",
            "authoritative": False,
            "bbox_south_west_north_east": [south, west, north, east],
        })
        part = out.with_suffix(out.suffix + ".part")
        part.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        part.replace(out)
    print(json.dumps({"output": str(out), "features": len(features)}, indent=2))


if __name__ == "__main__":
    main()
