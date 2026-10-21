#!/usr/bin/env python3
"""Compare V12 interval solver to an independent V13 integer-grid reference.

A small finite-policy correctness gate, never a claim against AAAI/ICRA shields.
"""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from capplan.handoff.temporal_recourse_v12 import analyze
from capplan.handoff.semantic_oracle_v13 import evaluate

def main():
    p=argparse.ArgumentParser();p.add_argument('--cases',required=True)
    p.add_argument('--output',required=True);p.add_argument('--max_seconds',type=int,default=12000)
    a=p.parse_args();out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True)
    n=mismatches=gaps=unknown=0;examples=[]
    with Path(a.cases).open(encoding='utf8') as src, out.open('w',encoding='utf8') as dst:
        for line in src:
            if not line.strip():continue
            case=json.loads(line);r=analyze(case);g=evaluate(case,max_seconds=a.max_seconds)
            n+=1;agree=r['status']==g['status'];mismatches+=int(not agree)
            gaps+=int(g['nonanticipativity_gap']);unknown+=int(g['unknown_data_encountered'])
            row={'case_id':case.get('case_id'),'interval_status':r['status'],'grid_status':g['status'],
                 'agree':agree,'clairvoyant_possible':g['clairvoyant_possible'],
                 'nonanticipativity_gap':g['nonanticipativity_gap'],
                 'grid_witness':g['fixed_witness'] or g['one_observation_witnesses'],
                 'evidence':'CONTROLLED_OR_ASSERTED_INPUT_NOT_PHYSICAL_GROUND_TRUTH'}
            dst.write(json.dumps(row,ensure_ascii=False)+'\n')
            if not agree and len(examples)<20:examples.append(row)
    summary={'n':n,'mismatches':mismatches,'clairvoyant_no_causal_policy_cases':gaps,
             'encountered_unknown_inputs':unknown,'mismatch_examples':examples,
             'scope':'INTEGER_SECONDS_AND_RESTRICTED_ONE_OBSERVATION_ONLY',
             'published_shield_reproduced':False,
             'research_gate':'PASS' if n>0 and not mismatches else 'FAIL'}
    out.with_suffix('.summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(summary,ensure_ascii=False));
    if n==0 or mismatches:raise SystemExit(2)
if __name__=='__main__':main()
