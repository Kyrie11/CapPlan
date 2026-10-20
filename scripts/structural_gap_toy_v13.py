#!/usr/bin/env python3
"""Exhaustive 2-site *static* structural counterexample, not prevalence.

Each PUDO has three independent booleans: pedestrian capability, vehicle
stop/legal feasibility, interface deployment. Enumeration over all 2^6 truth
assignments shows the fallacy of composing independent existential claims and
of choosing a nearest site before hard-validating the shared interface.
"""
from __future__ import annotations
import argparse,itertools,json
from pathlib import Path

def run():
    patterns=[]
    for bits in itertools.product((False,True),repeat=6):
        a=bits[:3];b=bits[3:];at=all(a);bt=all(b)
        # The independent checker can find one site for each subproblem
        separate=all(any(bits[i] for i in (j,j+3)) for j in range(3))
        joint=at or bt
        patterns.append({'a':a,'b':b,'separate_exists':separate,'joint_exists':joint,
                         'independent_join_false_positive':separate and not joint,
                         'nearest_A_then_validate_failure_when_B_feasible':not at and bt})
    summary={'universe':'EXHAUSTIVE_BOOLEAN_TRUTH_ASSIGNMENTS_NOT_EMPIRICAL_FREQUENCY',
             'site_count':2,'universe_size':len(patterns),
             'independent_component_existentials':sum(p['separate_exists'] for p in patterns),
             'genuine_same_site_solution':sum(p['joint_exists'] for p in patterns),
             'false_join_patterns':sum(p['independent_join_false_positive'] for p in patterns),
             'nearest_A_trap_with_available_B':sum(p['nearest_A_then_validate_failure_when_B_feasible'] for p in patterns),
             'representative_counterexample':{'site_A':{'passenger':True,'vehicle':True,'interface':False},
                   'site_B':{'passenger':True,'vehicle':False,'interface':True},
                   'component_wise_passes':True,'joint_handoff_exists':False},
             'negative_control':{'site_A':{'passenger':True,'vehicle':True,'interface':True},
                   'site_B':{'passenger':False,'vehicle':True,'interface':True},
                   'joint_handoff_exists':True},
             'claim':'Existing independently solved components cannot be safely joined without shared-site interface constraints.',
             'not_claim':'No claim that all current methods are independent or that those configurations occur at any measured prevalence.'}
    return summary

def main():
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);a=p.parse_args()
    out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True)
    r=run();out.write_text(json.dumps(r,indent=2,ensure_ascii=False)+'\n');print(json.dumps(r,ensure_ascii=False))
if __name__=='__main__':main()
