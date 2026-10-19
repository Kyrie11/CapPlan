#!/usr/bin/env python3
"""Search benign CARLA A targets FIRST; abort comparison if none reaches goal.
This invokes the existing CARLA 0.9.15 client, not an independent motion oracle.
"""
import argparse,json,subprocess,sys
from pathlib import Path

def main():
    p=argparse.ArgumentParser();p.add_argument('--output_dir',required=True)
    p.add_argument('--host',default='127.0.0.1');p.add_argument('--port',type=int,default=2000)
    p.add_argument('--map',default='Town03');p.add_argument('--spawn_indices',default='0,1,2,3')
    p.add_argument('--a_steps_options',default='4,7,10');p.add_argument('--b_steps',type=int,default=8)
    p.add_argument('--max_seconds',type=float,default=40);p.add_argument('--arrival_radius_m',type=float,default=4)
    p.add_argument('--seed',type=int,default=13)
    p.add_argument('--target_speed_kmh',type=float,default=8)
    p.add_argument('--service_stop_hold_s',type=float,default=2)
    p.add_argument('--require_stationary_stop',action='store_true')
    p.add_argument('--freeze_traffic_lights',action='store_true')
    a=p.parse_args();out=Path(a.output_dir);out.mkdir(parents=True,exist_ok=True)
    rows=[];candidates=[]
    for spawn in map(int,a.spawn_indices.split(',')):
        for astep in map(int,a.a_steps_options.split(',')):
            dest=out/f'spawn{spawn}_asteps{astep}'
            cmd=[sys.executable,str(Path(__file__).with_name('run_carla_controlled_handoff_v5.py')),
              '--host',a.host,'--port',str(a.port),'--map',a.map,'--mode','clear_A',
              '--spawn_index',str(spawn),'--a_steps',str(astep),'--b_steps',str(a.b_steps),
              '--max_seconds',str(a.max_seconds),'--arrival_radius_m',str(a.arrival_radius_m),
              '--seed',str(a.seed),'--output_dir',str(dest),
              '--target_speed_kmh',str(a.target_speed_kmh),'--service_stop_hold_s',str(a.service_stop_hold_s)]
            if a.freeze_traffic_lights:cmd.append('--freeze_traffic_lights')
            process=subprocess.run(cmd,capture_output=True,text=True)
            r={'spawn_index':spawn,'a_steps':astep,'returncode':process.returncode,'output_dir':str(dest)}
            report=dest/'execution_report.json'
            if report.exists():
                d=json.loads(report.read_text())
                r.update({k:d.get(k) for k in ('reached_goal_zone','stopped_near_goal','minimum_goal_gap_m','agent_done_seen','collisions','elapsed_sim_s')})
            else:r['error_tail']=process.stderr[-600:]
            rows.append(r)
            if r.get('collisions')==0 and (r.get('stopped_near_goal') if a.require_stationary_stop else r.get('reached_goal_zone')):candidates.append(r)
    # Conservative motion experiment comparison gate: current A goal must work.
    result={'calibrated_goal_zone_candidates':candidates,'trials':rows,
            'status':'GOAL_ZONE_CANDIDATES_FOUND' if candidates else 'NO_VALID_CLEAR_A_CONTROL',
            'cannot_claim_physical_pudo_or_boarding':True,
            'stationary_stop_required':a.require_stationary_stop,
            'control_parameters':{'target_speed_kmh':a.target_speed_kmh,'radius_m':a.arrival_radius_m,
                                  'hold_s':a.service_stop_hold_s,'freeze_traffic_lights':a.freeze_traffic_lights},
            'selection_bias':'Calibration candidates selected on clear-A result; paired trials on same geometry are mechanistic controls, NOT independent incidence.'}
    (out/'calibration_summary.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'status':result['status'],'candidates':len(candidates),'trials':len(rows)}))
if __name__=='__main__':main()
