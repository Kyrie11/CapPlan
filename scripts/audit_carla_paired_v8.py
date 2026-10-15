#!/usr/bin/env python3
"""Independent post-hoc matched CARLA run diagnostic, without physical claims."""
from __future__ import annotations
import argparse,collections,hashlib,json,math,zipfile
from pathlib import Path


def check_trace(report,raw_trace):
    rows=[json.loads(line) for line in raw_trace.splitlines() if line.strip()]
    correct_hash=hashlib.sha256(raw_trace).hexdigest()==report.get('trajectory_file_sha256')
    if not rows:return {'valid':False,'reason':'EMPTY_TRACE'}
    stamps=[float(r['elapsed_s']) for r in rows]
    increasing=all(a<b for a,b in zip(stamps,stamps[1:]))
    distances=[float(r['distance_to_goal_m']) for r in rows]
    idx=min(range(len(rows)),key=lambda i:distances[i]);near=rows[idx]
    last=rows[-1]
    zone_radius=float(report.get('geometry_parameters',{}).get('arrival_radius_m',4.))
    zone_entered=any(x<=zone_radius+1e-6 for x in distances)
    speed=float(near['speed_mps'])
    return {'valid':correct_hash and increasing,'sha256_ok':correct_hash,'timestamps_ok':increasing,
            'min_distance_m':distances[idx],'min_distance_time_s':near['elapsed_s'],
            'speed_at_closest_mps':speed,'distance_at_end_m':distances[-1],
            'passed_target_region':idx<len(rows)-5 and distances[-1]>distances[idx]+1,
            'goal_zone_entered_recomputed':zone_entered,
            'goal_zone_flag_consistent':zone_entered==bool(report.get('reached_goal_zone')),
            'scripted_brake_ever_active':any(r.get('scripted_braking') is True for r in rows),
            'stopped_dwell_in_report_s':report.get('service_stop_best_dwell_s',0),
            'at_minimum_vehicle_xy':[near.get('x'),near.get('y')],
            'last_speed_mps':last.get('speed_mps')}


def audit_zip(src,dest):
    summaries=[];status=collections.Counter()
    with zipfile.ZipFile(src) as z:
        entries=set(z.namelist())
        summary_name=next((x for x in entries if x.endswith('/paired_summary.json') or x=='paired_summary.json'),None)
        if not summary_name:raise ValueError('No paired_summary.json in source archive')
        original=json.loads(z.read(summary_name));prefix=summary_name[:-len('paired_summary.json')]
        for t in original.get('trials',[]):
            entry={'pair_id':t.get('pair_id'),'recorded_status':t.get('status'),'controls':{}}
            for mode in ('clear_A','blocked_A','blocked_A_redirect_B'):
                pair=t.get('geometry',{})
                stub=f"{prefix}pair_{t['pair_id']:02d}_spawn{pair.get('spawn_index')}_asteps{pair.get('a_steps')}/{mode}/"
                rp=stub+'execution_report.json';tp=stub+'trajectory.jsonl'
                if rp not in entries or tp not in entries:
                    entry['controls'][mode]={'present':False};continue
                report=json.loads(z.read(rp));diag=check_trace(report,z.read(tp));diag['present']=True
                diag['report_stationary_stop']=report.get('stationary_service_stop_observed')
                entry['controls'][mode]=diag
            clear=entry['controls']['clear_A']
            if not clear.get('present') or not clear.get('valid'):
                conclusion='INVALID_CLEAR_DATA'
            elif not clear.get('report_stationary_stop'):
                conclusion='BASELINE_CLEAR_SERVICE_STOP_NOT_ESTABLISHED'
            elif not all(entry['controls'][m].get('present') and entry['controls'][m].get('valid') for m in ('blocked_A','blocked_A_redirect_B')):
                conclusion='BLOCKER_AND_REDIRECT_COMPARISON_MISSING'
            else:
                conclusion='RUNS_PRESENT_REQUIRES_INDEPENDENT_MOTION_SAFETY_AND_CURB_AUDIT'
            entry['causal_status']=conclusion;status[conclusion]+=1;summaries.append(entry)
    payload={'summary':dict(status),'trials':summaries,'claim_boundary':'CARLA Town vehicle observations only; no real PUDO or verified passenger interface'}
    out=Path(dest);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n')
    return payload


def main():
    p=argparse.ArgumentParser();p.add_argument('--carla_zip',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();r=audit_zip(a.carla_zip,a.output);print(json.dumps(r['summary']))
if __name__=='__main__':main()
