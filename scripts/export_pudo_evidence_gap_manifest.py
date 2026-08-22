#!/usr/bin/env python
"""Export a compact cross-city manifest of unresolved PUDO evidence gaps."""
from __future__ import annotations
import argparse, csv, json
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

CITIES=("boston","pittsburgh","vegas","singapore")
KEEP=(
 "audit_id","city","lon","lat","split_membership","candidate_sources","episode_ids_train","episode_ids_val","episode_ids_test",
 "curb_height_m","sidewalk_width_m","deployment_clearance_m","curb_ramp","legal_stop","legal_basis",
 "entrance_id","entrance_lon","entrance_lat","entrance_candidate_id","entrance_candidate_lon","entrance_candidate_lat","entrance_candidate_source",
 "remaining_required_fields","review_reasons","machine_triage_reasons","machine_triage_decision",
)

def main()->None:
    p=argparse.ArgumentParser(description=__doc__); p.add_argument("--external_root",required=True); p.add_argument("--output_csv",required=True); p.add_argument("--report_json",required=True); args=p.parse_args()
    ext=Path(args.external_root); out_rows: List[Dict[str,Any]]=[]; reason_counts=Counter(); missing_counts=Counter(); city_counts=Counter()
    for city in CITIES:
        path=ext/"audits"/city/"new_evidence_required.csv"
        if not path.exists(): continue
        with path.open("r",encoding="utf-8-sig",newline="") as f:
            for row in csv.DictReader(f):
                rec={k:row.get(k,"") for k in KEEP}; rec["city"]=rec.get("city") or city
                reasons=str(row.get("machine_triage_reasons") or row.get("review_reasons") or "")
                rec["missing_or_blocking_evidence"] = reasons
                out_rows.append(rec); city_counts[city]+=1
                for reason in [x for x in reasons.split(";") if x]: reason_counts[reason]+=1
                # Preserve the exact required-field list when present; otherwise derive from missing_required reasons.
                fields=set(x for x in str(row.get("remaining_required_fields") or "").split(";") if x and x not in {"observed_at","auditor_id"})
                for reason in reasons.split(";"):
                    if reason.startswith("missing_required:"):
                        fields.update(x for x in reason.split(":",1)[1].split(",") if x)
                rec["missing_fields"]=";".join(sorted(fields))
                missing_counts.update(fields)
    fields=list(KEEP)+["missing_or_blocking_evidence","missing_fields"]
    out=Path(args.output_csv); out.parent.mkdir(parents=True,exist_ok=True)
    with out.open("w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore"); w.writeheader(); w.writerows(out_rows)
    report={"status":"PASS","rows":len(out_rows),"city_counts":dict(city_counts),"missing_field_counts":dict(missing_counts),"reason_counts":dict(reason_counts),"output_csv":str(out),"interpretation":"This is an acquisition/review manifest, not ground truth. Missing numeric/semantic facts remain missing until supported by evidence."}
    rp=Path(args.report_json); rp.parent.mkdir(parents=True,exist_ok=True); rp.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps(report,indent=2,sort_keys=True)); print("PUDO_EVIDENCE_GAP_MANIFEST=PASS")
if __name__=="__main__": main()
