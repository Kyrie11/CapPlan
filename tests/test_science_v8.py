"""Science v8 must disprove overly strong failure and vehicle evidence claims."""
from __future__ import annotations
import importlib.util
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]/'scripts'

def module(name):
    p=ROOT/(name+'.py')
    spec=importlib.util.spec_from_file_location(name,p)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    return m

def test_grid_brackets_are_not_exact_deadlines():
    m=module('audit_science_v8')
    rows=[{'episode_id':'e','a_anchor':'A','b_anchor':'B','notice_issued_at_s':t,
           'can_redirect_to_B_in_graph':t<=30,'city':'boston'} for t in (0,15,30,60)]
    w=m.extract_windows(rows)[0]
    assert w['last_tested_possible_s']==30
    assert w['first_tested_impossible_s']==60
    assert not w['monotonicity_violation']
    rows[-1]['can_redirect_to_B_in_graph']=True
    rows[-2]['can_redirect_to_B_in_graph']=False
    assert m.extract_windows(rows)[0]['monotonicity_violation']


def test_all_anchor_search_exposes_third_option_and_keeps_history():
    m=module('audit_handoff_all_alternatives_v8')
    # A path: O->M->A; at notice t=0, actionable after ack 10s,
    # passenger walked 10m and is at M. Direct B is too far, C is nearby.
    edge=lambda u,v,length:SimpleNamespace(from_node=u,to_node=v,length_m=length,
       obstacle=False,obstacle_state=None,step_free=True,width_m=2.,slope=0.,
       cross_slope=0.,confidence=1.,crossing_type='curb' if v in ('B','C') else 'sidewalk')
    graph=SimpleNamespace(nodes=[SimpleNamespace(node_id=n) for n in ('O','M','A','B','C')],
       edges=[edge('O','M',10.),edge('M','A',10.),edge('M','B',18.),edge('M','C',3.)])
    p={'episode_id':'e','a_anchor':'A','b_anchor':'B','distances_m':{'origin_to_a':20.},
       'reroute_total_m':80.,'screening_contract':{'distance_budget_m':30.,
            'already_consumed_m':3.,'min_width_m':1.,'max_slope':.2,'max_cross_slope':.2},
       'paths':{'origin_to_a':{'node_ids':['O','M','A'],
           'edges':[{'length_m':10.},{'length_m':10.}]}}}
    anchors=[{'anchor_id':name,'kind':'pickup','legal_stop':True,'blockage_risk':0,
             'adjacent_ped_node_id':name,'hybrid_physical_site_key':name} for name in ('A','B','C')]
    result=m.evaluate(p,anchors,graph,0,speed_mps=1.,ack_s=10.)
    assert result['non_B_alternate_graph_feasible']
    assert not result['B_graph_feasible']
    assert result['distance_consumed_at_ack_m']==13
    assert result['alternates'][0]['anchor_id']=='C'
    assert not result['alternates'][0]['physical_legality_independently_verified']
    anchors[2]['blockage_risk']=.9
    assert not m.evaluate(p,anchors,graph,0,speed_mps=1.,ack_s=10.)['non_B_alternate_graph_feasible']


def test_carla_trace_requires_actual_stationary_zone_time(tmp_path):
    m=module('run_carla_paired_v7')
    directory=tmp_path/'clear_A';directory.mkdir()
    def row(t,speed,distance):
        return {'elapsed_s':t,'speed_mps':speed,'distance_to_goal_m':distance,'target':'A'}
    rs=[row(i*.05,0.1,2.0) for i in range(50)]
    encoded=''.join(json.dumps(x)+'\n' for x in rs).encode()
    (directory/'trajectory.jsonl').write_bytes(encoded)
    d={'mode':'clear_A','status':'COMPLETED','trajectory_file_sha256':hashlib.sha256(encoded).hexdigest(),
       'stationary_service_stop_observed':True,'service_stop_best_dwell_s':2.,'collisions':0,
       'other_vehicles_at_start':0,'goal':'A','geometry_parameters':{'arrival_radius_m':4.},
       'stop_speed_threshold_mps':.5}
    (directory/'execution_report.json').write_text(json.dumps(d))
    r=m.validate_run(directory,'clear_A',2.)
    assert r['valid'] and r['stop_with_dwell']
    rs[20]['distance_to_goal_m']=5.0
    encoded=''.join(json.dumps(x)+'\n' for x in rs).encode()
    (directory/'trajectory.jsonl').write_bytes(encoded)
    d['trajectory_file_sha256']=hashlib.sha256(encoded).hexdigest()
    (directory/'execution_report.json').write_text(json.dumps(d))
    r=m.validate_run(directory,'clear_A',2.)
    assert r['valid'] and not r['stop_with_dwell'] # forged summary cannot override trace
