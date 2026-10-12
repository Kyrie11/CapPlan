#!/usr/bin/env python3
"""Export EXACT city/anchor/pose requests for an independent motion checker.

The output is an evaluation request, NOT a vehicle verification result.  A
checker must execute the selected PUDO trajectory and supply the required
motion packet, including the full motion interval, safety and interface tests.
"""
import argparse,json
from pathlib import Path


def rows(path):
    with Path(path).open(encoding='utf-8') as f:
        for line in f:
            if line.strip():yield json.loads(line)


def export(pairs,target):
    out=Path(target);out.parent.mkdir(parents=True,exist_ok=True)
    n=0;missing=0;seen=set()
    with out.open('w',encoding='utf-8') as f:
        for p in rows(pairs):
            key=(p['episode_id'],p['a_anchor'],p['b_anchor'])
            if key in seen:continue
            seen.add(key)
            poses=p.get('anchor_vehicle_poses',{})
            if not poses.get('A_stop_pose') or not poses.get('B_stop_pose'):
                missing+=1
            request={'episode_id':p['episode_id'],'scenario_token':p.get('scenario_token'),
              'map_name':p.get('map_name'),'from_anchor':p['a_anchor'],'to_anchor':p['b_anchor'],
              'anchor_vehicle_poses':poses,'traffic_event_s':p['event']['event_time_s'],
              'required_motion_checks':['actual executed trajectory','collision_free','legal_stop',
                'interface_verified','time_window_coverage'],
              'status':'UNVERIFIED_REQUEST_NOT_EVIDENCE'}
            f.write(json.dumps(request,ensure_ascii=False)+'\n');n+=1
    manifest={'requests':n,'missing_stop_poses':missing,'status':'NO_MOTION_RESULT',
      'required_external_result_schema':'configs/handoff_motion_evidence.example.jsonl',
      'note':'Do not mark verified until method-specific planner has run with original nuPlan scene/map plus actual PUDO constraints'}
    out.with_suffix('.manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(manifest))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--pairs',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();export(a.pairs,a.output)
