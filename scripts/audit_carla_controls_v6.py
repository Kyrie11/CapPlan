#!/usr/bin/env python3
"""Fail-closed paired validation of existing CARLA experiment reports/trajectory.

If the no-obstacle A control fails, DO NOT infer blockage effects from blocked A.
"""
import argparse,hashlib,json,math,zipfile
from pathlib import Path

MODES=('clear_A','blocked_A','blocked_A_redirect_B')
def load_bundle(folder_or_zip):
    p=Path(folder_or_zip)
    if p.is_dir():
        def read(mode,file):return (p/mode/file).read_bytes()
    else:
        zipf=zipfile.ZipFile(p)
        names=zipf.namelist()
        def read(mode,file):
            matches=[x for x in names if x.endswith('/'+mode+'/'+file)]
            if len(matches)!=1:raise ValueError(f'expected one {mode}/{file}, found {len(matches)}')
            return zipf.read(matches[0])
    return read

def audit(source):
    read=load_bundle(source);results={};problems=[]
    for mode in MODES:
        payload=json.loads(read(mode,'execution_report.json'))
        data=read(mode,'trajectory.jsonl');rows=[json.loads(x) for x in data.splitlines() if x.strip()]
        hash_ok=hashlib.sha256(data).hexdigest()==payload.get('trajectory_file_sha256')
        final=rows[-1] if rows else {}
        gaps=[float(x['distance_to_goal_m']) for x in rows]
        monotone=all(rows[i]['elapsed_s']>rows[i-1]['elapsed_s'] for i in range(1,len(rows)))
        observed_goal=payload.get('goal');arrival=bool(payload.get('reached_goal_zone'))
        # A successful 'zone' entry need not be a stop. Log that explicitly.
        near_speed=final.get('speed_mps')
        results[mode]={'hash_ok':hash_ok,'trajectory_rows':len(rows),'status':payload.get('status'),
          'goal':observed_goal,'reached_goal_zone':arrival,'minimum_gap_m':min(gaps) if gaps else None,
          'final_speed_mps':near_speed,'collision_sensor_events':payload.get('collisions'),
          'elapsed_sim_s':payload.get('elapsed_sim_s'),'timestamps_monotone':monotone,
          'real_pudo_or_boarding_verified':bool(payload.get('curb_legality_verified') and payload.get('passenger_boarding_verified')),
          'carla_locations':payload.get('carla_locations'),'ego_other_vehicles':payload.get('other_vehicles_at_start')}
        if not hash_ok:problems.append('TRAJECTORY_HASH_MISMATCH_'+mode)
        if not monotone or not rows:problems.append('INVALID_TRAJECTORY_'+mode)
        if payload.get('status')!='COMPLETED':problems.append('INCOMPLETE_RUN_'+mode)
    a=results['clear_A'];b=results['blocked_A'];c=results['blocked_A_redirect_B']
    comparable=(a['carla_locations']==b['carla_locations']==c['carla_locations'] and
                all(x['ego_other_vehicles']==0 for x in results.values()))
    if not comparable:problems.append('PAIRED_SCENE_NOT_CONTROLLED')
    if not a['reached_goal_zone']:problems.append('INVALID_CLEAR_A_CONTROL')
    # Distinguish reaching zone from stopped legally at curb. No physical proof.
    causal_blockage_evidence=bool(comparable and a['reached_goal_zone'] and not b['reached_goal_zone']
        and not problems and c['reached_goal_zone'])
    return {'runs':results,'paired_geometry_and_background_comparable':comparable,
      'clear_A_control_valid':bool(a['reached_goal_zone'] and a['hash_ok'] and a['timestamps_monotone']),
      'blocked_A_causal_effect_established':causal_blockage_evidence,
      'redirect_B_goal_zone_observed':bool(c['reached_goal_zone']),
      'controlled_vehicle_only':True,'independent_physical_pudo_verified':False,
      'scientific_status':'BLOCKED_BASELINE_CONTROL_FAILURE' if not a['reached_goal_zone'] else 'CONTROL_VALID_REQUIRES_MORE_VERIFICATION',
      'issues':problems,'research_boundary':'Not matched to nuPlan episode, real curb, stop posture, passenger arrival or accessible boarding.'}

def main():
    p=argparse.ArgumentParser();p.add_argument('--carla_dir_or_zip',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();result=audit(a.carla_dir_or_zip);f=Path(a.output);f.parent.mkdir(parents=True,exist_ok=True);f.write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n');print(json.dumps({'status':result['scientific_status'],'issues':result['issues']}))
if __name__=='__main__':main()
