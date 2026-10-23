#!/usr/bin/env python3
"""Seeded CONTROLLED multi-observation falsification workload, no city incidence.

Cases are not copied from real nuPlan sites and may not be cited as real
boarding evidence. Compare exact initial safe action SETS under equal actions.
"""
from __future__ import annotations
import argparse
from collections import Counter
import json
from pathlib import Path
import random
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from capplan.handoff.causal_policy_v2 import compare_jsonl


def generate(seed: int=13, n: int=100) -> list[dict]:
    rng=random.Random(seed);out=[]
    for idx in range(n):
        budget=rng.randint(6,13)
        options={name:{'drive_eta_s':rng.randint(1,5),'ack_s':1,'verify_s':rng.randint(1,2),
                       'boarding_s':rng.randint(1,3)} for name in ('A','B')}
        directed={'O':{'A':rng.randint(2,6),'B':rng.randint(3,8)}}
        if rng.random()<.8:directed['A']={'B':rng.randint(2,7)}
        if rng.random()<.5:directed['B']={'A':rng.randint(3,8)}
        worlds=[]
        for w in range(rng.choice((2,3))):
            sites={}
            for name in options:
                tstart=rng.randint(0,9)
                stop_end=rng.randint(15,21)
                evidence_end=rng.randint(10,21)
                available=rng.random()<.7
                open_from=rng.randint(0,8)
                full_stop=[[tstart,stop_end]]
                if rng.random()<.25 and tstart+1<stop_end:
                    cut=rng.randint(tstart+1,stop_end-1)
                    full_stop=[[tstart,cut],[cut,stop_end]]
                sites[name]={'stop':full_stop,'evidence':[[0,evidence_end]],
                             'interface':[[0,21]],
                             'open':[[open_from,21]] if available else []}
            worlds.append({'id':f'w{w}','handoff_windows':sites})
        out.append({'case_id':f'controlled_seed{seed}_{idx:04d}',
                    'claim_scope':'CONTROLLED_SYNTHETIC_ONLY',
                    'horizon_s':21,
                    'capability':{'max_distance_m':budget,'max_wait_s':rng.randint(8,17),
                                  'speed_mps':rng.choice((0.8,1,1.2))},
                    'options':options,'directed_edges_m':directed,
                    'initial':{'node':'O','consumed_m':rng.randint(0,2),
                               'waited_s':0,'time_s':0},
                    'worlds':worlds})
    return out


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--seed',type=int,default=13)
    p.add_argument('--n',type=int,default=100)
    p.add_argument('--output-prefix',required=True)
    p.add_argument('--max-states',type=int,default=12000)
    a=p.parse_args()
    if a.n<1 or a.n>100000 or a.max_states<1:p.error('invalid n or max-states')
    prefix=Path(a.output_prefix);prefix.parent.mkdir(parents=True,exist_ok=True)
    path=prefix.with_suffix('.cases.jsonl')
    with path.open('w',encoding='utf-8') as out:
        for case in generate(a.seed,a.n):out.write(json.dumps(case)+'\n')
    report=compare_jsonl(path,prefix.with_suffix('.results.jsonl'),max_states=a.max_states)
    print(json.dumps({'seed':a.seed,'generated':a.n,'cases':str(path),'report':report}))

if __name__=='__main__':main()
