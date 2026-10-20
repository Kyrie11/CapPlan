#!/usr/bin/env python3
"""Read-only cross-layer diagnostics for V9 output; never calls CARLA or GIS.

Adds an explicit consistency check between reported CARLA vehicle start
location and the first global route waypoint. Flags repeated route beginnings
for distinct spawn points but does not conclude a specific controller bug.
Paired/blocked conclusions remain gated on independently trace-verified clear.
"""
from __future__ import annotations
import argparse,json,math,sys
from collections import Counter,defaultdict
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.audit_handoff_carla_traces_v9 import diagnose


def diagnose_v9(v9_dir,route_offset_warn_m=20):
    root=Path(v9_dir);cal=root/'carla_clear_speed5_radius6'
    report={'scope':'UPLOADED_V9_STATIC_LOGS_ONLY',
        'v9_geometry_summary':None,'carla':{},'red_flags':[],
        'nuplan_handoff_verified':False,'carla_physical_curb_verified':False}
    geof=root/'alternatives_dual_4.summary.json'
    if geof.exists():
        geo=json.loads(geof.read_text())
        c=geo.get('counts',{})
        report['v9_geometry_summary']={
            'pairs':c.get('processed_pairs'), 'time_rows':c.get('time_rows'),
            'strict_B_count':c.get('strict_B_graph_feasible'),
            'optimistic_B_count':c.get('optimistic_B_graph_feasible'),
            'optimistic_third_count':c.get('optimistic_third_graph_feasible'),
            'geometry_not_physical_truth':True}
    records=[];per_spawn=defaultdict(list)
    for f in sorted(cal.glob('spawn*_asteps*/execution_report.json')):
        doc=json.loads(f.read_text());c=doc.get('control_diagnostics_v9',{})
        start=doc.get('carla_locations',{}).get('start')
        route=c.get('planned_first_5_xy',[])
        if not (isinstance(start,list) and len(start)>1 and route):continue
        offset=math.hypot(float(start[0])-float(route[0][0]),float(start[1])-float(route[0][1]))
        r={'trial':f.parent.name,'reported_start':start[:2],
           'route_first':route[0], 'first_waypoint_gap_from_start_m':round(offset,4),
           'planned_route_length_m':c.get('planned_route_length_m'),
           'control_stop_surrogate':bool(doc.get('stopped_near_goal')),
           'large_route_start_gap':offset>route_offset_warn_m,
           'physical_stop_interface_verified':False}
        records.append(r)
        per_spawn[int(doc.get('geometry_parameters',{}).get('spawn_index',-1))].append(r)
    report['carla']['trials']=records
    report['carla']['total_trials']=len(records)
    report['carla']['stopped_near_goal_report_count']=sum(r['control_stop_surrogate'] for r in records)
    report['carla']['route_start_gap_count']=sum(r['large_route_start_gap'] for r in records)
    report['carla']['unique_reported_route_starts']=len(set(tuple(r['route_first']) for r in records))
    report['carla']['unique_spawn_starts']=len(set(tuple(r['reported_start']) for r in records))
    report['carla']['route_offset_warn_m']=route_offset_warn_m
    if report['carla']['unique_spawn_starts']>1 and report['carla']['unique_reported_route_starts']==1:
        report['red_flags'].append('SAME_ROUTE_FIRST_WAYPOINT_ACROSS_DISTINCT_SPAWNS_CHECK_START_AND_AGENT_ROUTE')
    if any(r['large_route_start_gap'] for r in records):
        report['red_flags'].append('ROUTE_START_TOO_FAR_FROM_DECLARED_VEHICLE_START')
    report['required_checks']=['confirm BasicAgent.get_plan() coordinates correspond to the CURRENT ego spawn',
        'verify first waypoint before any movement and compare to actual ego pose',
        'only run paired blocker trials after independent clear vehicle stop passes',
        'do not infer legal curb or passenger boarding from CARLA Town map']
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--v9_dir',required=True);p.add_argument('--output',required=True)
    p.add_argument('--route_offset_warn_m',type=float,default=20)
    a=p.parse_args()
    if a.route_offset_warn_m<0:p.error('nonnegative warn gap required')
    obj=diagnose_v9(a.v9_dir,a.route_offset_warn_m)
    dest=Path(a.output);dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({'red_flags':obj['red_flags'],'trials':obj['carla']['total_trials']},ensure_ascii=False))
if __name__=='__main__':main()
