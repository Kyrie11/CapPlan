#!/usr/bin/env python
"""Normalize selected public GIS layers into CapPlan evidence schemas.

Profiles deliberately use conservative semantics. Parking meters/zones and taxi
stands are emitted as PUDO *candidates*, not as AV stopping legality truth,
unless an independent manual/official rule audit later confirms legality.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple


def read_rows(path: Path) -> Iterator[Dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix in {".json", ".geojson"}:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict) and payload.get("type") == "FeatureCollection":
            yield from payload.get("features") or []
        elif isinstance(payload, list):
            yield from payload
        elif isinstance(payload, dict):
            yield payload
    elif suffix in {".jsonl", ".ndjson", ".geojsonl"}:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    yield json.loads(line)
    elif suffix == ".csv":
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            yield from csv.DictReader(f)
    else:
        raise ValueError(f"unsupported input suffix: {suffix}")


def props(row: Dict[str, Any]) -> Dict[str, Any]:
    p = row.get("properties") if isinstance(row.get("properties"), dict) else {}
    return {**p, **{k: v for k, v in row.items() if k not in {"properties", "geometry", "type"}}}


def first(d: Dict[str, Any], *keys: str) -> Any:
    lower = {str(k).lower(): v for k, v in d.items()}
    for key in keys:
        value = lower.get(key.lower())
        if value not in (None, "", "unknown", "n/a"):
            return value
    return None


def number(value: Any) -> Optional[float]:
    if value in (None, "", "unknown", "n/a"):
        return None
    try:
        text = str(value).strip().lower().replace(",", "")
        for suffix in ("meters", "meter", "metres", "metre", "feet", "foot", "ft", "inches", "inch", "in", "m", "%"):
            if text.endswith(suffix):
                text = text[: -len(suffix)].strip()
                break
        return float(text)
    except Exception:
        return None


def ratio(value: Any) -> Optional[float]:
    x = number(value)
    if x is None:
        return None
    return x / 100.0 if abs(x) > 1.0 else x


def feet_to_m(value: Any) -> Optional[float]:
    x = number(value)
    return None if x is None else x * 0.3048


def inches_to_m(value: Any) -> Optional[float]:
    x = number(value)
    return None if x is None else x * 0.0254


def representative_point(row: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    geom = row.get("geometry") if isinstance(row.get("geometry"), dict) else None
    if geom:
        coords = geom.get("coordinates")
        points: List[Tuple[float, float]] = []
        def walk(v: Any) -> None:
            if isinstance(v, list) and len(v) >= 2 and all(isinstance(x, (int, float)) for x in v[:2]):
                points.append((float(v[0]), float(v[1])))
            elif isinstance(v, list):
                for item in v:
                    walk(item)
        walk(coords)
        if points:
            return sum(x for x, _ in points) / len(points), sum(y for _, y in points) / len(points)
    d = props(row)
    lon = first(d, "lon", "longitude", "x")
    lat = first(d, "lat", "latitude", "y")
    if lon is not None and lat is not None:
        return float(lon), float(lat)
    return None


def standard_feature(row: Dict[str, Any], updates: Dict[str, Any], *, source: str, authoritative: bool, tier: str) -> Dict[str, Any]:
    p = props(row)
    p.update({k: v for k, v in updates.items() if v is not None})
    p["source"] = source
    p["authoritative"] = authoritative
    p["evidence_tier"] = tier
    p.setdefault("confidence", 0.9 if authoritative else 0.65)
    return {"type": "Feature", "geometry": row.get("geometry"), "properties": p}


def normalize(profile: str, row: Dict[str, Any], index: int, source: str) -> Dict[str, Any]:
    d = props(row)
    authoritative = profile.startswith("boston_") or profile.startswith("government_")
    tier = "A_authoritative_city_gis" if authoritative else "B_candidate_layer"
    if profile == "boston_sidewalk":
        return standard_feature(
            row,
            {
                "feature_id": str(first(d, "SWK_ID", "OBJECTID") or f"boston_sidewalk_{index}"),
                "kind": "sidewalk",
                "width_m": feet_to_m(first(d, "SWK_WIDTH")),
                "sidewalk_width_m": feet_to_m(first(d, "SWK_WIDTH")),
                "slope": ratio(first(d, "SWK_SLOPE")),
                "surface": first(d, "MATERIAL"),
                "inspection_date": first(d, "INSP_DATE", "new_insp_d"),
            },
            source=source,
            authoritative=True,
            tier="A_authoritative_city_gis",
        )
    if profile == "boston_ramp":
        xy = representative_point(row)
        if not xy:
            raise ValueError("Boston ramp feature has no geometry")
        return {
            "id": str(first(d, "RAMP_ID", "OBJECTID", "ID2") or f"boston_ramp_{index}"),
            "lon": xy[0],
            "lat": xy[1],
            "frame": "wgs84",
            "kind": "curb_ramp",
            "curb_ramp": True,
            "curb_height_m": inches_to_m(first(d, "REVEAL")),
            "sidewalk_width_m": feet_to_m(first(d, "SWK_WIDTH")),
            "deployment_clearance_m": feet_to_m(first(d, "SWK_WIDTH")),
            "ramp_slope": ratio(first(d, "APRON_SL")),
            "landing_slope": ratio(first(d, "LANDING_SL")),
            "surface": first(d, "SWK_MATL", "MATL"),
            "condition": first(d, "COND"),
            "inspection_date": first(d, "INSP_DATE"),
            "source": source,
            "authoritative": True,
            "evidence_tier": "A_authoritative_city_gis",
            "confidence": 0.9,
            "unit_assumptions": {"SWK_WIDTH": "feet", "REVEAL": "inches", "APRON_SL/LANDING_SL": "percent_or_ratio"},
        }
    if profile in {"vegas_parking_zone", "pittsburgh_parking_meter", "lta_taxi_stand", "generic_pudo_candidate"}:
        xy = representative_point(row)
        if not xy:
            raise ValueError(f"{profile} feature has no geometry")
        rid = first(d, "OBJECTID", "UNITID", "METER_NO", "id", "TaxiCode", "TAXI_CODE") or f"{profile}_{index}"
        return {
            "regulation_id": str(rid),
            "lon": xy[0],
            "lat": xy[1],
            "frame": "wgs84",
            "legal_stop": False,
            "candidate_only": True,
            "requires_manual_legality_audit": True,
            "service_class": "taxi" if profile == "lta_taxi_stand" else "unknown",
            "side": first(d, "SIDE"),
            "hours": first(d, "HOURS"),
            "days": first(d, "DAYS"),
            "restrictions": first(d, "RESTRICTIONS", "DESCRIPTION", "LOCATION"),
            "source": source,
            "authoritative": authoritative,
            "evidence_tier": tier,
            "confidence": 0.7,
            "raw_properties": d,
        }
    if profile == "government_entrance":
        fid = str(first(d, "id", "OBJECTID", "EXIT_CODE", "STN_EXIT") or f"entrance_{index}")
        return standard_feature(row, {"entrance_id": fid, "kind": "entrance"}, source=source, authoritative=True, tier="A_government_entrance_layer")
    if profile == "generic_city_gis":
        return standard_feature(row, {"feature_id": str(first(d, "id", "OBJECTID") or f"feature_{index}")}, source=source, authoritative=False, tier="B_unmapped_city_gis")
    raise ValueError(f"unknown profile: {profile}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--profile", required=True, choices=[
        "boston_sidewalk", "boston_ramp", "vegas_parking_zone", "pittsburgh_parking_meter",
        "lta_taxi_stand", "generic_pudo_candidate", "government_entrance", "generic_city_gis",
    ])
    p.add_argument("--source", required=True)
    p.add_argument("--skip_invalid", action="store_true")
    args = p.parse_args()
    rows: List[Dict[str, Any]] = []
    errors: List[str] = []
    for i, row in enumerate(read_rows(Path(args.input))):
        try:
            rows.append(normalize(args.profile, row, i, args.source))
        except Exception as exc:
            if not args.skip_invalid:
                raise
            errors.append(f"row {i}: {type(exc).__name__}: {exc}")
    if not rows:
        raise RuntimeError("normalization produced zero records")
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    if args.profile in {"boston_sidewalk", "government_entrance", "generic_city_gis"}:
        payload = {"type": "FeatureCollection", "features": rows, "properties": {"profile": args.profile, "source": args.source, "record_count": len(rows)}}
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    else:
        with out.open("w", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    report = {"input": args.input, "output": str(out), "profile": args.profile, "records": len(rows), "skipped": len(errors), "errors": errors[:100]}
    out.with_suffix(out.suffix + ".report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
