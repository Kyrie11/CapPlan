#!/usr/bin/env python
from __future__ import annotations
import argparse, csv, json
from pathlib import Path

KEYS = [
    "PCR","OraclePCR","PCDecisionPrecision","PCDecisionRecall","PCDecisionF1",
    "PCFalseAcceptRate","PCFalseRejectRate","PlanReturnRate",
    "CF_success_flip_precision","CF_success_flip_recall",
    "DF_phase_macro_f1","DF_resource_macro_f1","DF_source_macro_f1","DF_certificate_exact_match","SME",
    "TSBS_expansions_mean","TSBS_expansions_p95","CCE_pruned_mean","CCE_pruned_p95","PlannerLatency_ms_mean","PlannerLatency_ms_p95",
]

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--entry', action='append', required=True, help='label=/path/to/variant_dir')
    ap.add_argument('--output', required=True)
    args=ap.parse_args()
    rows=[]
    for item in args.entry:
        label, path = item.split('=',1)
        d=json.loads((Path(path)/'metrics.json').read_text())
        rows.append({'method':label, **{k:d.get(k) for k in KEYS}})
    out=Path(args.output); out.parent.mkdir(parents=True, exist_ok=True)
    with out.open('w', newline='', encoding='utf-8') as f:
        w=csv.DictWriter(f, fieldnames=['method',*KEYS]); w.writeheader(); w.writerows(rows)
    for r in rows: print(r)
if __name__=='__main__': main()
