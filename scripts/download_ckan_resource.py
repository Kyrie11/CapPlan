#!/usr/bin/env python
"""Download a CKAN resource through the official package API with validation.

This avoids scraping HTML download pages. It is suitable for WPRDC and other
CKAN portals. The script never interprets a downloaded layer as accessibility
truth; it only preserves the original resource and provenance.
"""
from __future__ import annotations

import argparse
import hashlib
import http.client
import random
import socket
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
        except (urllib.error.URLError, urllib.error.HTTPError, ConnectionError, http.client.RemoteDisconnected, socket.timeout, TimeoutError, RuntimeError) as exc:
            last = exc
            if attempt + 1 < retries:
                time.sleep(min(2 ** attempt, 20) + random.uniform(0.0, 1.0))
    raise RuntimeError(f"download failed after {retries} attempts: {url}: {last}")


def download_to_file(url: str, destination: Path, *, timeout: int, retries: int) -> tuple[str, str]:
    """Stream a potentially large CKAN resource to disk with retry.

    Retrying starts from byte zero rather than attempting an unsafe partial
    resume because not every CKAN object store consistently supports Range.
    """
    last: Optional[Exception] = None
    destination.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
            first = b""
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
                ctype = str(resp.headers.get("Content-Type", ""))
                final_url = str(resp.geturl())
                with destination.open("wb") as f:
                    while True:
                        block = resp.read(1024 * 1024)
                        if not block:
                            break
                        if len(first) < 512:
                            first += block[: 512 - len(first)]
                        f.write(block)
            if not destination.exists() or destination.stat().st_size == 0:
                raise RuntimeError("zero-byte response")
            lower = first.lstrip().lower()
            if lower.startswith((b"<html", b"<!doctype html")):
                raise RuntimeError("server returned HTML instead of a dataset")
            return ctype or mimetypes.guess_type(final_url)[0] or "", final_url
        except (urllib.error.URLError, urllib.error.HTTPError, ConnectionError, http.client.RemoteDisconnected, socket.timeout, TimeoutError, RuntimeError, OSError) as exc:
            last = exc
            destination.unlink(missing_ok=True)
            if attempt + 1 < retries:
                time.sleep(min(2 ** attempt, 20) + random.uniform(0.0, 1.0))
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
    if not path.exists() or path.stat().st_size == 0:
        raise RuntimeError("downloaded file is empty")
    h = hashlib.sha256()
    size = 0
    first = b""
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            size += len(block)
            h.update(block)
            if len(first) < 1024:
                first += block[: 1024 - len(first)]
    result: Dict[str, Any] = {"bytes": size, "sha256": h.hexdigest()}
    suffix = logical_suffix.lower()
    if first.lstrip().lower().startswith((b"<html", b"<!doctype html")):
        raise RuntimeError("downloaded HTML/error page instead of data")
    if suffix == ".csv":
        lines = 0
        with path.open("r", encoding="utf-8-sig", errors="strict", newline="") as f:
            for _ in f:
                lines += 1
        if lines < 2:
            raise RuntimeError("CSV contains no data rows")
        result["records"] = lines - 1
    elif suffix == ".zip":
        import zipfile
        if not zipfile.is_zipfile(path):
            raise RuntimeError("file has .zip suffix but is not a ZIP archive")
        with zipfile.ZipFile(path) as zf:
            if not zf.namelist():
                raise RuntimeError("ZIP archive is empty")
            result["archive_members"] = len(zf.namelist())
    elif suffix in {".json", ".geojson"}:
        stripped = first.lstrip()
        if not stripped.startswith((b"{", b"[")):
            raise RuntimeError("JSON/GeoJSON resource does not begin with a JSON object/array")
        # Avoid loading very large GeoJSON into memory solely for validation.
        # GDAL will parse it during normalization; for modest files we can also
        # count FeatureCollection records here.
        if size <= 64 * 1024 * 1024:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and payload.get("type") == "FeatureCollection":
                n = len(payload.get("features") or [])
                if n == 0:
                    raise RuntimeError("GeoJSON contains zero features")
                result["records"] = n
            elif isinstance(payload, list):
                result["records"] = len(payload)
        else:
            result["records"] = None
            result["validation_note"] = "large_json_structural_check_only; parsed during normalization"
    return result


def choose_resource(resources: List[Dict[str, Any]], resource_id: Optional[str], resource_name: Optional[str], formats: List[str]) -> Dict[str, Any]:
    if resource_id:
        for r in resources:
            if str(r.get("id")) == resource_id:
                return r
        raise RuntimeError(f"resource id not found: {resource_id}")
    if resource_name:
        target = resource_name.strip().lower()
        exact = [r for r in resources if str(r.get("name") or "").strip().lower() == target and r.get("url")]
        if len(exact) == 1:
            return exact[0]
        contains = [r for r in resources if target in str(r.get("name") or "").strip().lower() and r.get("url")]
        if len(contains) == 1:
            return contains[0]
        names = [str(r.get("name") or r.get("id")) for r in resources]
        raise RuntimeError(f"resource_name={resource_name!r} did not match uniquely; available={names}")
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
    p.add_argument("--resource_name", default=None, help="Exact resource name preferred; falls back to a unique case-insensitive substring match.")
    p.add_argument("--list_resources", action="store_true", help="Print package resource names/ids/formats and exit without downloading.")
    p.add_argument("--prefer_formats", default="GeoJSON,ZIP,CSV,JSON")
    p.add_argument("--output_dir", required=True)
    p.add_argument("--output_name", default=None)
    p.add_argument("--timeout", type=int, default=180)
    p.add_argument("--retries", type=int, default=5)
    p.add_argument("--force", action="store_true")
    args = p.parse_args()

    api = args.portal.rstrip("/") + "/api/3/action/package_show?id=" + urllib.parse.quote(args.package_id)
    package = get_json(api, timeout=args.timeout, retries=args.retries)["result"]
    resources = package.get("resources") or []
    if args.list_resources:
        print(json.dumps([{"id": r.get("id"), "name": r.get("name"), "format": r.get("format"), "url_type": r.get("url_type")} for r in resources], ensure_ascii=False, indent=2))
        return
    resource = choose_resource(resources, args.resource_id, args.resource_name, args.prefer_formats.split(","))
    # Infer a preliminary extension from CKAN metadata; content type/final URL
    # are checked after streaming. This avoids buffering large countywide files.
    provisional_suffix = infer_suffix(resource, str(resource["url"]), "")
    name = safe_name(args.output_name or resource.get("name") or package.get("name") or args.package_id)
    if Path(name).suffix:
        provisional_suffix = ""
    output = Path(args.output_dir).expanduser() / f"{name}{provisional_suffix}"
    if output.exists() and not args.force:
        raise SystemExit(f"{output} exists; use --force to replace it")
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(output.suffix + ".part")
    ctype, final_url = download_to_file(str(resource["url"]), tmp, timeout=args.timeout, retries=args.retries)
    final_suffix = infer_suffix(resource, final_url, ctype)
    if not Path(name).suffix and final_suffix != provisional_suffix:
        output = Path(args.output_dir).expanduser() / f"{name}{final_suffix}"
        tmp2 = output.with_suffix(output.suffix + ".part")
        if tmp2 != tmp:
            tmp.replace(tmp2)
            tmp = tmp2
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
        "status": "PASS",
        **stats,
    }
    report_path = output.with_suffix(output.suffix + ".provenance.json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
