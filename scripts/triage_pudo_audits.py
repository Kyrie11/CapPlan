#!/usr/bin/env python
"""Machine-triage PUDO audit rows without inventing publication truth.

The triage stage is deliberately asymmetric:

* deterministic checks may reject an invalid/ambiguous source match;
* deterministic checks may identify rows that need new evidence;
* source-complete rows may be routed to human/visual review;
* automatic acceptance is allowed only when the input already carries an
  explicit authoritative semantic linkage for BOTH stopping legality and the
  intended service entrance.  A nearest spatial match is never promoted merely
  because its distance is small.

This script therefore reduces human workload without laundering geometry or
thresholds into manual-audit ground truth.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

PAPER_REQUIRED = [
    "curb_height_m", "sidewalk_width_m", "deployment_clearance_m", "curb_ramp",
    "legal_stop", "legal_basis", "entrance_id", "entrance_lon", "entrance_lat",
]
PHYSICAL_FIELDS = ["curb_height_m", "sidewalk_width_m", "deployment_clearance_m", "curb_ramp"]
EXPLICIT_LINKAGE_VALUES = {
    "explicit_source_relation", "authoritative_source_relation", "authoritative_service_relation",
    "authoritative_service_entrance_relation", "reviewed_service_relation", "reviewed_trip_entrance",
    "explicit_segment_relation", "explicit_curb_segment_relation", "direct_feature_relation",
}


def _blank(v: Any) -> bool:
    return v is None or str(v).strip() == ""


def _f(v: Any) -> Optional[float]:
    if _blank(v):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _b(v: Any) -> Optional[bool]:
    if isinstance(v, bool):
        return v
    s = str(v or "").strip().lower()
    if s in {"1", "true", "yes", "y"}:
        return True
    if s in {"0", "false", "no", "n"}:
        return False
    return None


def _split(v: Any) -> set[str]:
    return {x.strip() for x in str(v or "").split(";") if x.strip()}


def _is_authoritative_tier(v: Any) -> bool:
    return str(v or "").strip().lower().startswith("a_")


def _explicit_linkage(row: Dict[str, Any], prefix: str) -> bool:
    # A previous human approval is explicit evidence too, but is not fabricated
    # here.  Otherwise require a source-provided semantic relation field.
    if prefix == "entrance" and _b(row.get("entrance_linkage_approved")) is True:
        return True
    if prefix == "legal" and _b(row.get("legality_linkage_approved")) is True:
        return True
    keys = (
        f"{prefix}_linkage_method", f"{prefix}_relation_method",
        f"{prefix}_match_method", f"{prefix}_association_method",
    )
    for key in keys:
        if str(row.get(key) or "").strip().lower() in EXPLICIT_LINKAGE_VALUES:
            return True
    return False


def _source_complete(row: Dict[str, Any]) -> Tuple[bool, List[str]]:
    reasons: List[str] = []
    missing = [f for f in PAPER_REQUIRED if _blank(row.get(f))]
    if missing:
        reasons.append("missing_required:" + ",".join(missing))
    for f in PHYSICAL_FIELDS:
        if _blank(row.get(f"{f}_source")):
            reasons.append(f"missing_provenance:{f}")
    if _blank(row.get("legal_stop_source")):
        reasons.append("missing_provenance:legal_stop")
    if _blank(row.get("entrance_source")):
        reasons.append("missing_provenance:entrance")
    return not reasons, reasons


def _validate_ranges(row: Dict[str, Any]) -> List[str]:
    reasons: List[str] = []
    lon = _f(row.get("lon")); lat = _f(row.get("lat"))
    if lon is None or lat is None or not (-180 <= lon <= 180 and -90 <= lat <= 90):
        reasons.append("invalid_pudo_lonlat")
    for lon_key, lat_key, label in [
        ("entrance_lon", "entrance_lat", "entrance"),
        ("entrance_candidate_lon", "entrance_candidate_lat", "entrance_candidate"),
    ]:
        if not _blank(row.get(lon_key)) or not _blank(row.get(lat_key)):
            x = _f(row.get(lon_key)); y = _f(row.get(lat_key))
            if x is None or y is None or not (-180 <= x <= 180 and -90 <= y <= 90):
                reasons.append(f"invalid_{label}_lonlat")

    numeric_ranges = {
        "curb_height_m": (0.0, 0.50),
        "sidewalk_width_m": (0.30, 20.0),
        "deployment_clearance_m": (0.30, 20.0),
        "running_slope": (-1.0, 1.0),
        "cross_slope": (-1.0, 1.0),
    }
    for key, (lo, hi) in numeric_ranges.items():
        if _blank(row.get(key)):
            continue
        value = _f(row.get(key))
        if value is None:
            reasons.append(f"non_numeric:{key}")
        elif not (lo <= value <= hi):
            reasons.append(f"implausible_range:{key}={value:g}")
    if not _blank(row.get("curb_ramp")) and _b(row.get("curb_ramp")) is None:
        reasons.append("invalid_boolean:curb_ramp")
    if not _blank(row.get("legal_stop")) and _b(row.get("legal_stop")) is None:
        reasons.append("invalid_boolean:legal_stop")
    return reasons


def _distance_gate(row: Dict[str, Any], physical_m: float, legal_m: float, entrance_m: float) -> List[str]:
    reasons: List[str] = []
    for f in PHYSICAL_FIELDS:
        d = _f(row.get(f"{f}_match_distance_m"))
        if d is not None and d > physical_m:
            reasons.append(f"physical_match_too_far:{f}={d:.3f}m>{physical_m:g}m")
    d = _f(row.get("legal_stop_match_distance_m"))
    if d is not None and d > legal_m:
        reasons.append(f"legality_match_too_far:{d:.3f}m>{legal_m:g}m")
    # This is only a routing threshold for review, not an acceptance threshold.
    d = _f(row.get("entrance_match_distance_m"))
    if d is None:
        d = _f(row.get("entrance_candidate_match_distance_m"))
    if d is not None and d > entrance_m:
        reasons.append(f"entrance_candidate_too_far:{d:.3f}m>{entrance_m:g}m")
    return reasons


def _authoritative_core(row: Dict[str, Any]) -> bool:
    tiers = [row.get(f"{f}_evidence_tier") for f in PHYSICAL_FIELDS]
    return all(_is_authoritative_tier(t) for t in tiers) and _is_authoritative_tier(row.get("legal_stop_evidence_tier")) and _is_authoritative_tier(row.get("entrance_evidence_tier"))


def classify_row(row: Dict[str, Any], *, physical_match_m: float = 15.0,
                 regulation_match_m: float = 12.0, entrance_candidate_m: float = 80.0) -> Tuple[str, List[str]]:
    invalid = _validate_ranges(row)
    invalid += _distance_gate(row, physical_match_m, regulation_match_m, entrance_candidate_m)
    if invalid:
        return "MACHINE_REJECT_INVALID_OR_AMBIGUOUS", invalid

    complete, completeness_reasons = _source_complete(row)
    if not complete:
        # If all missing fields are only the entrance truth but a candidate
        # exists, route to semantic visual review rather than pretending a new
        # measurement is required.
        missing_required = [r for r in completeness_reasons if r.startswith("missing_required:")]
        only_entrance_missing = bool(missing_required) and all(
            set(r.split(":", 1)[1].split(",")) <= {"entrance_id", "entrance_lon", "entrance_lat"}
            for r in missing_required
        )
        candidate_ready = all(not _blank(row.get(k)) for k in [
            "entrance_candidate_id", "entrance_candidate_lon", "entrance_candidate_lat", "entrance_candidate_source"
        ])
        non_missing_provenance = [r for r in completeness_reasons if not r.startswith("missing_required:")]
        non_entrance_prov = [r for r in non_missing_provenance if r != "missing_provenance:entrance"]
        if only_entrance_missing and candidate_ready and not non_entrance_prov:
            return "VISUAL_REVIEW_REQUIRED", ["nearest_or_candidate_entrance_requires_semantic_confirmation"]
        return "NEW_EVIDENCE_REQUIRED", completeness_reasons

    auto = _split(row.get("auto_filled_fields"))
    nearest_entrance = bool({"entrance_id", "entrance_lon", "entrance_lat"} & auto) or (
        _f(row.get("entrance_candidate_match_distance_m")) is not None and not _explicit_linkage(row, "entrance")
    )
    if nearest_entrance or not _explicit_linkage(row, "entrance"):
        return "VISUAL_REVIEW_REQUIRED", ["entrance_semantic_linkage_not_explicit"]
    if not _explicit_linkage(row, "legal"):
        return "VISUAL_REVIEW_REQUIRED", ["stopping_legality_segment_linkage_not_explicit"]
    if not _authoritative_core(row):
        return "VISUAL_REVIEW_REQUIRED", ["publication_critical_evidence_not_all_tier_A"]

    # This means the source itself already provides explicit semantic relations;
    # no human identity or measurement is invented.  It is not equivalent to a
    # manual field audit and should be imported as authoritative source evidence.
    return "MACHINE_PASS_EXPLICIT_AUTHORITATIVE_SOURCE", []


def _write(path_text: str | None, fields: List[str], rows: Iterable[Dict[str, Any]]) -> None:
    if not path_text:
        return
    path = Path(path_text); path.parent.mkdir(parents=True, exist_ok=True)
    rows = list(rows)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input_csv", required=True)
    p.add_argument("--output_csv", required=True)
    p.add_argument("--machine_pass_csv", default=None)
    p.add_argument("--machine_reject_csv", default=None)
    p.add_argument("--visual_review_csv", default=None)
    p.add_argument("--new_evidence_csv", default=None)
    p.add_argument("--report_json", default=None)
    p.add_argument("--physical_match_m", type=float, default=15.0)
    p.add_argument("--regulation_match_m", type=float, default=12.0)
    p.add_argument("--entrance_candidate_m", type=float, default=80.0)
    args = p.parse_args()

    inp = Path(args.input_csv)
    with inp.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f); fields = list(reader.fieldnames or []); rows = [dict(r) for r in reader]
    if not fields:
        raise RuntimeError("input CSV has no header")

    extra = ["machine_triage_decision", "machine_triage_reasons"]
    out_fields = fields + [f for f in extra if f not in fields]
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    reason_counts: Counter[str] = Counter()
    for row in rows:
        decision, reasons = classify_row(
            row,
            physical_match_m=args.physical_match_m,
            regulation_match_m=args.regulation_match_m,
            entrance_candidate_m=args.entrance_candidate_m,
        )
        row["machine_triage_decision"] = decision
        row["machine_triage_reasons"] = ";".join(reasons)
        buckets.setdefault(decision, []).append(row)
        reason_counts.update(reasons)

    _write(args.output_csv, out_fields, rows)
    _write(args.machine_pass_csv, out_fields, buckets.get("MACHINE_PASS_EXPLICIT_AUTHORITATIVE_SOURCE", []))
    _write(args.machine_reject_csv, out_fields, buckets.get("MACHINE_REJECT_INVALID_OR_AMBIGUOUS", []))
    _write(args.visual_review_csv, out_fields, buckets.get("VISUAL_REVIEW_REQUIRED", []))
    _write(args.new_evidence_csv, out_fields, buckets.get("NEW_EVIDENCE_REQUIRED", []))

    report = {
        "status": "PASS",
        "rows": len(rows),
        "decision_counts": {k: len(v) for k, v in sorted(buckets.items())},
        "reason_counts": dict(reason_counts.most_common()),
        "thresholds": {
            "physical_match_m": args.physical_match_m,
            "regulation_match_m": args.regulation_match_m,
            "entrance_candidate_m": args.entrance_candidate_m,
        },
        "semantics": {
            "machine_reject": "deterministic invalidity or source-association distance outlier; does not prove real-world infeasibility",
            "new_evidence_required": "publication-critical facts/provenance are missing",
            "visual_review_required": "facts may be present but a spatial association still needs human semantic confirmation",
            "machine_pass": "only explicit authoritative source relations; never created from nearest-distance thresholds",
        },
        "warning": "This stage never writes auditor_id/observed_at and never converts a nearest entrance or regulation match into manual ground truth.",
    }
    if args.report_json:
        out = Path(args.report_json); out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    print("PUDO_AUDIT_MACHINE_TRIAGE=PASS")


if __name__ == "__main__":
    main()
