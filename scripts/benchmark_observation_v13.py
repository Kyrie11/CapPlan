#!/usr/bin/env python3
"""Controlled AND-OR observation benchmark versus an INDEPENDENT integer grid.

Not a generic POMDP or a published shielding baseline; observation timestamps,
worlds and sites are synthetic. Reports exactness, safety gap and timings.
"""
from __future__ import annotations
import argparse,json,random,time,sys
from collections import Counter
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from capplan.handoff.temporal_recourse_v12 import analyze
from capplan.handoff.semantic_oracle_v13 import evaluate


def make(seed,horizon,options,world_count):
    r=random.Random(seed);ws=[f'w{i}' for i in range(world_count)];reveal=max(1,horizon//5)
    sites=[]
    for i in range(options):
        a=r.randrange(0,max(1,horizon//3));arrival=min(3,horizon//12)
        arrival2=r.randrange(reveal,min(horizon,reveal+max(2,horizon//6)))
        vstart=r.randrange(0,max(1,horizon//3));v_end=r.randrange(max(horizon//2,vstart+1),horizon+1)
        e_end=r.randrange(max(horizon//2,1),horizon+1)
        worlds={}
        for w in ws:
            if r.random()<.25:worlds[w]=[]
            else:
                t=r.randrange(0,max(1,horizon//3));worlds[w]=[[t,horizon]]
        sites.append({'option_id':f's{i}', 'contract_feasible':r.random()>.12,
          'passenger_arrival_quantifier':r.choice(['all','exists']),
          'passenger_arrival_window_s':[a,a+arrival],
          'post_observation_passenger_arrival_window_s':[arrival2,arrival2+arrival],
          'passenger_wait_remaining_s':r.randrange(3,max(4,horizon//5)),
          'boarding_duration_s':r.randrange(1,max(2,min(10,horizon//8))),
          'vehicle_stop_windows_s':[[vstart,v_end]],
          'evidence_validity_windows_s':[[0,e_end]],
          'interface_windows_s':[[0,horizon]],
          'world_handoff_open_windows_s':worlds})
    return {'case_id':f'random_{seed}','world_support':ws,'options':sites,
            'one_observation':{'available_at_s':reveal,
                'outcomes_by_world':{w:f'y{j%max(1,world_count//2)}' for j,w in enumerate(ws)}}}


def run(seed,replicates,horizon,options,worlds):
    counts=Counter();mismatches=[];mismatch_count=0;t_fast=t_grid=0
    for i in range(replicates):
        c=make(seed+i,horizon,options,worlds)
        t=time.perf_counter_ns();fast=analyze(c);t_fast+=time.perf_counter_ns()-t
        t=time.perf_counter_ns();grid=evaluate(c,max_seconds=horizon+100);t_grid+=time.perf_counter_ns()-t
        counts[fast['status']]+=1
        counts['clairvoyant_all_worlds_possible']+=int(grid['clairvoyant_possible'])
        counts['clairvoyant_without_causal_policy']+=int(grid['nonanticipativity_gap'])
        if fast['status']!=grid['status']:
            mismatch_count+=1
            if len(mismatches)<20:
                mismatches.append({'id':i,'interval':fast['status'],'grid':grid['status']})
    return {'scope':'RANDOM_FINITE_ONE_OBSERVATION_SYNTHETIC_ONLY',
         'no_published_shield_reproduction':True,
         'parameters':{'seed':seed,'replicates':replicates,'horizon_s':horizon,'options':options,'worlds':worlds},
         'statuses':dict(counts),'mismatches':mismatches,
         'mismatch_count':mismatch_count,
         'interval_total_ms':round(t_fast/1e6,4),'independent_grid_total_ms':round(t_grid/1e6,4),
         'speedup_on_this_restricted_model':round(t_grid/t_fast,3) if t_fast else None,
         'passed':mismatch_count==0}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',required=True);p.add_argument('--seed',type=int,default=13)
    p.add_argument('--replicates',type=int,default=300);p.add_argument('--horizon',type=int,default=100)
    p.add_argument('--options',type=int,default=5);p.add_argument('--worlds',type=int,default=4)
    a=p.parse_args()
    if any(x<1 for x in (a.replicates,a.options,a.worlds)) or a.horizon<20:p.error('invalid parameters')
    r=run(a.seed,a.replicates,a.horizon,a.options,a.worlds)
    out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(r,ensure_ascii=False))
    if not r['passed']:raise SystemExit(2)
if __name__=='__main__':main()
