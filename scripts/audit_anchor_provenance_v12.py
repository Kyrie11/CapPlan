#!/usr/bin/env python3
"""Full-corpus PUDO provenance field audit, preserving UNKNOWN and split context.

Streams JSONL, does not assume field names or treat any hybrid-simulated fact as
independent real-world truth. Outputs split/city/site breakdown and a review CSV.
"""
from __future__ import annotations
import argparse,csv,json,hashlib
from pathlib import Path
from collections import Counter,defaultdict

FIELDS=('legal_stop','adjacent_ped_node_id','curb_height_m','sidewalk_width_m',
        'deployment_clearance_m','side','stop_pose','curb_pose','blockage_risk')

def provenance(value):
    if value is None:return 'missing'
    s=str(value).lower()
    if any(tok in s for tok in ('simulated','synthetic','hybrid_simulated')):return 'simulated'
    if 'derived' in s or 'inferred' in s or 'proxy' in s:return 'derived_or_proxy'
    if any(tok in s for tok in ('observed','measured','survey','audited')):return 'observed_claim_unverified'
    return 'other_or_unlabeled'

def classify_anchor(a):
    fp=a.get('field_provenance')
    out={}
    for k in FIELDS:
        val=a.get(k)
        meta=fp.get(k) if isinstance(fp,dict) else None
        if isinstance(meta,dict): meta=meta.get('source') or meta.get('kind') or meta.get('provenance') or str(meta)
        out[k]=('missing' if val is None else provenance(meta if meta is not None else a.get('truth_mode')))
    return out

def audit(paths,review_csv=None):
    counts=Counter(); prov=defaultdict(Counter);sites=defaultdict(lambda:defaultdict(set))
    review=[];examples=defaultdict(list)
    for path in paths:
        p=Path(path)
        if not p.is_file():raise FileNotFoundError(p)
        with p.open(encoding='utf8') as f:
            for line in f:
                if not line.strip():continue
                a=json.loads(line);ep=str(a.get('episode_id'))
                city=str(a.get('hybrid_physical_site_key') or a.get('city') or 'unknown').split('|')[0]
                site=str(a.get('hybrid_physical_site_key') or '')
                aid=str(a.get('anchor_id') or '')
                if not ep or not aid:raise ValueError('PUDO row missing episode or anchor identity')
                group=f'{p.parent.name}:{city}'; counts['anchors_total']+=1;counts[group]+=1
                for k,v in classify_anchor(a).items():prov[k][v]+=1
                if site:sites[group][site].add(ep)
                # Deterministic review priority: missing interfaces, never invented certainty.
                miss=[k for k in ('deployment_clearance_m','curb_height_m','sidewalk_width_m','stop_pose') if a.get(k) is None]
                if miss:
                    key=hashlib.sha256(f'{ep}|{aid}'.encode()).hexdigest()
                    examples[group].append((key,{'episode_id':ep,'anchor_id':aid,'physical_site_key':site,
                        'city':city,'missing_fields':';'.join(miss), 'review_status':'PENDING_INDEPENDENT_EVIDENCE'}))
    if review_csv:
        for group,items in examples.items():
            for _,item in sorted(items)[:min(25,len(items))]: review.append(item)
        with Path(review_csv).open('w',encoding='utf8',newline='') as f:
            w=csv.DictWriter(f,fieldnames=['episode_id','anchor_id','physical_site_key','city','missing_fields','review_status'])
            w.writeheader();w.writerows(review)
    return {'counts':dict(counts),'provenance_by_field':{k:dict(v) for k,v in prov.items()},
       'physical_sites_by_directory_and_city':{k:len(v) for k,v in sites.items()},
       'cross_episode_sites_by_directory_and_city':{k:sum(len(v)>1 for v in vv.values()) for k,vv in sites.items()},
       'audit_sample_size':len(review),'real_world_ground_truth_certified':False,
       'note':'Observed labels in the source are claims, not automatically independently validated physical truth.'}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--pudo_jsonl',nargs='+',required=True)
    p.add_argument('--output',required=True);p.add_argument('--review_csv')
    a=p.parse_args();out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True)
    s=audit(a.pudo_jsonl,a.review_csv);out.write_text(json.dumps(s,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(s['counts'],ensure_ascii=False))
if __name__=='__main__':main()
