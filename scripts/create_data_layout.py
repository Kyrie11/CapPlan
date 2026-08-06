#!/usr/bin/env python
"""Create the fail-closed CapPlan data directory layout under the repository."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
CITIES = ("boston", "pittsburgh", "vegas", "singapore")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(PROJECT_ROOT / "data"))
    args = parser.parse_args()
    root = Path(args.root).expanduser().resolve()
    dirs = [
        root / "nuplan" / "nuplan-v1.1" / "splits",
        root / "nuplan" / "maps",
        root / "external" / "raw" / "osm_pbf",
        root / "external" / "raw" / "osm_overpass",
        root / "external" / "raw" / "arcgis",
        root / "external" / "raw" / "wprdc",
        root / "external" / "raw" / "lta",
        root / "external" / "raw" / "onemap",
        root / "external" / "raw" / "manual",
        root / "external" / "normalized" / "osm",
        root / "external" / "normalized" / "opensidewalks",
        root / "external" / "normalized" / "curb_inventory",
        root / "external" / "normalized" / "curb_regulations",
        root / "external" / "normalized" / "entrances",
        root / "external" / "normalized" / "dem",
        root / "external" / "normalized" / "fleet",
        root / "external" / "normalized" / "dynamic_overlays",
        root / "external" / "georeference",
        root / "external" / "manifests",
        root / "external" / "reports",
        root / "external" / "schemas",
        root / "outputs" / "prepared",
        root / "outputs" / "datasets",
        root / "outputs" / "nuplan_closed_loop_jobs",
        root / "cache",
    ]
    for city in CITIES:
        dirs += [
            root / "external" / "normalized" / "city_gis" / city,
            root / "external" / "audits" / city,
            root / "external" / "raw" / "dem" / city,
            root / "external" / "raw" / "arcgis" / city,
            root / "external" / "raw" / "manual" / city,
        ]
    dirs += [
        root / "external" / "raw" / "wprdc" / "pittsburgh",
        root / "external" / "raw" / "lta" / "singapore",
        root / "external" / "raw" / "onemap" / "singapore",
    ]
    for directory in dirs:
        directory.mkdir(parents=True, exist_ok=True)
    marker = root / ".capplan_data_root.json"
    marker.write_text(
        json.dumps({"schema_version": "2.0", "cities": list(CITIES), "root": str(root)}, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"created_root": str(root), "directories": len(dirs), "marker": str(marker)}, indent=2))


if __name__ == "__main__":
    main()
