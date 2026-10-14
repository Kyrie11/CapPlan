#!/usr/bin/env python3
"""Bind functional distance thresholds and explicit wait-budget controls.

Does not assume that max_wait_exposure_s equals total accumulated wait. Wait
limits are a separately controlled design axis; slope, device and door fields
are NOT evaluated by the finite two-anchor oracle.
"""
from __future__ import annotations
import argparse,copy,json,math,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from capplan.handoff.gate_evidence import records,dump_json,site_key
from capplan.data.handoff_schema import validate_temporal_overlay


def build(overlays,profiles,output,*,max_sites=8,max_profiles=8,wait_budgets=(30.,120.,None)):
    base=[];seen=set()
    for row in records(overlays):
        # Each site on one fixed event family; keep worlds identical across profiles.
        key=site_key((row.get('provenance') or {}).get('source_pair') or {}) or (row['episode_id'],)
        if key in seen:continue
        seen.add(key);base.append(row)
        if len(base)>=max_sites:break
    pr=[]
    for p in records(profiles):
        if len(pr)>=max_profiles:break
        if p.get('evaluation_suite')!='canonical_counterfactual' or p.get('population_representative') is True:
            raise ValueError('only synthetic nonpopulation canonical profiles supported')
        pr.append(p)
    out=Path(output);out.parent.mkdir(parents=True,exist_ok=True)
    total=0
    with out.open('w',encoding='utf-8') as f:
        for si,src in enumerate(base):
            for pi,p in enumerate(pr):
                distance=p['mobility']['max_access_distance_m']
                if not isinstance(distance,(float,int)) or not math.isfinite(distance) or distance<=0:
                    raise ValueError('invalid profile distance')
                for wi,wait in enumerate(wait_budgets):
                    o=copy.deepcopy(src)
                    o['overlay_id']=f'{src["overlay_id"]}:site{si}:profile{pi}:wait{wi}'
                    o['capability_budget'].update(distance_budget_m=float(distance),
                        remaining_distance_m=float(distance)-float(o['initial_execution_state']['walked_or_rolled_m']),
                        wait_budget_s=wait,profile_id=p['profile_id'],
                        profile_role='canonical_profile_bound_distance_only_wait_synthetic_control')
                    o['provenance']['profile_test']={
                        'suite':'canonical_nonpopulation','profile_id':p['profile_id'],
                        'distance_source':'mobility.max_access_distance_m',
                        'wait_source':'separate controlled wait_budget_s, NOT profile max_wait_exposure_s',
                        'ignored_profile_dimensions':['slope','width','boarding equipment','cross_slope','rest',
                         'device type','dwell','ride'],
                        'same_event_worlds_as_base':True}
                    if o['capability_budget']['remaining_distance_m'] < 0:continue
                    validate_temporal_overlay(o)
                    f.write(json.dumps(o,ensure_ascii=False)+'\n');total+=1
    report={'overlays_generated':total,'source_distinct_site_groups':len(base),'profiles_used':len(pr),
            'wait_budget_controls_s':wait_budgets,
            'population_claim_allowed':False,'full_capability_validation':False,
            'warning':'Oracle checks ONLY cumulative distance and synthetic wait budget. Other profile properties ignored. Do not label passenger-complete feasibility.'}
    dump_json(out.with_suffix('.manifest.json'),report)
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--overlays',required=True);p.add_argument('--profiles',required=True)
    p.add_argument('--output',required=True);p.add_argument('--max_sites',type=int,default=8)
    p.add_argument('--max_profiles',type=int,default=8)
    p.add_argument('--wait_budgets',default='30,120,none')
    a=p.parse_args();wait=[None if x.strip().lower()=='none' else float(x) for x in a.wait_budgets.split(',')]
    if any(x is not None and (x<0 or not math.isfinite(x)) for x in wait):p.error('invalid budgets')
    print(json.dumps(build(a.overlays,a.profiles,a.output,max_sites=a.max_sites,
                           max_profiles=a.max_profiles,wait_budgets=wait),ensure_ascii=False))
