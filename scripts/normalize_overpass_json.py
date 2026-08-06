#!/usr/bin/env python
"""Convert an Overpass JSON response into conservative OSM GeoJSON evidence.

The output is ordinary GeoJSON with explicit provenance metadata.  It is not
labeled OpenSidewalks and must not be treated as authoritative curb/sidewalk
measurements merely because an OSM tag is present.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List, Optional


def _point(el: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if el.get("lon") is None or el.get("lat") is None:
        return None
    return {"type": "Point", "coordinates": [float(el["lon"]), float(el["lat"])]}


def _line(el: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    coords: List[List[float]] = []
    for p in el.get("geometry") or []:
        if isinstance(p, dict) and p.get("lon") is not None and p.get("lat") is not None:
            coords.append([float(p["lon"]), float(p["lat"])])
    if len(coords) < 2:
        return None
    return {"type": "LineString", "coordinates": coords}


def convert(payload: Dict[str, Any], *, source_url: str, downloaded_at: Optional[str] = None) -> Dict[str, Any]:
    if not isinstance(payload, dict) or not isinstance(payload.get("elements"), list):
        raise ValueError("input is not an Overpass JSON object with an elements array")
    features: List[Dict[str, Any]] = []
    skipped = 0
    for el in payload["elements"]:
        if not isinstance(el, dict):
            skipped += 1
            continue
        typ = el.get("type")
        geom = _point(el) if typ == "node" else _line(el) if typ == "way" else None
        if geom is None:
            skipped += 1
            continue
        tags = el.get("tags") if isinstance(el.get("tags"), dict) else {}
        props: Dict[str, Any] = {
            **tags,
            "osm_type": typ,
            "osm_id": el.get("id"),
            "source": "OpenStreetMap/Overpass",
            "source_url": source_url,
            "evidence_tier": "B_community_mapped",
            "authoritative": False,
            "schema_variant": "osm_geojson",
        }
        if downloaded_at:
            props["downloaded_at"] = downloaded_at
        features.append({"type": "Feature", "id": f"{typ}/{el.get('id')}", "geometry": geom, "properties": props})
    if not features:
        raise ValueError("Overpass payload contains no usable point or line geometry")
    return {
        "type": "FeatureCollection",
        "features": features,
        "properties": {
            "source": "OpenStreetMap/Overpass",
            "source_url": source_url,
            "license": "ODbL-1.0",
            "evidence_tier": "B_community_mapped",
            "authoritative": False,
            "schema_variant": "osm_geojson",
            "input_elements": len(payload["elements"]),
            "skipped_elements": skipped,
            "generator": payload.get("generator"),
            "osm3s": payload.get("osm3s"),
        },
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input_json", required=True)
    p.add_argument("--output_geojson", required=True)
    p.add_argument("--source_url", default="https://overpass-api.de/api/interpreter")
    p.add_argument("--downloaded_at", default=None)
    args = p.parse_args()
    src = Path(args.input_json)
    payload = json.loads(src.read_text(encoding="utf-8"))
    out = convert(payload, source_url=args.source_url, downloaded_at=args.downloaded_at)
    dst = Path(args.output_geojson)
    dst.parent.mkdir(parents=True, exist_ok=True)
    part = dst.with_suffix(dst.suffix + ".part")
    part.write_text(json.dumps(out, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    part.replace(dst)
    print(json.dumps({"input": str(src), "output": str(dst), "features": len(out["features"])}, indent=2))


if __name__ == "__main__":
    main()
