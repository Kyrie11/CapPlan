#!/usr/bin/env python3
"""Adversarial H4 robustness audit, NOT a real-city or published shield comparison.

Controls *two assumptions that previously generated a 3-way interaction*:
(1) serial versus parallel passenger guidance + vehicle dispatch/verification;
(2) zero versus nonzero passenger waiting allowance.

Uses the exact SAME fully declared finite worlds and physical *simulated* interface
booleans. A constructed 3-way contrast only counts as robust if it survives the
preregistered protocol variants; it is never a statistical effect size.
"""
from __future__ import annotations
import argparse
from collections import Counter
from copy import deepcopy
from itertools import product
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from capplan.handoff.commitment_model_v10 import FiniteCommitmentModel,GenericSupportSolver,verify_policy_in_all_worlds


def contrast(cell_map):
    if len(cell_map)!=8: return None
    return sum((-1)**(3-sum(key))*value for key,value in cell_map.items())


def evaluate_factorial(cases, *, max_states=40000, waiting=(0,1), parallel=(False,True)):
    factor=[c for c in cases if c.get('family')=='H4_factorial']
    if len(factor)!=8:
        raise ValueError(f'expected exactly 8 existing H4 cells; got {len(factor)}')
    rows=[];blocks=[]
    for wait,joint in product(waiting,parallel):
        cells={};complete=True
        for original in factor:
            c=deepcopy(original)
            c['max_wait_ticks']=int(wait)
            c['allow_parallel_control_actions']=bool(joint)
            m=FiniteCommitmentModel(c); solver=GenericSupportSolver(m,max_states=max_states)
            r=solver.solve()
            policy=solver.memo.get(m.initial,(None,None))[1] if r.status=='WIN' else None
            replay=verify_policy_in_all_worlds(m,policy) if policy is not None else None
            v=c['intervention']
            key=(v['required_width_m']==1.2,v['b_cert_expiry']==1,v['vehicle_start']=='A')
            if key in cells: raise ValueError('duplicate factorial cell')
            if r.unknown_due_to_cap or r.status not in ('WIN','LOSE'):complete=False
            else:cells[key]=int(r.status=='WIN')
            rows.append({'case_id':c['case_id'],'max_wait_ticks':wait,
                         'allow_parallel_control_actions':joint,
                         'required_width_m':v['required_width_m'],
                         'b_cert_expiry':v['b_cert_expiry'],
                         'vehicle_start':v['vehicle_start'],
                         'status':r.status,'best_action':r.best_action,
                         'safe_first_actions':r.first_safe_actions,
                         'explored_states':r.explored_states,
                         'unknown_due_to_cap':r.unknown_due_to_cap,
                         'policy_cost_optimality_certified':r.policy_cost_optimality_certified,
                         'all_world_replay_pass':None if replay is None else all(x['success'] for x in replay.values()),
                         'source_scope':'SYNTHETIC_FINITE_REPLAY'})
        blocks.append({'max_wait_ticks':wait,'allow_parallel_control_actions':joint,
                       'cells':len(cells),'fully_explored':complete and len(cells)==8,
                       'winning_cells':sum(cells.values()) if complete else None,
                       'three_way_binary_contrast':contrast(cells) if complete else None,
                       'cell_key': '(width_is_1p2,expiry_is_1,vehicle_is_A)'})
    original=next((r for r in blocks if r['max_wait_ticks']==0 and not r['allow_parallel_control_actions']),None)
    joint=next((r for r in blocks if r['max_wait_ticks']==0 and r['allow_parallel_control_actions']),None)
    waiting_one=next((r for r in blocks if r['max_wait_ticks']==1 and not r['allow_parallel_control_actions']),None)
    statements=[]
    if original and joint and original['fully_explored'] and joint['fully_explored']:
        if original['three_way_binary_contrast']!=0 and joint['three_way_binary_contrast']==0:
            statements.append('H4_ORIGINAL_INTERACTION_REMOVED_BY_PARALLEL_DISPATCH')
    if original and waiting_one and original['fully_explored'] and waiting_one['fully_explored']:
        if original['three_way_binary_contrast']!=0 and waiting_one['three_way_binary_contrast']==0:
            statements.append('H4_ORIGINAL_INTERACTION_REMOVED_BY_ONE_WAIT_TICK')
    if any(not b['fully_explored'] for b in blocks):
        statements.append('UNKNOWN_CAP_MAKES_SOME_INTERACTION_CONTRASTS_UNCERTIFIED')
    summary={'cells_per_variant':8,'protocol_variants':len(blocks),
             'protocols':blocks,'findings':statements,
             'time_units':'synthetic_ticks_no_empirical_time_calibration',
             'evidence':'CONTROLLED_SYNTHETIC_NOT_REAL_CITY',
             'not_estimated':['real-world incidence','population effect','published POMDP shielding performance'],
             'research_interpretation': 'Interaction is a property of a declared dynamics+action model, not an inherent limitation of generic POMDP shielding. Replicate with independent motion, curb and timing evidence.'}
    return rows,summary


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--cases',required=True)
    ap.add_argument('--output',required=True)
    ap.add_argument('--max_states',type=int,default=40000)
    ap.add_argument('--wait_values',default='0,1')
    ap.add_argument('--parallel_values',default='0,1')
    args=ap.parse_args()
    waiting=tuple(int(v) for v in args.wait_values.split(','))
    parallel=tuple(bool(int(v)) for v in args.parallel_values.split(','))
    if args.max_states<1 or any(v<0 for v in waiting) or not waiting or not parallel:
        ap.error('invalid parameter values')
    cases=[json.loads(s) for s in Path(args.cases).read_text(encoding='utf-8').splitlines() if s.strip()]
    rows,summary=evaluate_factorial(cases,max_states=args.max_states,waiting=waiting,parallel=parallel)
    out=Path(args.output);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in rows),encoding='utf-8')
    out.with_suffix('.summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'variants':len(summary['protocols']),'findings':summary['findings'],
                      'output':str(out)},ensure_ascii=False))
if __name__=='__main__':main()
