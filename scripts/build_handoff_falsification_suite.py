#!/usr/bin/env python3
"""Adversarial/negative controls exposing assumptions of the original two-world model.

All traces are declared CONTROLLED counterfactuals, never measured events.
The fixed observation request slot is EXOGENOUS (not determined by hidden
world identity). Strong belief-space shield remains a required external baseline.
"""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
try:
    from scripts.build_handoff_temporal_overlays import build_overlay
except ModuleNotFoundError:  # direct CLI execution under scripts/
    from build_handoff_temporal_overlays import build_overlay
from capplan.data.handoff_schema import validate_temporal_overlay


def rows(path):
    with Path(path).open(encoding='utf-8') as f:
        for s in f:
            if s.strip():yield json.loads(s)


def make_variant(pair,index,variant,*,event_s=80.0,observe_request_s=120.0,delay_s=5.0):
    if variant not in {'one_sided','two_sided_observable','two_sided_unobservable',
                       'both_fail_negative_control','late_event_negative_control'}:
        raise ValueError(variant)
    # The unobservable condition uses an early fixed information slot and an
    # outage strictly after that slot. Do not leak the outage time into the
    # policy's observation schedule.
    if variant=='two_sided_unobservable':
        observe_request_s=0.0
        event_s=delay_s+1.0
    over=build_overlay(pair,index,event_s,delay_s,3.0,.5,
                       observation_request_time_s=observe_request_s,
                       event_timing='fixed', observation_schedule='fixed')
    a,b=pair['a_anchor'],pair['b_anchor']
    w0,w1=over['world_support']
    if variant=='two_sided_observable':
        # W0: A available, B blocked. W1: A blocked, B available.
        # The observation of A at the preannounced 120s slot distinguishes
        # them, but a fixed B anchor fails in W0.
        w0['availability_trace'][b]=[[0,True],[event_s,False]]
    elif variant=='two_sided_unobservable':
        # Observation at t=125 gives the same A status in BOTH worlds. Both
        # worlds undergo different *future* outages before passenger arrival;
        # without further sensing or a safe alternative, no robust commitment.
        future=event_s
        w0['availability_trace'][b]=[[0,True],[future,False]]
        w1['availability_trace'][a]=[[0,True],[future,False]]
    elif variant=='both_fail_negative_control':
        w1['availability_trace'][b]=[[0,True],[event_s,False]]
    elif variant=='late_event_negative_control':
        future=1000000.0
        w1['availability_trace'][a]=[[0,True],[future,False]]
    over['overlay_id']+=f':{variant}'
    over['provenance']['outage_before_earliest_direct_arrival']=bool(
        event_s < min(float(pair['distances_m']['origin_to_a']),
                      float(pair['distances_m']['origin_to_b']))+3.0)

    over['motivation_witness']['vehicle_a_to_b_verified_for_event_time']=False
    over['provenance']['dynamic_event']=f'CONTROLLED_FALSIFICATION_{variant}_NOT_OBSERVED'
    over['provenance']['counterfactual_intervention_family']=variant
    over['provenance']['observation_schedule_design']='fixed_exogenous_not_future_trace'
    over['provenance']['world_identity_hidden']=True
    over['provenance']['nonanticipativity_test']='worlds with identical observation histories must share actions'
    validate_temporal_overlay(over)
    return over


def write_suite(pairs_path,output,max_site_pairs=20):
    # One fixture per distinct site pair to avoid dominance by repeated scene.
    seen=set();src=[]
    for pair in rows(pairs_path):
        sites=pair.get('physical_site_pair')
        key=tuple(sorted(map(str,sites))) if isinstance(sites,list) and len(sites)==2 and all(sites) else (
            pair['episode_id'],pair['a_anchor'],pair['b_anchor'])
        if key in seen:continue
        seen.add(key);src.append(pair)
        if len(src)>=max_site_pairs:break
    variants=['one_sided','two_sided_observable','two_sided_unobservable',
              'both_fail_negative_control','late_event_negative_control']
    dest=Path(output);dest.parent.mkdir(parents=True,exist_ok=True)
    with dest.open('w',encoding='utf-8') as f:
        for j,pair in enumerate(src):
            for variant in variants:
                f.write(json.dumps(make_variant(pair,j,variant),ensure_ascii=False)+'\n')
    manifest={'source_site_pairs_in_test':len(src),'overlays':len(src)*len(variants),
              'variant_families':variants,'evidence_class':'CONTROLLED_SYNTHETIC_EVENTS',
              'measurement_or_incidence_claim_permitted':False,
              'exogenous_observation_request_s':120.0,
              'observation_completion_s':125.0,
              'motion_verified':False,
              'warning':'A theoretical adversarial control, NOT an achieved research contribution. Some variants are unrecoverable.'}
    dest.with_suffix('.manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return manifest


def main():
    p=argparse.ArgumentParser();p.add_argument('--pairs',required=True);p.add_argument('--output',required=True)
    p.add_argument('--max_site_pairs',type=int,default=20)
    a=p.parse_args();print(json.dumps(write_suite(a.pairs,a.output,a.max_site_pairs),ensure_ascii=False))

if __name__=='__main__':main()
