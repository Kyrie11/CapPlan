#!/usr/bin/env python3
"""Evaluate finite-support candidate-service policies on controlled overlays.

Never report these numbers as vehicle-verified handoffs, observed blockage
prevalence, or population-weighted performance.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from capplan.handoff.finite_oracle import FiniteHandoffOracle, evaluate_baselines
from capplan.data.handoff_schema import validate_temporal_overlay


def records(path):
    with Path(path).open(encoding='utf-8') as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def evaluate(input_path: str, output_dir: str, *, speed_mps=1.0, boarding_s=10.0,
             waiting_cost_mps=0.04, verification_cost_m=4.0, limit=0):
    out=Path(output_dir);out.mkdir(parents=True,exist_ok=True)
    details=out/'policy_rollouts.jsonl'
    n=0;errs=[]; agg=defaultdict(lambda: {'episodes':0,'support_covered':0,'sum_success':0.0,'sum_distance':0.0,'sum_fcr':0.0,'fcr_n':0,'sum_pcrl':0.0,'pcrl_n':0,'budget_breach_worlds':0})
    with details.open('w',encoding='utf-8') as handle:
        for row in records(input_path):
            if limit and n >= limit:break
            try:
                validate_temporal_overlay(row)
                oracle=FiniteHandoffOracle(row,speed_mps,boarding_s,waiting_cost_mps,verification_cost_m)
                results=evaluate_baselines(oracle)
                vehicle_evidence=bool(row.get('motivation_witness',{}).get('vehicle_a_to_b_verified_for_event_time'))
                compact={'overlay_id':row['overlay_id'],'episode_id':row['episode_id'],
                         'map_name':row.get('provenance',{}).get('source_pair',{}).get('map_name'),
                         'observation_schedule':row.get('observation_model',{}).get('schedule_provenance'),
                         'vehicle_motion_verified':vehicle_evidence,
                         'physical_site_key':row.get('provenance',{}).get('source_pair',{}).get('physical_site_pair'),
                         'evidence_class':row['provenance']['dynamic_event'],
                         'profile_role':row['capability_budget'].get('profile_role'),
                         'results':results}
                handle.write(json.dumps(compact,ensure_ascii=False)+'\n')
                for name,r in results.items():
                    g=agg[name];g['episodes']+=1;g['support_covered']+=int(r['world_support_guaranteed'])
                    g['sum_success']+=r['weighted_candidate_success_rate']
                    g['sum_distance']+=r['weighted_burden_m']
                    g['budget_breach_worlds']+=r['budget_breach_worlds']
                    if r['weighted_false_commit_rate'] is not None:
                        g['sum_fcr']+=r['weighted_false_commit_rate'];g['fcr_n']+=1
                    if r['weighted_post_commitment_recourse_loss_rate'] is not None:
                        g['sum_pcrl']+=r['weighted_post_commitment_recourse_loss_rate'];g['pcrl_n']+=1
                n+=1
            except (ValueError,KeyError,TypeError,RuntimeError,RecursionError,OverflowError) as exc:
                errs.append({'overlay_id':row.get('overlay_id'),'error':repr(exc)})
    summary={'evaluated_overlays':n,'errors':errs,
             'scope':'finite-support two-anchor controlled service candidates; not vehicle closed loop, VHCR, population prevalence, or a full published POMDP shield',
             'oracle_scope':'exhaustive only over O/A/B, guide-to-anchor and one delayed verification, observed boarding outcome; no rescue, external assistance, continuous vehicle control, or arbitrary pedestrian route',
             'vehicle_verified_overlay_count':sum(1 for row in records(details) if row['vehicle_motion_verified']),
             'privileged_schedule_overlay_count':sum(1 for row in records(details)
                        if str(row.get('observation_schedule','')).startswith('PRIVILEGED_')),
             'methods':{name:{'n':g['episodes'],
                       'conditional_support_guarantee_rate':g['support_covered']/g['episodes'],
                       'mean_weighted_candidate_success':g['sum_success']/g['episodes'],
                       'mean_weighted_distance_m':g['sum_distance']/g['episodes'],
                       'worlds_with_budget_breach':g['budget_breach_worlds'],
                       'conditional_false_commit_rate':g['sum_fcr']/g['fcr_n'] if g['fcr_n'] else None,
                       'conditional_false_commit_denominator':g['fcr_n'],
                       'post_commitment_recourse_loss_rate':g['sum_pcrl']/g['pcrl_n'] if g['pcrl_n'] else None,
                       'post_commitment_recourse_loss_denominator':g['pcrl_n']}
                        for name,g in agg.items()}}
    (out/'policy_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'evaluated':n,'errors':len(errs),'output':str(out)},ensure_ascii=False))
    return 2 if errs else 0


def main():
    p=argparse.ArgumentParser();p.add_argument('--overlays',required=True);p.add_argument('--output_dir',required=True)
    p.add_argument('--speed_mps',type=float,default=1.0)
    p.add_argument('--boarding_s',type=float,default=10.0)
    p.add_argument('--waiting_cost_mps',type=float,default=0.04)
    p.add_argument('--verification_cost_m',type=float,default=4.0)
    p.add_argument('--limit',type=int,default=0)
    a=p.parse_args()
    return evaluate(a.overlays,a.output_dir,speed_mps=a.speed_mps,boarding_s=a.boarding_s,
                    waiting_cost_mps=a.waiting_cost_mps,verification_cost_m=a.verification_cost_m,limit=a.limit)

if __name__=='__main__':
    raise SystemExit(main())
