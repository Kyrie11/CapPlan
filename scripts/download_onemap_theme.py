#!/usr/bin/env python
"""Download a Singapore OneMap thematic layer and convert it to GeoJSON."""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

BASE = "https://www.onemap.gov.sg/api/public/themesvc/retrieveTheme"
USER_AGENT = "CapPlan-AbilityBench/1.0 (research dataset preparation; OneMap client)"


def request_json(url: str, token: str, retries: int, timeout: int) -> Dict[str, Any]:
    last: Optional[Exception] = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url,
                headers={"Authorization": token if token.lower().startswith("bearer ") else f"Bearer {token}", "User-Agent": USER_AGENT, "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                raw = resp.read()
            if raw.lstrip().lower().startswith((b"<html", b"<!doctype html")):
                raise RuntimeError("OneMap returned HTML instead of JSON")
            payload = json.loads(raw.decode("utf-8"))
            if isinstance(payload, dict) and payload.get("error"):
                raise RuntimeError(str(payload["error"]))
            return payload
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
            last = exc
            if attempt + 1 < retries:
                time.sleep(min(2 ** attempt, 30))
    raise RuntimeError(f"OneMap request failed after {retries} attempts: {last}")


def parse_latlng(value: Any) -> Any:
    if isinstance(value, str):
        return json.loads(value)
    return value


def flatten_points(value: Any) -> List[List[float]]:
    out: List[List[float]] = []
    if isinstance(value, list):
        if len(value) >= 2 and all(isinstance(x, (int, float)) for x in value[:2]):
            out.append([float(value[0]), float(value[1])])
        else:
            for item in value:
                out.extend(flatten_points(item))
    return out


def geometry_from_record(record: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    raw = parse_latlng(record.get("LatLng") or record.get("LATLNG") or record.get("latLng"))
    points = flatten_points(raw)
    if not points:
        return None
    kind = str(record.get("Type") or record.get("TYPE") or "Point").lower()
    if kind == "point" or len(points) == 1:
        return {"type": "Point", "coordinates": points[0]}
    if "polygon" in kind:
        ring = points
        if ring[0] != ring[-1]:
            ring = ring + [ring[0]]
        return {"type": "Polygon", "coordinates": [ring]}
    return {"type": "LineString", "coordinates": points}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--query_name", required=True)
    p.add_argument("--bbox", required=True, help="south,west,north,east in EPSG:4326")
    p.add_argument("--output", required=True)
    p.add_argument("--token", default=None)
    p.add_argument("--token_env", default="ONEMAP_TOKEN")
    p.add_argument("--timeout", type=int, default=120)
    p.add_argument("--retries", type=int, default=5)
    args = p.parse_args()
    token = args.token or os.environ.get(args.token_env)
    if not token:
        raise SystemExit(f"provide --token or set {args.token_env}")
    bbox = [float(x) for x in args.bbox.split(",")]
    if len(bbox) != 4:
        raise SystemExit("bbox must be south,west,north,east")
    params = urllib.parse.urlencode({"queryName": args.query_name, "extents": ",".join(str(x) for x in bbox)})
    payload = request_json(f"{BASE}?{params}", token, args.retries, args.timeout)
    records = payload.get("SrchResults") or payload.get("SearchResults") or []
    if not isinstance(records, list):
        raise RuntimeError("unexpected OneMap response: missing SrchResults list")
    metadata: List[Dict[str, Any]] = []
    features: List[Dict[str, Any]] = []
    for idx, record in enumerate(records):
        if not isinstance(record, dict):
            continue
        geom = geometry_from_record(record)
        if geom is None:
            metadata.append(record)
            continue
        props = {k: v for k, v in record.items() if k.lower() != "latlng"}
        props.setdefault("feature_id", f"{args.query_name}:{idx:07d}")
        props["source"] = "Singapore OneMap Themes API"
        props["authoritative"] = True
        props["evidence_tier"] = "A_government_thematic_layer"
        features.append({"type": "Feature", "geometry": geom, "properties": props})
    if not features:
        raise RuntimeError("OneMap returned zero geometry features for this query/bbox")
    fc = {
        "type": "FeatureCollection",
        "features": features,
        "properties": {
            "query_name": args.query_name,
            "bbox_south_west_north_east": bbox,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "source_url": BASE,
            "metadata_records": metadata,
            "record_count": len(features),
            "authoritative": True,
            "evidence_tier": "A_government_thematic_layer",
        },
    }
    out = Path(args.output).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(out.suffix + ".part")
    tmp.write_text(json.dumps(fc, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    json.loads(tmp.read_text(encoding="utf-8"))
    tmp.replace(out)
    print(json.dumps({"output": str(out), "features": len(features), "metadata_records": len(metadata)}, indent=2))


if __name__ == "__main__":
    main()
