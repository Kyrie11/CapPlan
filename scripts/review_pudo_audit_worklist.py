#!/usr/bin/env python
"""Classify or explicitly approve source-complete PUDO audit rows.

Default mode is *classification only*: it identifies rows whose publication
fields are already backed by explicit source provenance, but it never stamps a
human reviewer/auditor identity and never writes them to the accepted import
file.  This prevents an automated spatial join from being laundered into a
manual audit.

After a reviewer has actually inspected ``review_candidates_csv``, rerun with
``--approve_source_complete --reviewer_id ...``.  Only then are source-complete
rows stamped for import.  Rows still missing physical dimensions, stopping
legality, entrance truth, or provenance remain unresolved and are never guessed.
"""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

PAPER_REQUIRED = [
    "curb_height_m", "sidewalk_width_m", "deployment_clearance_m", "curb_ramp",
    "legal_stop", "legal_basis", "entrance_id", "entrance_lon", "entrance_lat",
]
ENTRANCE_FIELDS = ["entrance_id", "entrance_lon", "entrance_lat"]
ENTRANCE_CANDIDATE_FIELDS = ["entrance_candidate_id", "entrance_candidate_lon", "entrance_candidate_lat", "entrance_candidate_source"]
PHYSICAL_PROVENANCE = ["curb_height_m", "sidewalk_width_m", "deployment_clearance_m", "curb_ramp"]


def _blank(v: Any) -> bool:
    return v is None or str(v).strip() == ""


def _split(v: Any) -> set[str]:
    return {x.strip() for x in str(v or "").split(";") if x.strip()}


def _as_bool(v: Any) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "y", "accept", "approved"}


def _candidate_linkage_ready(row: Dict[str, Any]) -> bool:
    return all(not _blank(row.get(f)) for f in ENTRANCE_CANDIDATE_FIELDS)


def _promote_reviewed_entrance_candidate(row: Dict[str, Any]) -> None:
    row["entrance_id"] = row.get("entrance_candidate_id")
    row["entrance_lon"] = row.get("entrance_candidate_lon")
    row["entrance_lat"] = row.get("entrance_candidate_lat")
    row["entrance_source"] = row.get("entrance_candidate_source")
    row["entrance_evidence_tier"] = row.get("entrance_candidate_evidence_tier")
    row["entrance_match_distance_m"] = row.get("entrance_candidate_match_distance_m")
    row["entrance_evidence_as_of"] = row.get("entrance_candidate_evidence_as_of")
    auto = _split(row.get("auto_filled_fields"))
    auto.update(ENTRANCE_FIELDS)
    row["auto_filled_fields"] = ";".join(sorted(auto))


def _source_complete(row: Dict[str, Any]) -> tuple[bool, List[str]]:
    reasons: List[str] = []
    non_entrance_required = [f for f in PAPER_REQUIRED if f not in ENTRANCE_FIELDS]
    missing = [f for f in non_entrance_required if _blank(row.get(f))]
    if missing:
        reasons.append("missing_required:" + ",".join(missing))

    truth_entrance_complete = all(not _blank(row.get(f)) for f in ENTRANCE_FIELDS)
    if not truth_entrance_complete:
        if _candidate_linkage_ready(row):
            reasons.append("entrance_candidate_requires_explicit_acceptance")
        else:
            missing_ent = [f for f in ENTRANCE_FIELDS if _blank(row.get(f))]
            reasons.append("missing_required:" + ",".join(missing_ent))

    # Every publication-critical fact that can be auto-prefilled must retain
    # field-level lineage; non-null values alone are insufficient.
    missing_src = [f for f in PHYSICAL_PROVENANCE if _blank(row.get(f"{f}_source"))]
    if missing_src:
        reasons.append("missing_physical_field_provenance:" + ",".join(missing_src))
    if _blank(row.get("legal_stop_source")):
        reasons.append("missing_legality_provenance")
    entrance_src = row.get("entrance_source") or row.get("entrance_candidate_source")
    if _blank(entrance_src):
        reasons.append("missing_entrance_provenance")

    # If truth fields were auto-populated by the optional prefill flag, the
    # semantic nearest-entrance relation still requires explicit human review.
    auto = _split(row.get("auto_filled_fields"))
    if truth_entrance_complete and any(f in auto for f in ENTRANCE_FIELDS) and not _as_bool(row.get("entrance_linkage_approved")):
        reasons.append("entrance_candidate_requires_explicit_acceptance")
    return not reasons, reasons


def _write_subset(path_text: str | None, fields: List[str], subset: List[Dict[str, Any]]) -> None:
    if not path_text:
        return
    path = Path(path_text)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader(); w.writerows(subset)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input_csv", required=True)
    p.add_argument("--output_csv", required=True, help="All rows with review-status annotations.")
    p.add_argument("--review_candidates_csv", default=None, help="Source-complete rows awaiting actual reviewer confirmation.")
    p.add_argument("--accepted_csv", default=None, help="Approved-only CSV, consumable by build_manual_audit_layers.py. Empty in classification-only mode.")
    p.add_argument("--unresolved_csv", default=None, help="Rows that still require new evidence/manual audit.")
    p.add_argument("--approve_source_complete", action="store_true", help="Assert that a human reviewer actually inspected the source-complete candidates and approves them for reviewed-audit import.")
    p.add_argument("--reviewer_id", default=None, help="Required with --approve_source_complete. Prefer a stable pseudonymous project ID.")
    p.add_argument("--reviewed_at", default=None, help="Offset-aware ISO review timestamp; default current UTC when approval is enabled.")
    p.add_argument("--accept_nearest_entrance_linkage", action="store_true", help="Allow a row with review_accept=true to promote its authoritative nearest-entrance candidate. Prefer the per-row entrance_linkage_approved=true column for selective review.")
    p.add_argument("--fail_on_manual_required", action="store_true", help="Exit non-zero if any row still needs new evidence/manual audit.")
    p.add_argument("--report_json", default=None)
    args = p.parse_args()

    reviewed_at = None
    reviewer_id = str(args.reviewer_id or "").strip()
    if args.approve_source_complete:
        if not reviewer_id:
            raise RuntimeError("--reviewer_id is required with --approve_source_complete")
        reviewed_at = args.reviewed_at or datetime.now(timezone.utc).isoformat()
        try:
            parsed = datetime.fromisoformat(reviewed_at.replace("Z", "+00:00"))
        except ValueError as exc:
            raise RuntimeError("--reviewed_at must be ISO-8601/RFC3339") from exc
        if parsed.tzinfo is None:
            raise RuntimeError("--reviewed_at must include timezone/UTC offset")
    elif args.reviewer_id or args.reviewed_at:
        raise RuntimeError("--reviewer_id/--reviewed_at only make sense with --approve_source_complete")

    inp = Path(args.input_csv)
    with inp.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fields = list(reader.fieldnames or [])
        rows = [dict(r) for r in reader]
    if not rows:
        raise RuntimeError("input worklist is empty")

    extra = [
        "entrance_id", "entrance_lon", "entrance_lat", "entrance_source", "entrance_evidence_tier",
        "entrance_match_distance_m", "entrance_evidence_as_of", "auditor_id", "observed_at",
        "review_accept", "entrance_linkage_approved", "review_decision", "review_reasons",
        "review_method", "reviewed_at", "observation_semantics",
    ]
    out_fields = fields + [x for x in extra if x not in fields]
    accepted = 0
    candidates = 0
    manual = 0
    nearest_blocked = 0
    output: List[Dict[str, Any]] = []
    for row in rows:
        # Keep reviewer choices if this is a previously generated review sheet.
        row.setdefault("review_accept", "")
        row.setdefault("entrance_linkage_approved", "")
        ok, reasons = _source_complete(row)
        linkage_pending = "entrance_candidate_requires_explicit_acceptance" in reasons
        other_reasons = [r for r in reasons if r != "entrance_candidate_requires_explicit_acceptance"]
        reviewable = not other_reasons and (ok or linkage_pending)

        if reviewable:
            candidates += 1
            if args.approve_source_complete:
                if not _as_bool(row.get("review_accept")):
                    row["review_decision"] = "REVIEWER_NOT_APPROVED"
                    row["review_reasons"] = "set review_accept=true only after row-level human review"
                    row["review_method"] = "explicit_review_pending"
                else:
                    linkage_ok = (not linkage_pending) or _as_bool(row.get("entrance_linkage_approved")) or args.accept_nearest_entrance_linkage
                    if not linkage_ok:
                        row["review_decision"] = "ENTRANCE_LINKAGE_REVIEW_PENDING"
                        row["review_reasons"] = "set entrance_linkage_approved=true after verifying nearest candidate is the intended trip entrance"
                        row["review_method"] = "explicit_review_pending"
                        nearest_blocked += 1
                    else:
                        if linkage_pending and not all(not _blank(row.get(f)) for f in ENTRANCE_FIELDS):
                            _promote_reviewed_entrance_candidate(row)
                        # Re-check after candidate promotion; approval must never
                        # bypass missing physical/legal/provenance facts.
                        ok2, reasons2 = _source_complete(row)
                        reasons2 = [r for r in reasons2 if r != "entrance_candidate_requires_explicit_acceptance"]
                        if reasons2:
                            row["review_decision"] = "NEW_EVIDENCE_REQUIRED"
                            row["review_reasons"] = ";".join(reasons2)
                            row["review_method"] = "not_review_complete"
                            manual += 1
                        else:
                            row["auditor_id"] = reviewer_id
                            # For source-derived rows observed_at denotes review
                            # time; field evidence_as_of retains source timing.
                            row["observed_at"] = reviewed_at
                            row["reviewed_at"] = reviewed_at
                            row["observation_semantics"] = "source_evidence_review_time_not_field_measurement_time"
                            row["review_decision"] = "ACCEPT_SOURCE_EVIDENCE"
                            row["review_reasons"] = ""
                            row["review_method"] = "explicit_row_level_human_review_of_authoritative_source_match"
                            accepted += 1
            else:
                row["review_decision"] = "ENTRANCE_LINKAGE_REVIEW_PENDING" if linkage_pending else "SOURCE_COMPLETE_REVIEW_PENDING"
                row["review_reasons"] = (
                    "set review_accept=true and entrance_linkage_approved=true after verifying the candidate entrance"
                    if linkage_pending else
                    "set review_accept=true after row-level human confirmation"
                )
                row["review_method"] = "classification_only_no_human_stamp"
                nearest_blocked += int(linkage_pending)
        else:
            row["review_decision"] = "NEW_EVIDENCE_REQUIRED"
            row["review_reasons"] = ";".join(reasons)
            row["review_method"] = "not_review_complete"
            manual += 1
            nearest_blocked += int(linkage_pending)
        output.append(row)

    out = Path(args.output_csv)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=out_fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(output)

    candidate_rows = [r for r in output if r.get("review_decision") in {"SOURCE_COMPLETE_REVIEW_PENDING", "ENTRANCE_LINKAGE_REVIEW_PENDING", "REVIEWER_NOT_APPROVED", "ACCEPT_SOURCE_EVIDENCE"}]
    accepted_rows = [r for r in output if r.get("review_decision") == "ACCEPT_SOURCE_EVIDENCE"]
    unresolved_rows = [r for r in output if r.get("review_decision") == "NEW_EVIDENCE_REQUIRED"]
    _write_subset(args.review_candidates_csv, out_fields, candidate_rows)
    _write_subset(args.accepted_csv, out_fields, accepted_rows)
    _write_subset(args.unresolved_csv, out_fields, unresolved_rows)

    report = {
        "status": "PASS" if not (args.fail_on_manual_required and manual) else "FAIL",
        "mode": "explicit_human_approval" if args.approve_source_complete else "classification_only",
        "rows": len(rows),
        "source_complete_review_candidates": candidates,
        "source_evidence_accepted": accepted,
        "new_evidence_required": manual,
        "nearest_entrance_linkage_blocked": nearest_blocked,
        "reviewed_at": reviewed_at,
        "reviewer_id": reviewer_id or None,
        "output_csv": str(out),
        "review_candidates_csv": str(args.review_candidates_csv) if args.review_candidates_csv else None,
        "accepted_csv": str(args.accepted_csv) if args.accepted_csv else None,
        "unresolved_csv": str(args.unresolved_csv) if args.unresolved_csv else None,
        "interpretation": "Classification mode never fabricates a human audit. Approval mode requires an explicit reviewer assertion and still creates no new physical/legal/entrance facts.",
    }
    if args.report_json:
        rp = Path(args.report_json); rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    if args.fail_on_manual_required and manual:
        raise RuntimeError(f"{manual} rows still require new evidence/manual audit")
    print("PUDO_AUDIT_REVIEW_CHECK=PASS")


if __name__ == "__main__":
    main()
