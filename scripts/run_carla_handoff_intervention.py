#!/usr/bin/env python3
"""Controlled CARLA curb-blocker intervention with safe dry-run default.

The user MUST supply a manually calibrated CARLA world transform.  nuPlan
coordinates are NOT directly interchangeable with CARLA coordinates.  Only
successful execution logs justify an event-spawn claim; a blocker spawn is NOT
proof of safe motion, interface feasibility, or passenger handoff.

A separate controller must execute a method-selected vehicle trajectory and
check dynamic collisions, legality and dwell.  This script deliberately does
not impersonate that controller.
"""
from __future__ import annotations
import argparse,json,math
from pathlib import Path


def verify_config(d):
    fields=['overlay_id','carla_map','calibration_id','blocker_blueprint','blocker_transform',
            'spawn_after_sim_seconds','duration_sim_seconds']
    missing=[k for k in fields if k not in d]
    if missing:raise ValueError(f'missing required CARLA config: {missing}')
    if d['calibration_id'] in ('',None,'REPLACE_ME'):
        raise ValueError('a real nuPlan-to-CARLA site calibration is mandatory')
    for k in ('x','y','z','yaw'):
        value=float(d['blocker_transform'][k])
        if not math.isfinite(value):raise ValueError('non-finite actor transform')
    if float(d['spawn_after_sim_seconds'])<0 or float(d['duration_sim_seconds'])<=0:
        raise ValueError('time values must be nonnegative and duration >0')
    return d


def execute(d,host,port):
    try:
        import carla
    except ImportError as exc:
        raise RuntimeError('CARLA Python API is not installed in this environment') from exc
    client=carla.Client(host,port);client.set_timeout(12.0)
    world=client.get_world()
    if d['carla_map'] not in world.get_map().name:
        raise RuntimeError(f'wrong map: loaded {world.get_map().name}, expected {d["carla_map"]}')
    blueprints=world.get_blueprint_library()
    bp=blueprints.find(d['blocker_blueprint'])
    pt=d['blocker_transform']
    transform=carla.Transform(carla.Location(x=float(pt['x']),y=float(pt['y']),z=float(pt['z'])),
                              carla.Rotation(yaw=float(pt['yaw'])))
    before=world.get_snapshot().timestamp.elapsed_seconds
    target=before+float(d['spawn_after_sim_seconds'])
    actor=None
    result={'overlay_id':d['overlay_id'],'calibration_id':d['calibration_id'],'carla_map':world.get_map().name,
            'evidence_type':'controlled_carla_actor_intervention_only',
            'baseline_sim_time_s':before,'spawn_time_s':None,'destroy_time_s':None,
            'spawn_succeeded':False,'vehicle_motion_verified':False,'handoff_verified':False}
    try:
        while world.wait_for_tick(seconds=20.0).timestamp.elapsed_seconds < target:
            pass
        actor=world.try_spawn_actor(bp,transform)
        if actor is None:
            result['error']='actor_spawn_failed_at_site'
            return result
        result['spawn_succeeded']=True
        spawn_time=world.get_snapshot().timestamp.elapsed_seconds
        result['spawn_time_s']=spawn_time
        end=spawn_time+float(d['duration_sim_seconds'])
        while world.wait_for_tick(seconds=20.0).timestamp.elapsed_seconds < end:
            pass
    finally:
        if actor is not None:
            result['destroy_time_s']=world.get_snapshot().timestamp.elapsed_seconds
            actor.destroy()
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('--config',required=True)
    p.add_argument('--output',required=True);p.add_argument('--execute',action='store_true')
    p.add_argument('--host',default='127.0.0.1');p.add_argument('--port',type=int,default=2000)
    a=p.parse_args();d=verify_config(json.loads(Path(a.config).read_text(encoding='utf-8')))
    report=execute(d,a.host,a.port) if a.execute else {'overlay_id':d['overlay_id'],
       'dry_run':True,'calibration_id':d['calibration_id'],'carla_map':d['carla_map'],
       'spawn_succeeded':False,'vehicle_motion_verified':False,'handoff_verified':False,
       'evidence_type':'configuration_validation_only'}
    dest=Path(a.output);dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':main()
