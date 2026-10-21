"""Offline CARLA matched-control calibration and provenance audit.

No claims about roadside legality, nuPlan alignment, or safe boarding are
allowed. Only a completed, stationary, near-goal CARLA vehicle-stop SURROGATE
is evaluated. A blocked-A effect requires a *matched* successful clear-A
control and identical initial settings. No sensor collision callbacks does NOT
prove global collision freedom.
"""
from __future__ import annotations
from collections import Counter,defaultdict
from pathlib import Path
import hashlib,json,math


def _load_runs(root:Path,*,goal_radius=None,dwell_s=None,speed_mps=None):
    runs=[]
    if root is None:return runs
    for report_path in sorted(root.glob('**/execution_report.json')):
        report=json.loads(report_path.read_text())
        traj=report_path.with_name('trajectory.jsonl')
        trial=report_path.parent.name
        base={'trial':trial,'mode':report.get('mode'),
              'map':report.get('carla_map'),'report':str(report_path),
              'geometry_parameters':report.get('geometry_parameters',{}),
              'collision_events':report.get('collisions'),
              'vehicle_collision_free_independently_verified':False,
              'vehicle_service_surrogate_only':True,
              'nuplan_real_site_verified':False}
        if not traj.is_file():
            runs.append(dict(base,status='UNKNOWN_MISSING_TRAJECTORY',valid_clear=False));continue
        content=traj.read_bytes();sha=hashlib.sha256(content).hexdigest()
        if report.get('trajectory_file_sha256') and report['trajectory_file_sha256']!=sha:
            runs.append(dict(base,status='INVALID_TRACE_HASH',valid_clear=False));continue
        try: rows=[json.loads(x) for x in content.splitlines() if x.strip()]
        except Exception:
            runs.append(dict(base,status='INVALID_TRAJECTORY_JSON',valid_clear=False));continue
        if not rows:
            runs.append(dict(base,status='UNKNOWN_EMPTY_TRAJECTORY',valid_clear=False));continue
        radius=float(goal_radius if goal_radius is not None else base['geometry_parameters'].get('arrival_radius_m',0))
        dwell=float(dwell_s if dwell_s is not None else report.get('service_stop_hold_s_required',0))
        speed=float(speed_mps if speed_mps is not None else report.get('stop_speed_threshold_mps',0.5))
        if radius<=0 or dwell<=0 or speed<=0:
            runs.append(dict(base,status='UNKNOWN_STOP_PROTOCOL',valid_clear=False));continue
        elapsed=[float(x['elapsed_s']) for x in rows]
        if any(not math.isfinite(t) for t in elapsed) or any(elapsed[i+1]<=elapsed[i] for i in range(len(elapsed)-1)):
            runs.append(dict(base,status='INVALID_TIME_AXIS',valid_clear=False));continue
        longest=0.;now=0.;closest=float('inf')
        for k,row in enumerate(rows):
            d=float(row['distance_to_goal_m']);v=float(row['speed_mps']);closest=min(closest,d)
            if not all(math.isfinite(x) for x in (d,v)):
                now=0;continue
            # Use the recorded time delta except for frame zero where dt is
            # only diagnostic; repeated samples cannot invent dwell.
            dt=elapsed[k]-elapsed[k-1] if k>0 else 0.
            now=now+dt if d<=radius+1e-9 and v<=speed+1e-9 else 0.
            longest=max(longest,now)
        control=report.get('experiment_config')
        params=base['geometry_parameters']
        key=(report.get('carla_map'),report.get('seed'),params.get('spawn_index'),params.get('a_steps'),params.get('b_steps'))
        # Blocker mode and destination intentionally excluded, all other
        # control settings must be present and match to assert comparability.
        signature=None
        if isinstance(control,dict):
            signature=tuple(sorted((k,json.dumps(v,sort_keys=True)) for k,v in control.items()
                                   if k not in ('mode','blocker_blueprint','redirect_at_s')))
        log_valid=bool(report.get('status')=='COMPLETED' and report.get('collisions')==0
                        and longest+float(report.get('sample_dt_s',0.05))+1e-7>=dwell)
        detour=report.get('control_diagnostics_v9',{}).get('planned_route_length_m')
        straight=report.get('control_diagnostics_v9',{}).get('target_start_euclidean_m')
        ratio=float(detour)/float(straight) if isinstance(detour,(float,int)) and isinstance(straight,(float,int)) and straight>0 else None
        runs.append(dict(base,status='CLEAR_STOP_SURROGATE' if log_valid else 'NO_CLEAR_STOP_SURROGATE',
                         valid_clear=log_valid,observed_best_stationary_dwell_s=round(longest,4),
                         min_goal_distance_m=round(closest,4),elapsed_s=round(elapsed[-1],4),
                         route_length_to_euclidean_ratio=round(ratio,3) if ratio is not None else None,
                         matched_key=key,experiment_config_signature=signature,
                         exact_experiment_config_present=isinstance(control,dict),
                         source='CARLA_TOWN_SURROGATE_ONLY'))
    return runs


def audit(clear_dir,blocked_dir=None,redirect_dir=None):
    root=Path(clear_dir)
    clear=_load_runs(root)
    if not clear:return {'gate':'UNKNOWN_NO_CLEAR_CONTROL','clear_trials':0,
                         'evidence':'CARLA_SURROGATE_ONLY','trials':[]}
    valid=sum(x['valid_clear'] for x in clear)
    by_spawn=defaultdict(lambda:Counter({'trials':0,'successful_stop':0}))
    for x in clear:
        sp=x['geometry_parameters'].get('spawn_index')
        by_spawn[str(sp)]['trials']+=1
        by_spawn[str(sp)]['successful_stop']+=int(x['valid_clear'])
    summary={'gate':'PARTIALLY_CALIBRATED' if valid and valid<len(clear) else
                      'CLEAR_CONTROL_CALIBRATED' if valid==len(clear) else 'FAIL_NO_SUCCESSFUL_CLEAR_A',
             'evidence':'CARLA_TOWN_SURROGATE_ONLY',
             'clear_trials':len(clear),'clear_stop_surrogate_success':valid,
             'failed_or_unknown_clear_trials':len(clear)-valid,
             'independent_real_handoff_claims':0,'by_spawn':dict(by_spawn),
             'trials':clear,'blocked_comparison_gate':'NOT_TESTED',
             'warnings':['No sensor collision event is not an independent collision-free certificate',
                         'CARLA Town03 does not validate real nuPlan physical sites or curb legality',
                         'Never compare blocked-A to unmatched or failed clear-A controls']}
    if blocked_dir is not None or redirect_dir is not None:
        more=[]
        for path in (blocked_dir,redirect_dir):
            if path is not None:more.extend(_load_runs(Path(path)))
        cal_by_key={x['matched_key']:x for x in clear if 'matched_key' in x}
        comparisons=[]
        for other in more:
            ck=other.get('matched_key');base=cal_by_key.get(ck)
            if base is None:
                decision='UNKNOWN_UNMATCHED_CONTROL'
            elif not base['valid_clear']:
                decision='INVALID_FAILED_CLEAR_CONTROL'
            elif base['experiment_config_signature'] is None or other.get('experiment_config_signature') is None:
                decision='UNKNOWN_MATCHED_CONFIGURATION_NOT_RECORDED'
            elif base['experiment_config_signature']!=other['experiment_config_signature']:
                decision='INVALID_DIFFERENT_CONFIGURATION'
            else:
                decision='MATCHED_CARLA_SURROGATE_ONLY'
            comparisons.append({'trial':other['trial'],'mode':other['mode'],
                                'decision':decision,'clear_trial':base['trial'] if base else None,
                                'clear_stop':base['valid_clear'] if base else None,
                                'intervention_stop':other['valid_clear']})
        summary['comparisons']=comparisons
        summary['blocked_comparison_gate']='ELIGIBLE_CARLA_SURROGATE_ONLY' if comparisons and all(
            x['decision']=='MATCHED_CARLA_SURROGATE_ONLY' for x in comparisons) else 'NOT_VERIFIED'
    return summary
