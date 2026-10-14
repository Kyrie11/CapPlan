#!/usr/bin/env python3
"""Pre-register control PUDO anchors without turning benchmark labels into truth.

For each selected geometric A/B pair choose comparison anchors from the same
scene (when present): one dataset-legal and one dataset-illegal/unknown. These
are *audit tasks*, not measured legal stop or observed accessibility labels.
"""
from __future__ import annotations
import argparse,csv,sys,json
from collections import defaultdict
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from capplan.handoff.gate_evidence import records,dump_json,site_key

FIELDS=['episode_id','city','anchor_id','physical_site_key','dataset_legal_stop',
        'control_stratum','data_truth_mode','reason_selected','review_decision',
        'external_source_uri','reviewer','curb_connector_checked','legal_stop_checked',
        'boarding_interface_checked','notes']


def build(dataset_dir,selected_pairs,output):
    src=list(records(selected_pairs))
    episodes={p['episode_id'] for p in src}
    selected_by_episode=defaultdict(set)
    for p in src:selected_by_episode[p['episode_id']].update([p['a_anchor'],p['b_anchor']])
    available=defaultdict(lambda: {'positive':[],'negative_or_unknown':[]})
    for p in records(Path(dataset_dir)/'pudo_anchors.jsonl'):
        ep=p.get('episode_id')
        if ep not in episodes or p.get('anchor_id') in selected_by_episode[ep]:continue
        if p.get('kind') not in ('pickup','pickup_dropoff'):continue
        k='positive' if p.get('legal_stop') is True else 'negative_or_unknown'
        available[ep][k].append(p)
    out=Path(output);out.parent.mkdir(parents=True,exist_ok=True)
    unique=[];seen=set();strata=defaultdict(int)
    for p in src:
        ep=p['episode_id'];s=site_key(p)
        for group in ('positive','negative_or_unknown'):
            pool=sorted(available[ep][group],key=lambda a:str(a.get('anchor_id')))
            picked=next((a for a in pool if (ep,a.get('anchor_id')) not in seen and
                         a.get('hybrid_physical_site_key') not in (s or ())),None)
            if picked is None:continue
            seen.add((ep,picked['anchor_id']));strata[group]+=1
            unique.append({'episode_id':ep,'city':p.get('map_name'),'anchor_id':picked['anchor_id'],
                'physical_site_key':picked.get('hybrid_physical_site_key'),
                'dataset_legal_stop':picked.get('legal_stop'),
                'control_stratum':group,'data_truth_mode':picked.get('truth_mode'),
                'reason_selected':'same_episode_comparator_NOT_independently_truth_checked',
                'review_decision':'PENDING','curb_connector_checked':'UNKNOWN',
                'legal_stop_checked':'UNKNOWN','boarding_interface_checked':'UNKNOWN'})
    with out.open('w',encoding='utf-8',newline='') as f:
        w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader();w.writerows(unique)
    meta={'audit_only_controls':len(unique),'by_dataset_stratum':dict(strata),
          'independently_verified_positive_controls':0,'independently_verified_negative_controls':0,
          'warning':'Dataset legal_stop labels may be simulated or derived; control strata are study design, not truth labels.'}
    dump_json(out.with_suffix('.manifest.json'),meta)
    return meta

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--dataset_dir',required=True)
    p.add_argument('--selected_pairs',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();print(json.dumps(build(a.dataset_dir,a.selected_pairs,a.output),ensure_ascii=False))
