#!/usr/bin/env python3
"""Export *unverified* trajectory-check requests, optionally site-diverse.

No exported request is a vehicle-motion result. A selected candidate's event
clock is only one of potentially many sidecar intervention times.
"""
import argparse,hashlib,json
from collections import defaultdict,Counter
from pathlib import Path


def rows(path):
    with Path(path).open(encoding='utf-8') as f:
        for line in f:
            if line.strip():yield json.loads(line)


def select_diverse(pairs,max_per_city=12,max_per_site=1):
    best={};scanned=0
    for p in rows(pairs):
        scanned+=1
        sites=p.get('physical_site_pair')
        key=tuple(sorted(map(str,sites))) if isinstance(sites,list) and len(sites)==2 and all(sites) else (
            p['episode_id'],p['a_anchor'],p['b_anchor'])
        curb=p.get('explicit_curb_connector_evidence') or {}
        pri=(int(bool(curb.get('A')) and bool(curb.get('B'))),
             int(bool(p.get('interface_completeness_claim'))),
             min(1000.,float(p.get('distance_excess_m',0))),
             hashlib.sha256(str(p['episode_id']).encode()).hexdigest())
        if key not in best or pri>best[key][0]:best[key]=(pri,p)
    by_city=defaultdict(list)
    for pri,p in best.values():by_city[p.get('map_name','UNKNOWN')].append((pri,p))
    selected=[];occupied=Counter()
    for city in sorted(by_city):
        n=0
        for pri,p in sorted(by_city[city],key=lambda x:x[0],reverse=True):
            sites=p.get('physical_site_pair') or [p['episode_id'],p['a_anchor']]
            if any(occupied[str(x)]>=max_per_site for x in sites):continue
            selected.append(p);n+=1
            for x in sites:occupied[str(x)]+=1
            if n>=max_per_city:break
    return selected,{'all_pairs_scanned':scanned,'distinct_site_pairs_screened':len(best),
                     'site_diverse_selected':len(selected),'selection':'best candidate per site pair then city-balanced with site reuse cap'}


def export(pairs,target,diverse=False,max_per_city=12,max_per_site=1):
    out=Path(target);out.parent.mkdir(parents=True,exist_ok=True)
    if diverse:records,selection=select_diverse(pairs,max_per_city,max_per_site)
    else:records=rows(pairs);selection={'selection':'all unique episode-A-B triples'}
    n=0;missing=0;seen=set()
    with out.open('w',encoding='utf-8') as f:
        for p in records:
            key=(p['episode_id'],p['a_anchor'],p['b_anchor'])
            if key in seen:continue
            seen.add(key)
            poses=p.get('anchor_vehicle_poses',{})
            if not poses.get('A_stop_pose') or not poses.get('B_stop_pose'):missing+=1
            request={'episode_id':p['episode_id'],'scenario_token':p.get('scenario_token'),
              'map_name':p.get('map_name'),'from_anchor':p['a_anchor'],'to_anchor':p['b_anchor'],
              'anchor_vehicle_poses':poses,'traffic_event_s':p['event']['event_time_s'],
              'physical_site_pair':p.get('physical_site_pair'),
              'explicit_curb_connector_evidence':p.get('explicit_curb_connector_evidence'),
              'required_motion_checks':['method-specific actual trajectory','collision_free',
                'legal_stopping','interface_pose','full_event_window_coverage'],
              'status':'UNVERIFIED_REQUEST_NOT_EVIDENCE'}
            f.write(json.dumps(request,ensure_ascii=False)+'\n');n+=1
    manifest={**selection,'requests':n,'missing_stop_poses':missing,'status':'NO_MOTION_RESULT',
      'required_external_result_schema':'configs/handoff_motion_evidence.example.jsonl',
      'note':'Never mark verified until a method-specific executed motion and interface checker returns independent evidence.'}
    out.with_suffix('.manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(manifest))
    return manifest

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--pairs',required=True);p.add_argument('--output',required=True)
    p.add_argument('--diverse',action='store_true');p.add_argument('--max_per_city',type=int,default=12)
    p.add_argument('--max_per_site',type=int,default=1)
    a=p.parse_args();export(a.pairs,a.output,a.diverse,a.max_per_city,a.max_per_site)
