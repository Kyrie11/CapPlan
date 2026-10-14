#!/usr/bin/env python3
"""Preregister an UNSCREENED episode cohort, blind to A/B failure labels.

The denominator is the already-retained hybrid benchmark, not natural rides.
Weights describe this finite cohort, never dynamic event incidence or risk.
"""
from __future__ import annotations
import argparse,hashlib,json,sys
from collections import defaultdict
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from capplan.handoff.gate_evidence import records,dump_json,city

def run(dataset_dir,output,per_city=20,seed=13):
    if per_city<=0:raise ValueError('per_city must be positive')
    src=Path(dataset_dir)/'episodes.jsonl'
    groups=defaultdict(list)
    seen=set()
    for e in records(src):
        ep=str(e['episode_id'])
        if ep in seen:raise ValueError('duplicate episode identity in cohort')
        seen.add(ep)
        c=city(e)
        key=hashlib.sha256(f'{seed}:{ep}'.encode()).hexdigest()
        groups[c].append((key,e))
    chosen=[];denom={};selection={}
    for c,rows in sorted(groups.items()):
        n=len(rows);take=min(per_city,n);denom[c]=n;selection[c]=take
        for _,row in sorted(rows,key=lambda t:t[0])[:take]:
            chosen.append({'episode_id':row['episode_id'],'map_name':row.get('map_name'),
               'split':row.get('split'),'city':c,'finite_cohort_weight':n/take if take else None,
               'sampling_design':'precommitted_hash_uniform_within_city_before_failure_screening'})
    out=Path(output);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in chosen),encoding='utf-8')
    d={'retained_episode_cohort_by_city':denom,'selected_by_city':selection,'seed':seed,
       'screening_blind':True,'scope':'finite retained hybrid episode sampling, not population or event-frequency estimate',
       'no_positive_or_negative_ground_truth_assigned':True}
    dump_json(out.with_suffix('.manifest.json'),d);return d

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--dataset_dir',required=True);a.add_argument('--output',required=True)
    a.add_argument('--per_city',type=int,default=20);a.add_argument('--seed',type=int,default=13)
    x=a.parse_args();print(json.dumps(run(x.dataset_dir,x.output,x.per_city,x.seed),ensure_ascii=False))
