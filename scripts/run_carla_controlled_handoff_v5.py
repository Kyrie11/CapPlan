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
    actors=[];collision_events=[];rows=[];controlled_traffic_lights=[]
    result={'evidence_type':'CARLA_CONTROLLED_VEHICLE_TRAJECTORY_ONLY', 'carla_map':world.get_map().name,
        'mode':args.mode,'seed':args.seed,'passenger_boarding_verified':False,'curb_legality_verified':False,
        'nuplan_episode_matched':False,'vehicle_motion_observed':False,'vehicle_collision_free':None,
        'status':'STARTED','sample_dt_s':args.dt}
    try:
        settings=world.get_settings();settings.synchronous_mode=True;settings.fixed_delta_seconds=args.dt
        settings.no_rendering_mode=args.no_rendering
        world.apply_settings(settings)
        preexisting=[a.id for a in world.get_actors().filter('vehicle.*')]
        traffic_lights=sorted(world.get_actors().filter('traffic.traffic_light*'),key=lambda x:x.id)
        result['traffic_signal_count']=len(traffic_lights)
        result['traffic_signals_controlled']=bool(args.freeze_traffic_lights)
        if args.freeze_traffic_lights:
            # A deliberately artificial, fixed common signal phase used ONLY
            # for matched CARLA controls, never a natural traffic claim.
            for light in traffic_lights:
                controlled_traffic_lights.append((light,light.get_state()))
                light.set_state(carla.TrafficLightState.Green)
                light.freeze(True)
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
        # V9 instrumentation only (does not change control). It distinguishes
        # a poor target/route choice from vehicle inability to stop.
        start_tf=vehicle.get_transform()
        forward=start_tf.get_forward_vector()
        delta=A.transform.location-start_tf.location
        denom=max(1e-9,math.hypot(delta.x,delta.y)*math.hypot(forward.x,forward.y))
        route_debug={'start_yaw_deg':float(start_tf.rotation.yaw),
                     'heading_dot_to_goal_xy':float((delta.x*forward.x+delta.y*forward.y)/denom),
                     'target_start_euclidean_m':float(math.hypot(delta.x,delta.y))}
        try:
            local=agent.get_local_planner()
            route=list(local.get_plan()) if hasattr(local,'get_plan') else []
            route_debug['planned_waypoint_count']=len(route)
            route_debug['planned_first_5_xy']=[
                [float(point.transform.location.x),float(point.transform.location.y)]
                for point,_ in route[:5]]
            if route:
                coords=[start_tf.location]+[point.transform.location for point,_ in route]
                route_debug['planned_route_length_m']=sum(coords[i].distance(coords[i+1]) for i in range(len(coords)-1))
        except Exception as exc:
            route_debug['route_introspection_unavailable']=type(exc).__name__
        result['control_diagnostics_v9']=route_debug
        sim_start=None;changed=False;reached=False;agent_done_seen=False;minimum_gap_m=float('inf')
        in_goal_zone_once=False; braking_to_stop=False; stopped_dwell_s=0.0; best_dwell_s=0.0
        for tick in range(int(args.max_seconds/args.dt)):
            frame=world.tick();snap=world.get_snapshot()
            simtime=float(snap.timestamp.elapsed_seconds)
            if sim_start is None:sim_start=simtime
            elapsed=simtime-sim_start
            if args.mode=='blocked_A_redirect_B' and not changed and elapsed>=args.redirect_at_s:
                agent.set_destination(B.transform.location);goal='B';changed=True
            dest=A.transform.location if goal=='A' else B.transform.location
            # V7: zone entry alone is not passenger handoff. In strict mode
            # apply a documented scripted brake, and verify a stationary dwell.
            # This is NOT a normal curb-parking planner or accessible boarding.
            if args.service_stop_hold_s>0 and braking_to_stop:
                vehicle.apply_control(carla.VehicleControl(throttle=0.0,brake=1.0,hand_brake=False))
            else:
                vehicle.apply_control(agent.run_step())
            loc=vehicle.get_location();vel=vehicle.get_velocity()
            sp=math.sqrt(vel.x**2+vel.y**2+vel.z**2)
            gap=loc.distance(dest)
            minimum_gap_m=min(minimum_gap_m,gap)
            agent_done_seen=agent_done_seen or bool(agent.done())
            in_goal_zone_once = in_goal_zone_once or gap<=args.arrival_radius_m
            if args.service_stop_hold_s>0 and gap<=args.arrival_radius_m:
                braking_to_stop=True
            if args.service_stop_hold_s>0 and braking_to_stop:
                stopped_dwell_s=(stopped_dwell_s+args.dt if gap<=args.arrival_radius_m and sp<=args.stop_speed_mps else 0.0)
                best_dwell_s=max(best_dwell_s,stopped_dwell_s)
            rows.append({'tick':tick,'elapsed_s':round(elapsed,3),'x':loc.x,'y':loc.y,'z':loc.z,
                'speed_mps':sp,'target':goal,'distance_to_goal_m':gap,'collisions_to_date':len(collision_events),
                'scripted_braking':braking_to_stop,'stationary_dwell_s':round(stopped_dwell_s,3)})
            if args.service_stop_hold_s>0:
                if stopped_dwell_s+1e-8>=args.service_stop_hold_s:
                    reached=True;break
            elif gap<=args.arrival_radius_m:
                reached=True;break
        service_stopped=bool(args.service_stop_hold_s>0 and reached and len(collision_events)==0)
        result.update({'status':'COMPLETED','goal':goal,'reached_goal_zone':in_goal_zone_once,
            'elapsed_sim_s':rows[-1]['elapsed_s'] if rows else None,'ticks':len(rows),
            'collisions':len(collision_events),'collision_events':collision_events,
            'minimum_goal_gap_m':minimum_gap_m, 'agent_done_seen':agent_done_seen,
            'ended_speed_mps':rows[-1]['speed_mps'] if rows else None,
            'stopped_near_goal':service_stopped if args.service_stop_hold_s>0 else bool(reached and rows and rows[-1]['speed_mps']<=0.5),
            'service_stop_hold_s_required':args.service_stop_hold_s,
            'service_stop_best_dwell_s':best_dwell_s,
            'service_stop_protocol':'SCRIPTED_BRAKE_IN_GOAL_ZONE; no curb legality or boarding',
            'stationary_service_stop_observed':service_stopped,
            'stop_speed_threshold_mps':args.stop_speed_mps,
            'vehicle_collision_free':None, # lack of callbacks is not an independently verified collision-free proof
            'no_collision_sensor_events_observed':len(collision_events)==0,
            'vehicle_motion_observed':bool(rows),
            'vehicle_service_success_surrogate':bool(service_stopped if args.service_stop_hold_s>0 else reached and not collision_events),
            'research_boundary':'CARLA goal-zone arrival with collision sensor only; NOT a passenger handoff or legal accessible PUDO'})
        return result,rows
    finally:
        for light,previous_state in controlled_traffic_lights:
            try:
                light.freeze(False)
                light.set_state(previous_state)
            except Exception:pass
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
    p.add_argument('--service_stop_hold_s',type=float,default=0.0,help='When positive, brake after zone entry and require stationary dwell; scripted surrogate only')
    p.add_argument('--stop_speed_mps',type=float,default=0.5)
    p.add_argument('--freeze_traffic_lights',action='store_true',help='Artificial all-green traffic lights for matched control; never a natural-road result')
    p.add_argument('--blocker_blueprint',default='vehicle.tesla.model3');p.add_argument('--no_rendering',action='store_true')
    p.add_argument('--dry_run',action='store_true')
    a=p.parse_args()
    if a.dt<=0 or a.max_seconds<=0 or a.redirect_at_s<0 or a.target_speed_kmh<=0 or a.a_steps<=0 or a.b_steps<=0 or a.service_stop_hold_s<0 or a.stop_speed_mps<=0:p.error('invalid timing or speed')
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
