#!/usr/bin/env python
"""Recover publication-safe PUDO audit evidence from already-downloaded sources.

This stage is deliberately semantic, not heuristic.  It only promotes facts when
an authoritative source explicitly states the required relation.  In particular,
Singapore LTA Passenger Pickup Bay features can supply static general-passenger
pickup/drop-off stopping legality because the source dataset itself defines those
features as designated pickup/drop-off areas.  Missing curb geometry, deployment
clearance, or intended-trip entrance facts remain missing.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List


def _canon(v: Any) -> str:
    return str(v or "").strip().lower()


def _flatten(row: Dict[str, Any]) -> Dict[str, Any]:
    props = row.get("properties") if isinstance(row.get("properties"), dict) else {}
    return {**props, **{k: v for k, v in row.items() if k not in {"properties", "geometry", "type"}}}


def _iter_jsonl(path: Path) -> Iterator[Dict[str, Any]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                obj = json.loads(line)
                if isinstance(obj, dict):
                    yield obj


def _candidate_to_lta_regulation(row: Dict[str, Any]) -> Dict[str, Any] | None:
    d = _flatten(row)
    source = str(d.get("source") or "")
    if "lta" not in _canon(source) or "passenger pickup bay" not in _canon(source):
        return None
    rid = str(d.get("regulation_id") or d.get("id") or d.get("feature_id") or "").strip()
    if not rid:
        return None
    lon, lat = d.get("lon"), d.get("lat")
    try:
        lon, lat = float(lon), float(lat)
    except (TypeError, ValueError):
        return None
    if not (-180 <= lon <= 180 and -90 <= lat <= 90):
        return None
    return {
        "regulation_id": rid,
        "lon": lon,
        "lat": lat,
        "frame": "wgs84",
        "legal_stop": True,
        "legal_basis": "Singapore LTA DataMall Passenger Pickup Bay: designated roadside area for vehicles to pick up or drop off passengers",
        "service_class": "general_passenger_loading",
        "source": source or "Singapore LTA DataMall Passenger Pickup Bay",
        "authoritative": True,
        "evidence_tier": "A_authoritative_passenger_loading_bay",
        "confidence": 0.95,
        "legal_linkage_method": "authoritative_source_relation",
        "candidate_only": False,
        "recovered_from": "normalized/candidates/singapore/passenger_pickup_bay.jsonl",
        "raw_candidate_regulation_id": rid,
    }


def _merge_regulations(existing: Iterable[Dict[str, Any]], recovered: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    # Existing reviewed/manual records win. Recovered records only replace an old
    # record with the same ID when that old record is explicitly candidate-only
    # or lacks an authoritative legality statement.
    out: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for i, row in enumerate(existing):
        d = _flatten(row)
        key = str(d.get("regulation_id") or d.get("id") or f"__existing_{i}")
        if key not in out:
            order.append(key)
        out[key] = row
    for i, row in enumerate(recovered):
        key = str(row.get("regulation_id") or f"__recovered_{i}")
        old = out.get(key)
        replace = old is None
        if old is not None:
            od = _flatten(old)
            old_authoritative = bool(od.get("authoritative") is True or _canon(od.get("evidence_tier")).startswith("a_"))
            old_complete = od.get("legal_stop") is not None and bool(str(od.get("legal_basis") or "").strip())
            old_reviewed = bool(od.get("audited") is True or od.get("reviewer_id") or od.get("auditor_id"))
            replace = (not old_reviewed) and (not old_authoritative or not old_complete or bool(od.get("candidate_only")))
        if replace:
            if key not in out:
                order.append(key)
            out[key] = row
    return [out[k] for k in order]


def _count_records(path: Path) -> int:
    if not path.exists():
        return 0
    if path.suffix.lower() == ".jsonl":
        return sum(1 for _ in _iter_jsonl(path))
    if path.suffix.lower() in {".json", ".geojson"}:
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return 0
        if isinstance(obj, dict) and isinstance(obj.get("features"), list):
            return len(obj["features"])
        if isinstance(obj, list):
            return len(obj)
        return int(bool(obj))
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--external_root", required=True)
    p.add_argument("--cities", default="boston,pittsburgh,vegas,singapore")
    p.add_argument("--report_json", default=None)
    args = p.parse_args()

    ext = Path(args.external_root)
    cities = [x.strip() for x in args.cities.replace("+", ",").split(",") if x.strip()]
    report: Dict[str, Any] = {
        "status": "PASS",
        "semantic_policy": "explicit_authoritative_source_relations_only; no nearest-neighbor promotion of missing physical facts",
        "cities": {},
    }

    if "singapore" in cities:
        candidate = ext / "normalized" / "candidates" / "singapore" / "passenger_pickup_bay.jsonl"
        target = ext / "normalized" / "curb_regulations" / "singapore.jsonl"
        recovered = [x for x in (_candidate_to_lta_regulation(r) for r in _iter_jsonl(candidate)) if x is not None]
        existing = list(_iter_jsonl(target))
        merged = _merge_regulations(existing, recovered)
        if recovered:
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_suffix(target.suffix + ".tmp")
            with tmp.open("w", encoding="utf-8") as f:
                for row in merged:
                    f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            tmp.replace(target)
        report["cities"]["singapore"] = {
            "lta_passenger_pickup_candidates": len(recovered),
            "regulations_before": len(existing),
            "regulations_after": len(merged),
            "regulation_output": str(target),
            "legality_recovered": bool(recovered),
            "note": "Only static general-passenger pickup/drop-off legality is recovered. Physical interface and intended entrance remain independent evidence requirements.",
        }

    for city in cities:
        city_report = report["cities"].setdefault(city, {})
        paths = {
            "curb_regulations": ext / "normalized" / "curb_regulations" / f"{city}.jsonl",
            "entrances": ext / "normalized" / "entrances" / f"{city}.geojson",
            "curb_inventory": ext / "normalized" / "curb_inventory" / f"{city}.jsonl",
        }
        city_report["record_inventory"] = {k: _count_records(v) for k, v in paths.items()}
        city_report["paths"] = {k: str(v) for k, v in paths.items()}

    rp = Path(args.report_json) if args.report_json else ext / "reports" / "pudo_audit_evidence_recovery.json"
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    print("PUDO_AUDIT_EVIDENCE_RECOVERY=PASS")


if __name__ == "__main__":
    main()
