#!/usr/bin/env python3
"""Headless CARLA 0.9.15 controlled *vehicle-only* experiment.

This runs CARLA's BasicAgent on its own Town map in synchronous fixed-step
mode. It does NOT replay a nuPlan city, certify public-street legality, deploy
an accessible interface, or verify passenger boarding.  NEVER convert these
logs to method-specific nuPlan real_motion_claims.jsonl.
"""
from __future__ import annotations
import argparse, hashlib, json, math, os, sys, random
from pathlib import Path

def offline_spec():
    return {'simulator':'CARLA 0.9.15','run_modes':['clear_A','blocked_A','blocked_A_redirect_B'],
      'scene':'CARLA native town, uncalibrated to nuPlan four-city geometry',
      'limits':['no actual PUDO legal status','no sidewalk connection/ADA geometry','no passenger capability','no independent boarding confirmation',
                'no nuPlan-to-CARLA coordinate registration'],
      'client_import':'requires `import carla` and agents.navigation.basic_agent.BasicAgent',
      'output_truth_type':'CARLA_CONTROLLED_VEHICLE_TRAJECTORY_ONLY'}

def load_api():
    try:
        import carla
    except ImportError as e:
        raise RuntimeError('CARLA 0.9.15 Python API missing; add PythonAPI/carla wheel or compatible egg to PYTHONPATH') from e
    try:
        from agents.navigation.basic_agent import BasicAgent
    except ImportError as e:
        raise RuntimeError('CARLA agents package missing: add $CARLA_ROOT/PythonAPI/carla to PYTHONPATH') from e
    return carla,BasicAgent

def choose_waypoints(world,carla,spawn_index,a_steps=7,b_steps=8):
    spawn=world.get_map().get_spawn_points()
    if not spawn:raise RuntimeError('map has no vehicle spawn points')
    start=spawn[spawn_index%len(spawn)]
    road=world.get_map().get_waypoint(start.location,project_to_road=True,lane_type=carla.LaneType.Driving)
    if road is None:raise RuntimeError('start not on driving lane')
    def advance(w,n,step=5.0):
        for i in range(n):
            opts=w.next(step)
            if not opts:raise RuntimeError('cannot sample continuous road ahead')
            opts.sort(key=lambda v:(v.road_id,v.lane_id,v.s))
            w=opts[0]
        return w
    A=advance(road,a_steps);B=advance(A,b_steps)
    if A.transform.location.distance(B.transform.location)<15:raise RuntimeError('candidate A/B too close')
    return start,A,B

def run(args):
    random.seed(args.seed)
    carla,BasicAgent=load_api()
    client=carla.Client(args.host,args.port);client.set_timeout(args.timeout)
    world=client.get_world()
    if args.map and args.map not in world.get_map().name:
        world=client.load_world(args.map)
    original=world.get_settings();traffic=None
    actors=[];collision_events=[];rows=[]
    result={'evidence_type':'CARLA_CONTROLLED_VEHICLE_TRAJECTORY_ONLY', 'carla_map':world.get_map().name,
        'mode':args.mode,'seed':args.seed,'passenger_boarding_verified':False,'curb_legality_verified':False,
        'nuplan_episode_matched':False,'vehicle_motion_observed':False,'vehicle_collision_free':None,
        'status':'STARTED','sample_dt_s':args.dt}
    try:
        settings=world.get_settings();settings.synchronous_mode=True;settings.fixed_delta_seconds=args.dt
        settings.no_rendering_mode=args.no_rendering
        world.apply_settings(settings)
        preexisting=[a.id for a in world.get_actors().filter('vehicle.*')]
        result['other_vehicles_at_start']=len(preexisting)
        result['paired_controlled_comparability']=len(preexisting)==0
        start,A,B=choose_waypoints(world,carla,args.spawn_index,args.a_steps,args.b_steps)
        result['geometry_parameters']={'spawn_index':args.spawn_index,'a_steps':args.a_steps,'b_steps':args.b_steps,'arrival_radius_m':args.arrival_radius_m}
        result['carla_locations']={'start':[start.location.x,start.location.y,start.location.z],
          'A':[A.transform.location.x,A.transform.location.y,A.transform.location.z],
          'B':[B.transform.location.x,B.transform.location.y,B.transform.location.z]}
        library=world.get_blueprint_library()
        ego_bps=library.filter('vehicle.*');ego_bps=sorted(ego_bps,key=lambda b:b.id)
        if not ego_bps:raise RuntimeError('no vehicle blueprint')
        vehicle=None
        for bp in ego_bps:
            vehicle=world.try_spawn_actor(bp,start)
            if vehicle:break
        if vehicle is None:raise RuntimeError('no spawn available for ego')
        actors.append(vehicle)
        collision_bp=library.find('sensor.other.collision')
        collision=world.spawn_actor(collision_bp,carla.Transform(),attach_to=vehicle)
        actors.append(collision)
        def on_collision(ev):
            other=getattr(ev.other_actor,'type_id','unknown')
            collision_events.append({'frame':int(ev.frame),'other_actor':other})
        collision.listen(on_collision)
        if args.mode!='clear_A':
            blocker_bp=library.find(args.blocker_blueprint)
            # Using a CARLA lane-center obstruction, NOT a real curb construction.
            block_tf=A.transform
            block_tf.location.z+=0.2
            blocker=world.try_spawn_actor(blocker_bp,block_tf)
            if blocker is None:raise RuntimeError('could not spawn lane blocker; change spawn index or map')
            actors.append(blocker)
        agent=BasicAgent(vehicle,target_speed=args.target_speed_kmh)
        goal='A';agent.set_destination(A.transform.location)
        sim_start=None;changed=False;reached=False;agent_done_seen=False;minimum_gap_m=float('inf')
        for tick in range(int(args.max_seconds/args.dt)):
            frame=world.tick();snap=world.get_snapshot()
            simtime=float(snap.timestamp.elapsed_seconds)
            if sim_start is None:sim_start=simtime
            elapsed=simtime-sim_start
            if args.mode=='blocked_A_redirect_B' and not changed and elapsed>=args.redirect_at_s:
                agent.set_destination(B.transform.location);goal='B';changed=True
            dest=A.transform.location if goal=='A' else B.transform.location
            vehicle.apply_control(agent.run_step())
            loc=vehicle.get_location();vel=vehicle.get_velocity()
            sp=math.sqrt(vel.x**2+vel.y**2+vel.z**2)
            gap=loc.distance(dest)
            minimum_gap_m=min(minimum_gap_m,gap)
            agent_done_seen=agent_done_seen or bool(agent.done())
            rows.append({'tick':tick,'elapsed_s':round(elapsed,3),'x':loc.x,'y':loc.y,'z':loc.z,
                'speed_mps':sp,'target':goal,'distance_to_goal_m':gap,'collisions_to_date':len(collision_events)})
            if gap<=args.arrival_radius_m:
                reached=True;break
        result.update({'status':'COMPLETED','goal':goal,'reached_goal_zone':reached,
            'elapsed_sim_s':rows[-1]['elapsed_s'] if rows else None,'ticks':len(rows),
            'collisions':len(collision_events),'collision_events':collision_events,
            'minimum_goal_gap_m':minimum_gap_m, 'agent_done_seen':agent_done_seen,
            'ended_speed_mps':rows[-1]['speed_mps'] if rows else None,
            'stopped_near_goal':bool(reached and rows and rows[-1]['speed_mps']<=0.5),
            'vehicle_collision_free':None, # lack of callbacks is not an independently verified collision-free proof
            'no_collision_sensor_events_observed':len(collision_events)==0,
            'vehicle_motion_observed':bool(rows),
            'vehicle_service_success_surrogate':bool(reached and not collision_events),
            'research_boundary':'CARLA goal-zone arrival with collision sensor only; NOT a passenger handoff or legal accessible PUDO'})
        return result,rows
    finally:
        for a in reversed(actors):
            try:
                if a.type_id.startswith('sensor.'):a.stop()
                a.destroy()
            except Exception:pass
        try:world.apply_settings(original)
        except Exception:pass

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--output_dir',required=True);p.add_argument('--host',default='127.0.0.1');p.add_argument('--port',type=int,default=2000)
    p.add_argument('--map',default='Town03');p.add_argument('--mode',choices=['clear_A','blocked_A','blocked_A_redirect_B'],default='clear_A')
    p.add_argument('--spawn_index',type=int,default=0);p.add_argument('--seed',type=int,default=13)
    p.add_argument('--a_steps',type=int,default=7);p.add_argument('--b_steps',type=int,default=8)
    p.add_argument('--redirect_at_s',type=float,default=8);p.add_argument('--target_speed_kmh',type=float,default=20)
    p.add_argument('--arrival_radius_m',type=float,default=4);p.add_argument('--max_seconds',type=float,default=50)
    p.add_argument('--dt',type=float,default=.05);p.add_argument('--timeout',type=float,default=30)
    p.add_argument('--blocker_blueprint',default='vehicle.tesla.model3');p.add_argument('--no_rendering',action='store_true')
    p.add_argument('--dry_run',action='store_true')
    a=p.parse_args()
    if a.dt<=0 or a.max_seconds<=0 or a.redirect_at_s<0 or a.target_speed_kmh<=0 or a.a_steps<=0 or a.b_steps<=0:p.error('invalid timing or speed')
    out=Path(a.output_dir);out.mkdir(parents=True,exist_ok=True)
    if a.dry_run:
        payload=offline_spec();payload.update({'status':'DRY_RUN_NO_SIMULATION','mode':a.mode});rows=[]
    else:payload,rows=run(a)
    log=out/'trajectory.jsonl';log.write_text(''.join(json.dumps(x)+'\n' for x in rows))
    payload['trajectory_file_sha256']=hashlib.sha256(log.read_bytes()).hexdigest()
    payload['trajectory_file']=str(log)
    (out/'execution_report.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'status':payload['status'],'mode':a.mode,'output_dir':str(out)}))
if __name__=='__main__':main()
