#!/usr/bin/env python3
"""Preregistered V12 contract/latency stress (controlled synthetic mechanisms).

Only change communication delay, observation TTL, boarding dwell, and declared
capability budgets; hold topology/world time traces fixed. This measures
robustness to parameters in finite synthetic cases, not population incidence.
Each case preserves its parent and deterministic variant identity.
"""
from __future__ import annotations
import argparse,json
from copy import deepcopy
from pathlib import Path

VARIANTS=('base','ack_plus2','ack_plus4','ttl_one','dwell_plus1','walk_budget_075','wait_budget_075')

def variants(case):
    out=[]
    for variant in VARIANTS:
        c=deepcopy(case)
        original=case['case_id']
        c['parent_case_id']=original;c['stress_variant']=variant;c['case_id']=original+'::'+variant
        if variant=='ack_plus2':c['ack_delay_ticks']=int(c['ack_delay_ticks'])+2
        elif variant=='ack_plus4':c['ack_delay_ticks']=int(c['ack_delay_ticks'])+4
        elif variant=='ttl_one':c['evidence_ttl_ticks']=1
        elif variant=='dwell_plus1':c['boarding_ticks']=int(c['boarding_ticks'])+1
        elif variant=='walk_budget_075':
            c['max_distance_m']=max(float(c['passenger'].get('walked_m',0)),float(c['max_distance_m'])*.75)
        elif variant=='wait_budget_075':
            c['max_wait_ticks']=max(int(c['passenger'].get('waited_ticks',0)),int(c['max_wait_ticks']*.75))
        out.append(c)
    return out

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cases',required=True);p.add_argument('--output',required=True)
    a=p.parse_args()
    base=[json.loads(s) for s in Path(a.cases).read_text().splitlines() if s.strip()]
    out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True)
    cases=[v for c in base for v in variants(c)]
    out.write_text(''.join(json.dumps(c,ensure_ascii=False)+'\n' for c in cases))
    out.with_suffix('.manifest.json').write_text(json.dumps({'base_count':len(base),'stress_count':len(cases),
        'variants':VARIANTS,'model':'finite synthetic counterfactuals not independent observed events'},indent=2)+'\n')
    print(f'Created {len(cases)} controlled cases from {len(base)} parents')
if __name__=='__main__':main()
