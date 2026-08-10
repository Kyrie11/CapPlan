#!/usr/bin/env python3
"""
Download all USGS The National Map GeoTIFF products intersecting one of three
predefined city bounding boxes, matching these TNM Downloader selections:

  Data -> Elevation Products (3D Elevation Program Products and Services)
      - 1-meter DEM
      - Seamless 1-meter DEM (Limited Availability)
  File Formats -> GeoTIFF

Usage:
    python3 usgs_tnm_dem_download.py Boston /data/boston
    python3 usgs_tnm_dem_download.py Pittsburgh /data/pittsburgh
    python3 usgs_tnm_dem_download.py LasVegas /data/lasvegas

Only Python 3 standard library is required.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlencode, urlparse
from urllib.request import Request, urlopen

API_URL = "https://tnmaccess.nationalmap.gov/api/v1/products"
USER_AGENT = "usgs-tnm-city-dem-downloader/1.0 (+https://apps.nationalmap.gov/downloader/)"
PAGE_SIZE = 50
API_TIMEOUT = 120
DOWNLOAD_TIMEOUT = 180
API_RETRIES = 6
DOWNLOAD_RETRIES = 8
DEFAULT_WORKERS = 3

# BBox order required by TNMAccess: minX,minY,maxX,maxY = west,south,east,north.
CITY_BBOX: Dict[str, Tuple[float, float, float, float]] = {
    "Boston": (-71.482076, 42.112050, -71.042559, 42.367739),
    "Pittsburgh": (-80.338189, 40.154309, -79.964829, 40.445395),
    "LasVegas": (-115.687529, 35.340672, -115.174103, 36.155420),
}

# These are the current TNM/ScienceBase dataset tags shown by The National Map.
DATASET_TAGS: Sequence[Tuple[str, str]] = (
    ("1-meter DEM", "Digital Elevation Model (DEM) 1 meter"),
    ("Seamless 1-meter DEM", "Seamless 1-m DEM (S1M)"),
)

_print_lock = threading.Lock()


def log(message: str = "") -> None:
    with _print_lock:
        print(message, flush=True)


def normalize_city(value: str) -> str:
    key = "".join(ch for ch in value.lower() if ch.isalnum())
    aliases = {
        "boston": "Boston",
        "pittsburgh": "Pittsburgh",
        "pittsburg": "Pittsburgh",
        "lasvegas": "LasVegas",
        "vegas": "LasVegas",
    }
    if key not in aliases:
        raise argparse.ArgumentTypeError(
            "城市名必须是 Boston、Pittsburgh 或 LasVegas"
        )
    return aliases[key]


def bbox_string(bbox: Sequence[float]) -> str:
    return ",".join(f"{x:.6f}" for x in bbox)


def _retry_sleep(attempt: int, retry_after: Optional[str] = None) -> None:
    if retry_after:
        try:
            delay = min(float(retry_after), 60.0)
        except ValueError:
            delay = 0.0
    else:
        delay = 0.0
    if delay <= 0:
        delay = min(2 ** attempt, 30)
    time.sleep(delay)


def get_json(url: str, params: Dict[str, Any]) -> Dict[str, Any]:
    full_url = f"{url}?{urlencode(params)}"
    last_error: Optional[BaseException] = None

    for attempt in range(API_RETRIES):
        req = Request(
            full_url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(req, timeout=API_TIMEOUT) as resp:
                raw = resp.read()
            data = json.loads(raw.decode("utf-8"))
            if not isinstance(data, dict):
                raise RuntimeError(f"TNMAccess returned unexpected JSON: {type(data)!r}")

            error_message = data.get("errorMessage")
            if error_message:
                raise RuntimeError(f"TNMAccess error: {error_message}")

            errors = data.get("errors")
            if errors and not data.get("items"):
                raise RuntimeError(f"TNMAccess errors: {errors}")
            return data

        except HTTPError as exc:
            last_error = exc
            if exc.code not in (408, 425, 429, 500, 502, 503, 504):
                raise
            if attempt + 1 < API_RETRIES:
                _retry_sleep(attempt, exc.headers.get("Retry-After"))
        except (URLError, TimeoutError, ConnectionError, json.JSONDecodeError, RuntimeError) as exc:
            last_error = exc
            if attempt + 1 < API_RETRIES:
                _retry_sleep(attempt)

    raise RuntimeError(f"TNMAccess request failed after {API_RETRIES} attempts: {last_error}")


def search_products_once(
    bbox: Sequence[float],
    dataset_label: str,
    dataset_tag: str,
) -> List[Dict[str, Any]]:
    """Search all pages for one dataset tag and one bbox."""
    found: List[Dict[str, Any]] = []
    seen_urls: set[str] = set()
    offset = 0
    previous_page_signature: Optional[Tuple[str, ...]] = None

    while True:
        params = {
            "datasets": dataset_tag,
            "bbox": bbox_string(bbox),
            "prodFormats": "GeoTIFF",
            "outputFormat": "JSON",
            "max": PAGE_SIZE,
            "offset": offset,
        }
        data = get_json(API_URL, params)
        items = data.get("items") or []
        if not isinstance(items, list):
            raise RuntimeError("TNMAccess JSON field 'items' is not a list")

        signature = tuple(
            str(item.get("downloadURL") or item.get("id") or "")
            for item in items
            if isinstance(item, dict)
        )
        if previous_page_signature is not None and signature == previous_page_signature and signature:
            raise RuntimeError("TNMAccess repeated the same page while paginating; aborting to avoid an infinite loop")
        previous_page_signature = signature

        for item in items:
            if not isinstance(item, dict):
                continue
            url = item.get("downloadURL")
            if not isinstance(url, str) or not url:
                continue
            path = urlparse(url).path.lower()
            if not (path.endswith(".tif") or path.endswith(".tiff")):
                # prodFormats=GeoTIFF should already constrain this, but keep the
                # downloader strict because the user asked for TIF files only.
                continue
            if url in seen_urls:
                continue
            seen_urls.add(url)
            copied = dict(item)
            copied["_datasetLabel"] = dataset_label
            copied["_datasetTag"] = dataset_tag
            found.append(copied)

        total_raw = data.get("total")
        try:
            total = int(total_raw)
        except (TypeError, ValueError):
            total = None

        if not items:
            break
        offset += len(items)
        if total is not None and offset >= total:
            break
        if len(items) < PAGE_SIZE and total is None:
            break

    return found


def split_bbox_4(bbox: Sequence[float]) -> List[Tuple[float, float, float, float]]:
    west, south, east, north = bbox
    midx = (west + east) / 2.0
    midy = (south + north) / 2.0
    return [
        (west, south, midx, midy),
        (midx, south, east, midy),
        (west, midy, midx, north),
        (midx, midy, east, north),
    ]


def search_products(
    bbox: Sequence[float],
    dataset_label: str,
    dataset_tag: str,
) -> List[Dict[str, Any]]:
    """
    Search a dataset. If the API repeatedly errors on the full bbox, retry as
    four quadrants and union the results. This preserves the same geometric
    query semantics while avoiding occasional large-query timeouts.
    """
    try:
        return search_products_once(bbox, dataset_label, dataset_tag)
    except Exception as exc:
        log(f"  Full-bbox query failed ({exc}). Retrying as 4 smaller boxes...")
        merged: Dict[str, Dict[str, Any]] = {}
        last_error: Optional[BaseException] = None
        success_count = 0
        for sub_bbox in split_bbox_4(bbox):
            try:
                for item in search_products_once(sub_bbox, dataset_label, dataset_tag):
                    merged[str(item["downloadURL"])] = item
                success_count += 1
            except Exception as sub_exc:
                last_error = sub_exc
                log(f"    Sub-bbox {bbox_string(sub_bbox)} failed: {sub_exc}")
        if success_count == 0:
            raise RuntimeError(
                f"TNMAccess failed for the full bbox and all fallback sub-bboxes. Last error: {last_error}"
            ) from exc
        if success_count < 4:
            raise RuntimeError(
                f"Only {success_count}/4 fallback sub-bbox queries succeeded; refusing to silently return an incomplete file list. Last error: {last_error}"
            ) from exc
        return list(merged.values())


def expected_size(item: Dict[str, Any]) -> Optional[int]:
    value = item.get("sizeInBytes")
    if value is None:
        return None
    try:
        size = int(float(value))
    except (TypeError, ValueError):
        return None
    return size if size > 0 else None


def human_bytes(value: Optional[int]) -> str:
    if value is None:
        return "unknown"
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    x = float(value)
    for unit in units:
        if x < 1024 or unit == units[-1]:
            return f"{x:.1f} {unit}"
        x /= 1024
    return f"{value} B"


def basename_from_url(url: str) -> str:
    name = unquote(Path(urlparse(url).path).name)
    if not name:
        name = hashlib.sha1(url.encode("utf-8")).hexdigest() + ".tif"
    # Avoid any platform/path oddities and keep only a filename.
    name = name.replace("/", "_").replace("\\", "_")
    return name


def assign_unique_filenames(items: List[Dict[str, Any]]) -> None:
    used: Dict[str, str] = {}
    for item in items:
        url = str(item["downloadURL"])
        name = basename_from_url(url)
        if name in used and used[name] != url:
            p = Path(name)
            digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:10]
            name = f"{p.stem}_{digest}{p.suffix or '.tif'}"
        used[name] = url
        item["_filename"] = name


def _open_download(url: str, start: int = 0):
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    if start > 0:
        headers["Range"] = f"bytes={start}-"
    req = Request(url, headers=headers)
    return urlopen(req, timeout=DOWNLOAD_TIMEOUT)


def download_one(item: Dict[str, Any], out_dir: Path, force: bool = False) -> Tuple[str, str, int]:
    url = str(item["downloadURL"])
    name = str(item["_filename"])
    target = out_dir / name
    part = out_dir / f"{name}.part"
    expected = expected_size(item)

    if force:
        target.unlink(missing_ok=True)
        part.unlink(missing_ok=True)

    if target.exists():
        actual = target.stat().st_size
        if expected is None or actual == expected:
            return (name, "skipped", actual)
        # A prior run may have left a truncated file under the final name.
        if not part.exists():
            target.replace(part)
        else:
            target.unlink()

    last_error: Optional[BaseException] = None

    for attempt in range(DOWNLOAD_RETRIES):
        start = part.stat().st_size if part.exists() else 0
        try:
            try:
                resp = _open_download(url, start=start)
            except HTTPError as exc:
                if exc.code == 416 and part.exists():
                    # Requested range is beyond EOF. If expected size agrees,
                    # the partial file is actually complete.
                    actual = part.stat().st_size
                    if expected is None or actual == expected:
                        part.replace(target)
                        return (name, "downloaded", actual)
                raise

            with resp:
                status = getattr(resp, "status", resp.getcode())
                if start > 0 and status == 206:
                    mode = "ab"
                else:
                    # Server ignored Range or this is a fresh download.
                    mode = "wb"
                    start = 0

                with open(part, mode) as fh:
                    while True:
                        chunk = resp.read(1024 * 1024)
                        if not chunk:
                            break
                        fh.write(chunk)

            actual = part.stat().st_size
            if expected is not None and actual != expected:
                raise IOError(
                    f"size mismatch after transfer: got {actual} bytes, expected {expected}"
                )
            part.replace(target)
            return (name, "downloaded", actual)

        except HTTPError as exc:
            last_error = exc
            if exc.code not in (408, 425, 429, 500, 502, 503, 504):
                break
            if attempt + 1 < DOWNLOAD_RETRIES:
                _retry_sleep(attempt, exc.headers.get("Retry-After"))
        except (URLError, TimeoutError, ConnectionError, OSError) as exc:
            last_error = exc
            if attempt + 1 < DOWNLOAD_RETRIES:
                _retry_sleep(attempt)

    raise RuntimeError(f"failed to download {url}: {last_error}")


def write_manifest(city: str, bbox: Sequence[float], items: List[Dict[str, Any]], out_dir: Path) -> None:
    manifest_items = []
    urls = []
    for item in items:
        url = str(item["downloadURL"])
        urls.append(url)
        manifest_items.append(
            {
                "dataset": item.get("_datasetLabel"),
                "datasetTag": item.get("_datasetTag"),
                "title": item.get("title"),
                "downloadURL": url,
                "filename": item.get("_filename"),
                "sizeInBytes": expected_size(item),
                "sourceId": item.get("sourceId") or item.get("id"),
            }
        )

    manifest = {
        "city": city,
        "bbox": {
            "x_min_longitude": bbox[0],
            "y_min_latitude": bbox[1],
            "x_max_longitude": bbox[2],
            "y_max_latitude": bbox[3],
        },
        "format": "GeoTIFF",
        "datasets": [tag for _, tag in DATASET_TAGS],
        "fileCount": len(manifest_items),
        "items": manifest_items,
    }
    (out_dir / f"tnm_manifest_{city}.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (out_dir / f"tnm_urls_{city}.txt").write_text(
        "\n".join(urls) + ("\n" if urls else ""),
        encoding="utf-8",
    )


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "按预设 Boston/Pittsburgh/LasVegas bbox 查询 USGS TNM 的 1-meter DEM 与 "
            "Seamless 1-meter DEM (S1M) GeoTIFF，并下载全部匹配的 TIF。"
        )
    )
    parser.add_argument("city", type=normalize_city, help="Boston | Pittsburgh | LasVegas")
    parser.add_argument("path", help="下载目录")
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"并行下载数，默认 {DEFAULT_WORKERS}",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只查询并生成 URL/manifest，不下载文件",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="强制重新下载已存在的 TIF",
    )
    args = parser.parse_args(argv)
    if args.workers < 1 or args.workers > 16:
        parser.error("--workers 必须在 1 到 16 之间")
    return args


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    city: str = args.city
    bbox = CITY_BBOX[city]
    out_dir = Path(args.path).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    log(f"City: {city}")
    log(
        "BBox (minX,minY,maxX,maxY): "
        f"{bbox_string(bbox)}  [west,south,east,north]"
    )
    log(f"Output: {out_dir}")
    log("Format: GeoTIFF")
    log("")

    all_items_by_url: Dict[str, Dict[str, Any]] = {}
    counts: Dict[str, int] = {}

    for dataset_label, dataset_tag in DATASET_TAGS:
        log(f"Searching: {dataset_label}  ({dataset_tag})")
        items = search_products(bbox, dataset_label, dataset_tag)
        counts[dataset_label] = len(items)
        log(f"  Found {len(items)} TIF product(s).")
        for item in items:
            all_items_by_url[str(item["downloadURL"])] = item

    items = list(all_items_by_url.values())
    items.sort(key=lambda x: (str(x.get("_datasetLabel", "")), str(x.get("title", "")), str(x.get("downloadURL", ""))))
    assign_unique_filenames(items)
    write_manifest(city, bbox, items, out_dir)

    log("")
    log("Search summary:")
    for label, _ in DATASET_TAGS:
        log(f"  {label}: {counts.get(label, 0)}")
    log(f"  Unique TIF URLs: {len(items)}")

    known_total = sum(size for size in (expected_size(x) for x in items) if size is not None)
    known_count = sum(1 for x in items if expected_size(x) is not None)
    if known_count:
        log(f"  Known total size ({known_count}/{len(items)} files): {human_bytes(known_total)}")

    log(f"  Manifest: {out_dir / f'tnm_manifest_{city}.json'}")
    log(f"  URL list: {out_dir / f'tnm_urls_{city}.txt'}")

    if not items:
        log("")
        log("No matching GeoTIFF products were returned. Nothing to download.")
        if counts.get("1-meter DEM", 0) == 0:
            log(
                "Note: if you expect 1-meter coverage here, this can also indicate a temporary "
                "TNMAccess/ScienceBase inventory outage; rerun the command later."
            )
        return 0

    if args.dry_run:
        log("")
        log("--dry-run specified; query finished without downloading files.")
        return 0

    log("")
    log(f"Downloading with {args.workers} worker(s)...")

    downloaded = 0
    skipped = 0
    failed: List[Tuple[str, str]] = []

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        future_to_item = {
            pool.submit(download_one, item, out_dir, args.force): item for item in items
        }
        try:
            for future in concurrent.futures.as_completed(future_to_item):
                item = future_to_item[future]
                name = str(item.get("_filename"))
                try:
                    filename, status, size = future.result()
                    if status == "skipped":
                        skipped += 1
                        log(f"[SKIP] {filename} ({human_bytes(size)})")
                    else:
                        downloaded += 1
                        log(f"[ OK ] {filename} ({human_bytes(size)})")
                except Exception as exc:
                    failed.append((name, str(exc)))
                    log(f"[FAIL] {name}: {exc}")
        except KeyboardInterrupt:
            log("\nInterrupted; partial downloads are kept as *.part and will resume next run.")
            for f in future_to_item:
                f.cancel()
            return 130

    log("")
    log(f"Done. Downloaded: {downloaded}, skipped: {skipped}, failed: {len(failed)}")

    if failed:
        fail_log = out_dir / f"tnm_failed_{city}.txt"
        fail_log.write_text(
            "\n".join(f"{name}\t{err}" for name, err in failed) + "\n",
            encoding="utf-8",
        )
        log(f"Failed list: {fail_log}")
        log("Rerun the same command to resume/retry failed or partial files.")
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
