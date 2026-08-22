#!/usr/bin/env python
"""Download selected LTA DataMall geospatial ZIPs from the official static-data page.

The LTA filenames are versioned by release month/year.  This downloader discovers
current ZIP hrefs from the official static-data catalogue instead of hard-coding a
specific release.  It validates the downloaded bytes as ZIP and writes provenance.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

CATALOGUE = "https://datamall.lta.gov.sg/content/datamall/en/static-data.html/1000"
DATASETS: Dict[str, str] = {
    "passenger_pickup_bay": "PassengerPickupBay",
    "taxi_stand": "TaxiStand",
    "footpath": "Footpath",
    "kerbline": "KerbLine",
    "train_station_exit": "TrainStationExit",
}


def _get(url: str, retries: int = 4) -> bytes:
    headers = {"User-Agent": "CapPlan-AbilityBench/1.0 (+research dataset builder)"}
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=90) as resp:
                return resp.read()
        except (urllib.error.URLError, urllib.error.HTTPError, ConnectionError, TimeoutError) as exc:
            last = exc
            if attempt + 1 < retries:
                time.sleep(min(2 ** attempt, 10))
    raise RuntimeError(f"failed to download {url}: {last}")


def _discover(html_text: str, dataset: str, catalogue_url: str) -> str:
    stem = DATASETS[dataset].lower()
    hrefs = re.findall(r'''href\s*=\s*["']([^"']+\.zip(?:\?[^"']*)?)["']''', html_text, flags=re.I)
    matches = []
    for href in hrefs:
        href = html.unescape(href)
        basename = Path(urllib.parse.urlparse(href).path).name.lower()
        compact = re.sub(r"[^a-z0-9]", "", basename)
        if stem.lower() in compact:
            matches.append(urllib.parse.urljoin(catalogue_url, href))
    matches = sorted(set(matches))
    if len(matches) != 1:
        raise RuntimeError(f"expected exactly one current LTA ZIP for {dataset}, found {matches}")
    return matches[0]


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dataset", choices=sorted(DATASETS), required=True)
    p.add_argument("--output_dir", required=True)
    p.add_argument("--force", action="store_true")
    p.add_argument("--catalogue", default=CATALOGUE)
    args = p.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / f"{args.dataset}.zip"
    provenance = output.with_suffix(output.suffix + ".provenance.json")

    if output.exists() and not args.force:
        if not zipfile.is_zipfile(output):
            raise RuntimeError(f"existing output is not a valid ZIP: {output}; use --force")
        report = {"status": "PASS", "output": str(output), "reused": True}
        print(json.dumps(report, indent=2, sort_keys=True))
        print("LTA_STATIC_DOWNLOAD=PASS")
        return

    catalogue_bytes = _get(args.catalogue)
    catalogue_text = catalogue_bytes.decode("utf-8", errors="replace")
    resolved_url = _discover(catalogue_text, args.dataset, args.catalogue)
    data = _get(resolved_url)
    part = output.with_suffix(output.suffix + ".part")
    part.write_bytes(data)
    if not zipfile.is_zipfile(part):
        part.unlink(missing_ok=True)
        raise RuntimeError(f"LTA response is not a valid ZIP: {resolved_url}")
    part.replace(output)
    prov = {
        "status": "PASS",
        "dataset": args.dataset,
        "catalogue_url": args.catalogue,
        "resolved_url": resolved_url,
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "bytes": output.stat().st_size,
        "sha256": _sha256(output.read_bytes()),
        "format": "ESRI Shapefile ZIP",
        "source": "Singapore Land Transport Authority DataMall",
    }
    provenance.write_text(json.dumps(prov, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(prov, ensure_ascii=False, indent=2, sort_keys=True))
    print("LTA_STATIC_DOWNLOAD=PASS")


if __name__ == "__main__":
    main()
