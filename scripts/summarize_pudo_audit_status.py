#!/usr/bin/env python
"""Summarize PUDO audit readiness across cities into a small report artifact."""
from __future__ import annotations
import argparse, csv, json
from collections import Counter
from pathlib import Path
from typing import Any, Dict

CITIES = ("boston", "pittsburgh", "vegas", "singapore")

def _csv_count(path: Path) -> int:
    if not path.exists(): return 0
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        return sum(1 for _ in csv.DictReader(f))

def _load(path: Path) -> Dict[str, Any]:
    if not path.exists(): return {}
    try: return json.loads(path.read_text(encoding="utf-8"))
    except Exception: return {}

def main() -> None:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--external_root", required=True)
    p.add_argument("--reports_root", required=True)
    p.add_argument("--output", required=True)
    args=p.parse_args()
    ext=Path(args.external_root); reports=Path(args.reports_root)
    result: Dict[str, Any]={"status":"PASS","cities":{},"totals":Counter()}
    for city in CITIES:
        root=ext/"audits"/city
        counts={
            "machine_pass": _csv_count(root/"machine_pass_explicit_authoritative.csv"),
            "machine_reject": _csv_count(root/"machine_reject_invalid_or_ambiguous.csv"),
            "visual_review": _csv_count(root/"visual_review_required.csv"),
            "new_evidence": _csv_count(root/"new_evidence_required.csv"),
            "source_complete_review": _csv_count(root/"source_complete_review_candidates.csv"),
            "source_accepted": _csv_count(root/"pudo_audit_source_accepted.csv"),
        }
        tri=_load(reports/f"pudo_audit_triage.{city}.json")
        pre=_load(reports/f"pudo_audit_prefill.{city}.json")
        if counts["visual_review"] or counts["source_complete_review"]:
            next_action="REVIEW_EXISTING_SOURCE_MATCHES"
        elif counts["new_evidence"]:
            next_action="ACQUIRE_OR_RECOVER_MISSING_EVIDENCE"
        elif counts["machine_pass"]:
            next_action="IMPORT_EXPLICIT_AUTHORITATIVE_EVIDENCE"
        else:
            next_action="NO_AUDIT_ROWS_OR_REBUILD_AUDIT_PIPELINE"
        city_rec={"counts":counts,"next_action":next_action,"field_coverage":pre.get("field_coverage",{}),"top_triage_reasons":tri.get("reason_counts",{})}
        result["cities"][city]=city_rec
        for k,v in counts.items(): result["totals"][k]+=v
    result["totals"]=dict(result["totals"])
    result["ready_for_human_source_review"] = bool(result["totals"].get("visual_review") or result["totals"].get("source_complete_review"))
    result["paper_evidence_ready"] = result["totals"].get("new_evidence",0)==0
    result["interpretation"] = "PASS means the triage program completed. It does not mean publication evidence is complete. NEW_EVIDENCE_REQUIRED must remain fail-closed."
    out=Path(args.output); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps(result,indent=2,sort_keys=True)); print("PUDO_AUDIT_STATUS=PASS")
if __name__=="__main__": main()
