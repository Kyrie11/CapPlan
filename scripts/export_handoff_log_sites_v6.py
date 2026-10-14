#!/usr/bin/env python3
"""City-balanced, distinct-episode and physical-pair sample of GIS pose proxies.
Export is not a measured site or a simulated stop. Consumes mining pair JSONL.
"""
from __future__ import annotations
import argparse,json,random,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from collections import defaultdict
from scripts.nuplan_log_bind_v6 import classify_map,records

def export(pairs,episodes,output,per_city=8,seed=13):
    pools=defaultdict(dict)
    for pair in records(pairs):
        ep_id=pair.get('episode_id')
        city=classify_map(pair.get('map_name'));key=pair.get('physical_site_pair')
        poses=pair.get('anchor_vehicle_poses') or {}
        if not city or not isinstance(key,(list,tuple)) or len(key)<2:continue
        if not poses.get('A_stop_pose') or not poses.get('B_stop_pose'):continue
        if any(poses[a+'_stop_pose'].get('frame')!='map' for a in ('A','B')):continue
        # One distinct episode per city, no repeated pairs; avoid first-N selection bias.
        if ep_id not in pools[city]:pools[city][ep_id]=pair
    requested={ep_id for v in pools.values() for ep_id in v}
    eps={}
    for r in records(episodes):
        if r.get('episode_id') in requested:
            eps[r['episode_id']]={k:r.get(k) for k in ('episode_id','log_name','scenario_token','map_name')}
    rand=random.Random(seed);sites=[];selected=defaultdict(int)
    for city,episodes_map in sorted(pools.items()):
        episode_ids=sorted(episodes_map);rand.shuffle(episode_ids)
        seen_physical_pairs=set()
        for ep_id in episode_ids:
            if selected[city]>=per_city:break
            if ep_id not in eps:continue
            pair=episodes_map[ep_id];ep=eps[ep_id];poses=pair['anchor_vehicle_poses'];key=pair['physical_site_pair']
            physical_pair=tuple(sorted(str(x) for x in key))
            if physical_pair in seen_physical_pairs:continue
            seen_physical_pairs.add(physical_pair)
            for side,index in (('A',0),('B',1)):
                pose=poses[side+'_stop_pose']
                sites.append({'site_id':ep_id+':'+side,'episode_id':ep_id,'city':city,
                  'map_name':ep['map_name'],'log_name':ep.get('log_name'),'scenario_token':ep.get('scenario_token'),
                  'pose':{'x':pose['x'],'y':pose['y']},'site_key':key[index],
                  'evidence_status':'UNVERIFIED_GIS_POSE_PROXY',
                  'research_boundary':'not a legal stopping place or observed passenger access'})
            selected[city]+=1
    out=Path(output);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in sites))
    report={'sampled_episodes_by_city':dict(selected),'selected_site_rows':len(sites),
      'source':'selected mined pairs only; NOT unscreened population prevalence',
      'expected_episode_metadata_required':True}
    out.with_suffix('.summary.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
    return report

def main():
    p=argparse.ArgumentParser();p.add_argument('--pairs',required=True);p.add_argument('--episodes',required=True)
    p.add_argument('--output',required=True);p.add_argument('--per_city',type=int,default=8);p.add_argument('--seed',type=int,default=13)
    a=p.parse_args();print(json.dumps(export(a.pairs,a.episodes,a.output,a.per_city,a.seed)))
if __name__=='__main__':main()
