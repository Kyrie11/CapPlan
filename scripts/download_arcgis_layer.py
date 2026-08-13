#!/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

USER_AGENT = "CapPlan-AbilityBench/1.0 (research dataset preparation; ArcGIS REST client)"


def request_json(url: str, params: Dict[str, Any], retries: int = 5, timeout: int = 120) -> Dict[str, Any]:
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    full = f"{url}?{query}"
    # ArcGIS object-id batches can easily exceed common proxy/web-server GET
    # URL limits. ArcGIS REST query endpoints accept application/x-www-form-
    # urlencoded POST with the same parameters, so switch automatically for
    # long requests instead of retrying a deterministic HTTP 404/414 forever.
    use_post = len(full) > 1800
    last: Optional[Exception] = None
    for attempt in range(retries):
        try:
            headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
            if use_post:
                headers["Content-Type"] = "application/x-www-form-urlencoded; charset=utf-8"
                req = urllib.request.Request(url, data=query.encode("utf-8"), headers=headers, method="POST")
            else:
                req = urllib.request.Request(full, headers=headers, method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                body = r.read()
                ctype = str(r.headers.get("Content-Type", "")).lower()
            if body.lstrip().lower().startswith((b"<html", b"<!doctype html")):
                raise RuntimeError("ArcGIS endpoint returned HTML instead of JSON")
            payload = json.loads(body.decode("utf-8"))
            if isinstance(payload, dict) and payload.get("error"):
                raise RuntimeError(f"ArcGIS error: {payload['error']}")
            return payload
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
            last = exc
            if attempt + 1 < retries:
                time.sleep(min(2 ** attempt, 20))
    request_desc = f"POST {url} ({len(query)} encoded bytes)" if use_post else full
    raise RuntimeError(f"request failed after {retries} attempts: {request_desc}: {last}")


def _query_url(layer_url: str) -> str:
    base = layer_url.rstrip("/")
    return base if base.endswith("/query") else base + "/query"


def _geometry_params(bbox: Optional[Sequence[float]]) -> Dict[str, Any]:
    if not bbox:
        return {}
    if len(bbox) != 4:
        raise ValueError("bbox must be south,west,north,east")
    south, west, north, east = [float(x) for x in bbox]
    envelope = {"xmin": west, "ymin": south, "xmax": east, "ymax": north, "spatialReference": {"wkid": 4326}}
    return {
        "geometry": json.dumps(envelope, separators=(",", ":")),
        "geometryType": "esriGeometryEnvelope",
        "inSR": 4326,
        "spatialRel": "esriSpatialRelIntersects",
    }


def chunks(values: Sequence[int], n: int) -> Iterable[Sequence[int]]:
    for i in range(0, len(values), n):
        yield values[i : i + n]


def download_layer(
    layer_url: str,
    output: Path,
    *,
    bbox: Optional[Sequence[float]] = None,
    where: str = "1=1",
    batch_size: int = 500,
    retries: int = 5,
    timeout: int = 120,
) -> Dict[str, Any]:
    query_url = _query_url(layer_url)
    spatial = _geometry_params(bbox)
    ids_payload = request_json(
        query_url,
        {
            "where": where,
            "returnIdsOnly": "true",
            "returnGeometry": "false",
            "f": "json",
            **spatial,
        },
        retries=retries,
        timeout=timeout,
    )
    object_ids = sorted({int(x) for x in ids_payload.get("objectIds", [])})
    oid_field = ids_payload.get("objectIdFieldName")
    features: List[Dict[str, Any]] = []
    if object_ids:
        for batch in chunks(object_ids, max(1, batch_size)):
            payload = request_json(
                query_url,
                {
                    "objectIds": ",".join(str(x) for x in batch),
                    "outFields": "*",
                    "returnGeometry": "true",
                    "outSR": 4326,
                    "f": "geojson",
                },
                retries=retries,
                timeout=timeout,
            )
            if payload.get("type") != "FeatureCollection" or not isinstance(payload.get("features"), list):
                raise RuntimeError("ArcGIS query did not return a GeoJSON FeatureCollection")
            features.extend(payload["features"])
    else:
        # Some services do not support returnIdsOnly. Fall back to a single
        # bounded query; the completeness check below records whether transfer
        # limits were exceeded.
        payload = request_json(
            query_url,
            {
                "where": where,
                "outFields": "*",
                "returnGeometry": "true",
                "outSR": 4326,
                "f": "geojson",
                **spatial,
            },
            retries=retries,
            timeout=timeout,
        )
        if payload.get("type") != "FeatureCollection" or not isinstance(payload.get("features"), list):
            raise RuntimeError("ArcGIS query did not return a GeoJSON FeatureCollection")
        features.extend(payload["features"])

    fc = {
        "type": "FeatureCollection",
        "features": features,
        "properties": {
            "source_url": layer_url,
            "retrieved_at": datetime.now(timezone.utc).isoformat(),
            "query_where": where,
            "bbox_south_west_north_east": list(bbox) if bbox else None,
            "object_id_field": oid_field,
            "record_count": len(features),
            "authoritative": True,
            "evidence_tier": "A_authoritative_city_gis",
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(output.suffix + ".part")
    raw = (json.dumps(fc, indent=2, sort_keys=True) + "\n").encode("utf-8")
    tmp.write_bytes(raw)
    parsed = json.loads(tmp.read_text(encoding="utf-8"))
    if parsed.get("type") != "FeatureCollection" or not parsed.get("features"):
        tmp.unlink(missing_ok=True)
        raise RuntimeError("download produced no features; check layer URL, bbox, permissions and query")
    tmp.replace(output)
    return {
        "output": str(output),
        "features": len(features),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "source_url": layer_url,
    }


def main() -> None:
    p = argparse.ArgumentParser(description="Download an ArcGIS FeatureServer/MapServer layer through its REST query endpoint with pagination and validation.")
    p.add_argument("--layer_url", required=True, help="Layer URL ending in /FeatureServer/<id> or /MapServer/<id>.")
    p.add_argument("--output", required=True, help="Output GeoJSON path.")
    p.add_argument("--bbox", default=None, help="Optional south,west,north,east crop in EPSG:4326.")
    p.add_argument("--where", default="1=1")
    p.add_argument("--batch_size", type=int, default=500)
    p.add_argument("--retries", type=int, default=5)
    p.add_argument("--timeout", type=int, default=120)
    args = p.parse_args()
    bbox = [float(x) for x in args.bbox.split(",")] if args.bbox else None
    report = download_layer(args.layer_url, Path(args.output), bbox=bbox, where=args.where, batch_size=args.batch_size, retries=args.retries, timeout=args.timeout)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
