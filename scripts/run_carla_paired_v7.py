#!/usr/bin/env python3
"""Matched, *vehicle-only* CARLA 0.9.15 control with fail-closed stopping gate.

No station-level reality or human boarding is asserted. Do not use calibrated
zone-entry as physical handoff. A trial with invalid Clear-A is not a blocker
causal contrast. Only one instance should tick a CARLA server at a time.
"""
from __future__ import annotations
import argparse, hashlib, json, math, subprocess, sys
from pathlib import Path

MODES=('clear_A','blocked_A','blocked_A_redirect_B')

def trace_dwell(rows, radius_m, speed_limit_mps, target):
    """Independent-from-report *trace-consistency* check, not physical proof.

    Uses the CARLA trace's own sampled times and target to rederive dwell.
    We accept the final sample's dt as a documented fixed-step approximation.
    """
    if not rows or not (math.isfinite(radius_m) and radius_m > 0 and
                        math.isfinite(speed_limit_mps) and speed_limit_mps > 0):
        return 0.0
    intervals=[float(rows[i]['elapsed_s'])-float(rows[i-1]['elapsed_s']) for i in range(1,len(rows))]
    if not intervals or any(not math.isfinite(dt) or dt<=0 for dt in intervals):return 0.0
    dt=min(intervals);run=best=0.0
    for i,r in enumerate(rows):
        try:
            inside=(str(r['target'])==target and
                    0<=float(r['distance_to_goal_m'])<=radius_m+1e-5 and
                    0<=float(r['speed_mps'])<=speed_limit_mps+1e-5)
        except (KeyError,ValueError,TypeError):return 0.0
        run=run+(intervals[i-1] if i else dt) if inside else 0.0
        best=max(best,run)
    return best


def validate_run(output_dir,mode,hold_s):
    p=Path(output_dir);report=p/'execution_report.json';trace=p/'trajectory.jsonl'
    if not report.exists() or not trace.exists():return {'mode':mode,'valid':False,'status':'OUTPUT_MISSING'}
    d=json.loads(report.read_text());blob=trace.read_bytes()
    digest=hashlib.sha256(blob).hexdigest()==d.get('trajectory_file_sha256')
    rows=[json.loads(r) for r in blob.splitlines() if r.strip()]
    stable=(bool(rows) and all(math.isfinite(float(r.get('elapsed_s',float('nan')))) for r in rows) and
            all(rows[i]['elapsed_s']>rows[i-1]['elapsed_s'] for i in range(1,len(rows))))
    params=d.get('geometry_parameters') or {}
    radius=float(params.get('arrival_radius_m') or float('nan'))
    stop_speed=float(d.get('stop_speed_threshold_mps') or float('nan'))
    target=d.get('goal')
    measured=trace_dwell(rows,radius,stop_speed,target) if stable else 0.0
    hold=float(d.get('service_stop_best_dwell_s') or 0)
    stop=bool(d.get('stationary_service_stop_observed') and hold+1e-5>=hold_s and
              measured+1e-4>=hold_s)
    clean=bool(d.get('collisions')==0)
    matched=bool(d.get('mode')==mode and d.get('status')=='COMPLETED' and digest and stable and len(rows)>0)
    return {'mode':mode,'valid':matched,'status':d.get('status'),'sha256_ok':digest,
            'timestamps_ok':stable,'stop_with_dwell':stop,
            'dwell_derived_from_trace_s':round(measured,4),
            'goal_zone_entered':d.get('reached_goal_zone'),
            'collision_events':d.get('collisions'),'no_collision_sensor_events_observed':clean,
            'geometry':d.get('carla_locations'),'parameters':d.get('geometry_parameters'),
            'elapsed_sim_s':d.get('elapsed_sim_s'),'trajectory_rows':len(rows),'dwell_s':hold,
            'goal':d.get('goal'),'scene_empty_at_start':d.get('other_vehicles_at_start')==0,
            'output_dir':str(p),'traffic_signals_controlled':d.get('traffic_signals_controlled')}

def evaluate_triplet(runs,required_hold_s):
    a,b,c=(runs[m] for m in MODES)
    shared=all(x['valid'] and x['scene_empty_at_start'] for x in runs.values()) and (
         a['geometry']==b['geometry']==c['geometry'] and a['parameters']==b['parameters']==c['parameters'])
    clear_valid=bool(a['valid'] and a['stop_with_dwell'] and a['collision_events']==0)
    blocked_difference=bool(shared and clear_valid and b['valid'] and
         not b['stop_with_dwell'] and b['collision_events']==0)
    recovered=bool(c['valid'] and c['stop_with_dwell'] and c['collision_events']==0 and c['goal']=='B')
    if not clear_valid:status='INVALID_CLEAR_STOP_CONTROL'
    elif not shared:status='UNMATCHED_OR_INCOMPLETE_SCENE'
    elif b['collision_events']!=0:status='BLOCKED_RUN_COLLISION_OR_UNSAFE'
    elif not blocked_difference:status='NO_ISOLATED_BLOCKER_EFFECT'
    elif not recovered:status='VEHICLE_REDIRECTION_UNVERIFIED'
    else:status='CARLA_VEHICLE_ONLY_CONTROLLED_REDIRECTION'
    signal_controlled=all(x.get('traffic_signals_controlled') is True for x in runs.values())
    if status=='CARLA_VEHICLE_ONLY_CONTROLLED_REDIRECTION' and not signal_controlled:
        status='VEHICLE_REDIRECTION_OBSERVED_BUT_SIGNAL_PHASE_NOT_CONTROLLED'
    return {'status':status,'clear_A_valid_stop':clear_valid,'controlled_same_geometry':shared,
            'blocked_vehicle_stop_difference':blocked_difference,'vehicle_redirect_B_stop':recovered,
            'independently_verified_curb_or_passenger':False,
            'H1_H2_H3_claims':'NONE_FROM_CARLA_ALONE',
            'research_limit':'Native CARLA map with scripted braking and lane-center blocker; no actual nuPlan PUDO, pedestrian interface, or passenger.'}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--calibration',required=True);p.add_argument('--output_dir',required=True)
    p.add_argument('--host',default='127.0.0.1');p.add_argument('--port',type=int,default=2000)
    p.add_argument('--map',default='Town03');p.add_argument('--max_trials',type=int,default=4)
    p.add_argument('--seed',type=int,default=13);p.add_argument('--b_steps',type=int,default=8)
    p.add_argument('--speed_kmh',type=float,default=8);p.add_argument('--radius_m',type=float,default=4)
    p.add_argument('--stop_hold_s',type=float,default=2);p.add_argument('--dt',type=float,default=.05)
    p.add_argument('--max_seconds',type=float,default=50);p.add_argument('--redirect_at_s',type=float,default=5)
    p.add_argument('--dry_run',action='store_true')
    a=p.parse_args()
    if any(not math.isfinite(v) or v<=0 for v in (a.speed_kmh,a.radius_m,a.stop_hold_s,a.dt,a.max_seconds)) or a.max_trials<1:
        p.error('invalid positive parameters')
    out=Path(a.output_dir);out.mkdir(parents=True,exist_ok=True)
    candidate_data=json.loads(Path(a.calibration).read_text());candidate_data=candidate_data.get('calibrated_goal_zone_candidates',[])
    if not candidate_data:raise ValueError('No calibrated goal-zone candidates. Cannot start paired trials.')
    pairs=[]
    for i,candidate in enumerate(candidate_data[:a.max_trials]):
        spawn=int(candidate['spawn_index']);astep=int(candidate['a_steps'])
        base=out/f'pair_{i:02d}_spawn{spawn}_asteps{astep}'
        command_base=[sys.executable,str(Path(__file__).with_name('run_carla_controlled_handoff_v5.py')),
            '--host',a.host,'--port',str(a.port),'--map',a.map,
            '--spawn_index',str(spawn),'--a_steps',str(astep),'--b_steps',str(a.b_steps),
            '--seed',str(a.seed),'--target_speed_kmh',str(a.speed_kmh),
            '--arrival_radius_m',str(a.radius_m),'--max_seconds',str(a.max_seconds),
            '--service_stop_hold_s',str(a.stop_hold_s),'--dt',str(a.dt),
            '--redirect_at_s',str(a.redirect_at_s),'--freeze_traffic_lights']
        plan={'pair_id':i,'geometry':{'spawn_index':spawn,'a_steps':astep,'b_steps':a.b_steps},
              'parameters':{'seed':a.seed,'speed_kmh':a.speed_kmh,'radius_m':a.radius_m,
                            'stop_hold_s':a.stop_hold_s,'redirect_at_s':a.redirect_at_s},'runs':{}}
        for mode in MODES:
            # In a fail-closed study, do not run induced obstruction when the
            # clear condition has not first achieved the required service stop.
            if mode!='clear_A' and not plan['runs']['clear_A'].get('stop_with_dwell'):
                plan['status']='INVALID_CLEAR_STOP_CONTROL';break
            dest=base/mode;cmd=command_base+['--mode',mode,'--output_dir',str(dest)]
            if a.dry_run:
                plan['runs'][mode]={'mode':mode,'valid':False,'status':'DRY_RUN_NO_SIMULATION','command':cmd}
                plan['status']='DRY_RUN';break
            proc=subprocess.run(cmd,capture_output=True,text=True)
            rr=validate_run(dest,mode,a.stop_hold_s);rr['returncode']=proc.returncode
            rr['stderr_tail']=proc.stderr[-500:] if proc.returncode else ''
            plan['runs'][mode]=rr
            if proc.returncode or not rr.get('valid'):
                plan['status']='EXECUTION_FAILED';break
        if set(plan['runs'])==set(MODES):
            plan['assessment']=evaluate_triplet(plan['runs'],a.stop_hold_s)
            plan['status']=plan['assessment']['status']
        pairs.append(plan)
        (out/'paired_summary.json').write_text(json.dumps({'trials':pairs,'research_boundary':'CARLA native town vehicle-only; NOT real PUDO, legal curb, or passenger'},ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'trials':len(pairs),'statuses':[x.get('status') for x in pairs]}))

if __name__=='__main__':main()
