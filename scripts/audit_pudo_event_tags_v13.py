#!/usr/bin/env python3
"""Profile naturally tagged nuPlan pickup/pedestrian scenes before problem mining.

This script requires actual episode metadata with scenario tags. If tags are
missing, it explicitly reports unknown instead of assigning labels based on GIS
geometry or on handoff model outcomes. Does not query large SQLite databases.
"""
from __future__ import annotations
import argparse,json
from pathlib import Path
from collections import Counter,defaultdict

TAGS={
    'on_pickup_dropoff','behind_pedestrian_on_pickup_dropoff',
    'near_pedestrian_at_pickup_dropoff','traversing_pickup_dropoff',
}
KEYS=('scenario_type','scenario_tag','scenario_types','scenario_tags','type')


def tags(ep):
    possibilities=[ep,ep.get('metadata'),ep.get('scenario_metadata'),ep.get('nuplan_metadata')]
    found=[]
    for src in possibilities:
        if not isinstance(src,dict):continue
        for key in KEYS:
            val=src.get(key)
            if isinstance(val,str):found.append(val)
            elif isinstance(val,list):found.extend(x for x in val if isinstance(x,str))
    return set(found)


def analyze(file,preselected=None):
    selected=None
    if preselected:
        selected={json.loads(line)['episode_id'] for line in Path(preselected).read_text().splitlines() if line.strip()}
    counts=Counter();city=defaultdict(Counter);matches=[]
    with Path(file).open(encoding='utf8') as f:
        for line in f:
            if not line.strip():continue
            ep=json.loads(line);id_=ep['episode_id']
            if selected is not None and id_ not in selected:continue
            name=str(ep.get('map_name') or ep.get('metadata',{}).get('city') or 'unknown')
            t=tags(ep);counts['cohort']+=1;city[name]['total']+=1
            if not t:
                counts['missing_tags']+=1;city[name]['missing_tags']+=1
            else:
                counts['tagged']+=1
                if t&TAGS:
                    counts['pudo_exposure_tagged']+=1;city[name]['pudo_exposure_tagged']+=1
                    matches.append({'episode_id':id_,'map_name':name,'tags':sorted(t&TAGS)})
    return {'counts':dict(counts),'by_map':{k:dict(v) for k,v in city.items()},
      'pudo_tagged_episodes':matches,
      'tags_definition':'nuPlan published scenario taxonomy, not ground-truth handoff failure',
      'absence_of_tag_does_not_prove_absence_of_pudo_event':True,
      'missing_tag_means':'metadata not available; use keyed nuPlan DB scenario_tag when required'}


def main():
    p=argparse.ArgumentParser();p.add_argument('--episodes',required=True)
    p.add_argument('--cohort');p.add_argument('--output',required=True)
    a=p.parse_args();s=analyze(a.episodes,a.cohort)
    dest=Path(a.output);dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(json.dumps(s,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps(s['counts'],ensure_ascii=False))
if __name__=='__main__':main()
