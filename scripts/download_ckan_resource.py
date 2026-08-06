#!/usr/bin/env python
"""Download a CKAN resource through the official package API with validation.

This avoids scraping HTML download pages. It is suitable for WPRDC and other
CKAN portals. The script never interprets a downloaded layer as accessibility
truth; it only preserves the original resource and provenance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

USER_AGENT = "CapPlan-AbilityBench/1.0 (research dataset preparation; CKAN client)"


def get_bytes(url: str, *, timeout: int, retries: int) -> tuple[bytes, str, str]:
    last: Optional[Exception] = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                data = resp.read()
                ctype = str(resp.headers.get("Content-Type", ""))
                final_url = str(resp.geturl())
            if not data:
                raise RuntimeError("zero-byte response")
            lower = data[:512].lstrip().lower()
            if lower.startswith((b"<html", b"<!doctype html")):
                raise RuntimeError("server returned HTML instead of a dataset")
            return data, ctype or mimetypes.guess_type(final_url)[0] or "", final_url
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, RuntimeError) as exc:
            last = exc
            if attempt + 1 < retries:
                time.sleep(min(2 ** attempt, 20))
    raise RuntimeError(f"download failed after {retries} attempts: {url}: {last}")


def get_json(url: str, *, timeout: int, retries: int) -> Dict[str, Any]:
    data, _, _ = get_bytes(url, timeout=timeout, retries=retries)
    try:
        payload = json.loads(data.decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"CKAN API did not return JSON: {url}: {exc}") from exc
    if not payload.get("success"):
        raise RuntimeError(f"CKAN API error: {payload}")
    return payload


def safe_name(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
    return value.strip("._") or "resource"


def infer_suffix(resource: Dict[str, Any], url: str, content_type: str) -> str:
    fmt = str(resource.get("format") or "").strip().lower()
    mapping = {"geojson": ".geojson", "json": ".json", "csv": ".csv", "zip": ".zip", "kml": ".kml", "gpkg": ".gpkg", "shp": ".zip"}
    if fmt in mapping:
        return mapping[fmt]
    suffix = Path(urllib.parse.urlparse(url).path).suffix
    if suffix:
        return suffix
    guessed = mimetypes.guess_extension(content_type.split(";", 1)[0].strip()) if content_type else None
    return guessed or ".bin"


def validate_payload(path: Path, logical_suffix: str) -> Dict[str, Any]:
    raw = path.read_bytes()
    result: Dict[str, Any] = {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
    suffix = logical_suffix.lower()
    if suffix in {".json", ".geojson"}:
        payload = json.loads(raw.decode("utf-8"))
        if isinstance(payload, dict) and payload.get("type") == "FeatureCollection":
            n = len(payload.get("features") or [])
            if n == 0:
                raise RuntimeError("GeoJSON contains zero features")
            result["records"] = n
        elif isinstance(payload, (dict, list)):
            result["records"] = len(payload) if isinstance(payload, list) else 1
    elif suffix == ".csv":
        lines = raw.decode("utf-8-sig", errors="strict").splitlines()
        if len(lines) < 2:
            raise RuntimeError("CSV contains no data rows")
        result["records"] = len(lines) - 1
    elif suffix == ".zip" and not raw.startswith(b"PK"):
        raise RuntimeError("file has .zip suffix but is not a ZIP archive")
    return result


def choose_resource(resources: List[Dict[str, Any]], resource_id: Optional[str], formats: List[str]) -> Dict[str, Any]:
    if resource_id:
        for r in resources:
            if str(r.get("id")) == resource_id:
                return r
        raise RuntimeError(f"resource id not found: {resource_id}")
    wanted = [x.strip().lower() for x in formats if x.strip()]
    for fmt in wanted:
        for r in resources:
            if str(r.get("format") or "").strip().lower() == fmt and r.get("url"):
                return r
    for r in resources:
        if r.get("url"):
            return r
    raise RuntimeError("package has no downloadable resource")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--portal", default="https://data.wprdc.org")
    p.add_argument("--package_id", required=True)
    p.add_argument("--resource_id", default=None)
    p.add_argument("--prefer_formats", default="GeoJSON,ZIP,CSV,JSON")
    p.add_argument("--output_dir", required=True)
    p.add_argument("--output_name", default=None)
    p.add_argument("--timeout", type=int, default=180)
    p.add_argument("--retries", type=int, default=5)
    p.add_argument("--force", action="store_true")
    args = p.parse_args()

    api = args.portal.rstrip("/") + "/api/3/action/package_show?id=" + urllib.parse.quote(args.package_id)
    package = get_json(api, timeout=args.timeout, retries=args.retries)["result"]
    resource = choose_resource(package.get("resources") or [], args.resource_id, args.prefer_formats.split(","))
    data, ctype, final_url = get_bytes(str(resource["url"]), timeout=args.timeout, retries=args.retries)
    suffix = infer_suffix(resource, final_url, ctype)
    name = safe_name(args.output_name or resource.get("name") or package.get("name") or args.package_id)
    if Path(name).suffix:
        suffix = ""
    output = Path(args.output_dir).expanduser() / f"{name}{suffix}"
    if output.exists() and not args.force:
        raise SystemExit(f"{output} exists; use --force to replace it")
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(output.suffix + ".part")
    tmp.write_bytes(data)
    stats = validate_payload(tmp, output.suffix)
    tmp.replace(output)
    report = {
        "portal": args.portal,
        "package_id": args.package_id,
        "package_title": package.get("title"),
        "resource_id": resource.get("id"),
        "resource_name": resource.get("name"),
        "resource_format": resource.get("format"),
        "source_url": final_url,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "output": str(output),
        **stats,
    }
    report_path = output.with_suffix(output.suffix + ".provenance.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
