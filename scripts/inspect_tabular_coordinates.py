#!/usr/bin/env python
"""Inspect a CSV/JSON tabular public source for usable WGS84 coordinates.

Designed for sources such as WPRDC Payment Points where the file is valid CSV
but field names may vary in capitalization/punctuation. The script does not
normalize or infer projected coordinates; it only validates explicit latitude /
longitude-style fields and prints a machine-readable PASS/FAIL token.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import re
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Tuple


def canon(v: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(v).replace("\ufeff", "").strip().lower()).strip("_")


def first(row: Dict[str, Any], names: Iterable[str]) -> Any:
    d = {canon(k): v for k, v in row.items()}
    for n in names:
        v = d.get(canon(n))
        if v not in (None, "", "NULL", "null", "N/A", "n/a"):
            return v
    return None


def valid_xy(row: Dict[str, Any]) -> Tuple[bool, Any, Any]:
    lon = first(row, ["longitude", "lon", "lng", "long", "long_dd", "longitude_dd", "point_x"])
    lat = first(row, ["latitude", "lat", "lat_dd", "latitude_dd", "point_y"])
    try:
        x, y = float(str(lon).strip()), float(str(lat).strip())
    except (TypeError, ValueError):
        return False, lon, lat
    ok = math.isfinite(x) and math.isfinite(y) and -180 <= x <= 180 and -90 <= y <= 90
    return ok, x, y


def rows(path: Path) -> Iterator[Dict[str, Any]]:
    if path.suffix.lower() == ".csv":
        head = path.read_bytes()[:512].lstrip().lower()
        if head.startswith((b"<html", b"<!doctype html")):
            raise RuntimeError("input is HTML, not CSV")
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            r = csv.DictReader(f)
            if not r.fieldnames:
                raise RuntimeError("CSV has no header")
            yield from r
        return
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        yield from payload
    elif isinstance(payload, dict) and payload.get("type") == "FeatureCollection":
        for f in payload.get("features") or []:
            p = f.get("properties") or {}
            yield p
    elif isinstance(payload, dict):
        yield payload
    else:
        raise RuntimeError("unsupported JSON structure")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", required=True)
    p.add_argument("--min_valid_fraction", type=float, default=0.95)
    p.add_argument("--min_rows", type=int, default=10)
    p.add_argument("--min_valid_rows", type=int, default=0,
                   help="Optional absolute minimum usable coordinate rows. Useful when a public table legitimately contains non-spatial/retired rows.")
    args = p.parse_args()
    path = Path(args.input)
    if not path.exists():
        raise FileNotFoundError(path)
    total = valid = 0
    fields = []
    bad_examples = []
    for row in rows(path):
        total += 1
        if not fields:
            fields = list(row.keys())
        ok, lon, lat = valid_xy(row)
        if ok:
            valid += 1
        elif len(bad_examples) < 5:
            bad_examples.append({"lon": lon, "lat": lat, "row": {str(k): v for k, v in list(row.items())[:20]}})
    frac = valid / total if total else 0.0
    status = "PASS" if (
        total >= args.min_rows
        and valid >= args.min_valid_rows
        and frac >= args.min_valid_fraction
    ) else "FAIL"
    report = {
        "status": status,
        "input": str(path),
        "rows": total,
        "valid_wgs84_rows": valid,
        "valid_fraction": frac,
        "min_valid_fraction": args.min_valid_fraction,
        "min_valid_rows": args.min_valid_rows,
        "fields": fields,
        "bad_examples": bad_examples,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    print(f"TABULAR_COORDINATE_CHECK={status}")
    if status != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
