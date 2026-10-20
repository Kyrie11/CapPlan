#!/usr/bin/env python3
"""Controlled finite-model scaling falsification, *not* published ICRA baseline.

State growth of a generic exhaustive AND/OR enumerator does not prove that
POMCP, PRISM factored shielding, or other strong solvers cannot handle it.
Every generated site is a synthetic node, not real nuPlan road space.
"""
from __future__ import annotations
import argparse,json,sys,time
from copy import deepcopy
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from capplan.handoff.commitment_model_v10 import FiniteCommitmentModel,GenericSupportSolver


def generate_case(n_sites:int,n_worlds:int,horizon:int):
    names=[f'Z{i}' for i in range(n_sites)]
    pgraph={'O':[]};vgraph={'O':names[:]} ;sites={};worlds=[]
    for name in names:
        pgraph['O'].append({'to':name,'length_m':1,'min_width_m':1.2})
        pgraph[name]=[{'to':'O','length_m':1,'min_width_m':1.2}]
        vgraph[name]=['O']+[x for x in names if x!=name]
        sites[name]={'passenger_node':name,'motion_ok':True,'interface_ok':True}
    # Exactly one reliable anchor. Every world shares it, no artificially
    # adversarial absence of all success options. Other sites have hidden states.
    for i in range(n_worlds):
        occupancy={name:([True]*(horizon+1) if name=='Z0' else
                        [((i >> (j%max(1,n_worlds.bit_length())))&1)==0 for _ in range(horizon+1)])
                   for j,name in enumerate(names)}
        worlds.append({'id':f'w{i}','probability':1/n_worlds,'open':occupancy})
    return {'case_id':f'generated_sites{n_sites}_worlds{n_worlds}_horizon{horizon}',
       'sites':sites,'pedestrian_graph':pgraph,'vehicle_graph':vgraph,'worlds':worlds,
       'passenger':{'start':'O'},'vehicle':{'start':'O'},
       'horizon_ticks':horizon,'max_distance_m':4,'max_wait_ticks':horizon,
       'ack_delay_ticks':0,'evidence_ttl_ticks':3,'boarding_ticks':1,
       'required_width_m':1.0,'require_evidence_for_board':True,
       'initial_evidence':{'Z0':{'open':True,'observed_at':0,'valid_until':horizon}},
       'evidence_type':'CONTROLLED_SYNTHETIC_NOT_REAL_CITY'}


def bench(specs,cap):
    rows=[]
    for n,w,h in specs:
        start=time.perf_counter()
        m=FiniteCommitmentModel(generate_case(n,w,h))
        r=GenericSupportSolver(m,max_states=cap).solve()
        elapsed=time.perf_counter()-start
        rows.append({'sites':n,'worlds':w,'horizon_ticks':h,'wall_seconds':round(elapsed,6),
                    'max_states':cap,'status':r.status,'explored_states':r.explored_states,
                    'evaluated_actions':r.evaluated_actions,'motion_queries':r.motion_queries,
                    'motion_unique_queries':r.motion_unique_queries,
                    'unknown_due_to_cap':r.unknown_due_to_cap,
                    'exact_action_set_complete':r.exact_action_set_complete,
                    'policy_cost_optimality_certified':r.policy_cost_optimality_certified,
                    'scope':'BENCHMARK_GENERATED_DISCRETE_MODEL_NOT_CARLA_REAL_WORLD'})
    return rows


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',required=True)
    p.add_argument('--sites',default='2,3,4')
    p.add_argument('--worlds',default='2,4')
    p.add_argument('--horizons',default='4,6,8')
    p.add_argument('--max_states',type=int,default=5000)
    args=p.parse_args()
    ns=[int(x) for x in args.sites.split(',')];ws=[int(x) for x in args.worlds.split(',')];hs=[int(x) for x in args.horizons.split(',')]
    if min(ns)<2 or min(ws)<1 or min(hs)<2 or args.max_states<1:p.error('invalid range')
    rows=bench([(n,w,h) for n in ns for w in ws for h in hs],args.max_states)
    dest=Path(args.output);dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(''.join(json.dumps(x)+'\n' for x in rows))
    summary={'runs':len(rows),'cap_hits':sum(x['unknown_due_to_cap'] for x in rows),
             'statuses':{label:sum(x['status']==label for x in rows) for label in sorted(set(x['status'] for x in rows))},
             'scope':'SYNTHETIC_EXHAUSTIVE_REFERENCE_ONLY',
             'invalid_conclusion':'Cannot infer published factored POMDP shield complexity from this enumerator'}
    dest.with_suffix('.summary.json').write_text(json.dumps(summary,indent=2)+'\n')
    print(json.dumps(summary))
if __name__=='__main__':main()
