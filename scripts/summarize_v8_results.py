#!/usr/bin/env python
from __future__ import annotations
import argparse,csv,json
from pathlib import Path

KEYS=[
    'PCR','OraclePCR','PCDecisionF1','PCFalseAcceptRate','PCFalseRejectRate',
    'TSBS_expansions_mean','TSBS_expansions_p95','PlannerLatency_ms_mean','PlannerLatency_ms_p95',
    'DF_phase_macro_f1','DF_resource_macro_f1','DF_source_macro_f1','DF_certificate_exact_match',
    'CVK_typed_pruned_mean','WPA_summary_checks_mean','WPA_raw_suffixes_mean','WPA_raw_proofs_mean',
    'WPA_antichain_size_mean','DCP_build_candidates_mean','DCP_edge_relaxations_mean',
    'DCP_rejection_antichain_size_mean','DiagnosticReplayRate','DiagnosticReplayExpansionsMean','DiagnosticReplayRescueRate',
]

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--entry',action='append',default=[]); ap.add_argument('--output',required=True); a=ap.parse_args()
    rows=[]
    for entry in a.entry:
        name,path=entry.split('=',1); d=json.loads((Path(path)/'metrics.json').read_text())
        rows.append({'variant':name, **{k:d.get(k) for k in KEYS}})
    out=Path(a.output); out.parent.mkdir(parents=True,exist_ok=True)
    with out.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['variant']+KEYS); w.writeheader(); w.writerows(rows)
    for r in rows: print(r)
if __name__=='__main__': main()
