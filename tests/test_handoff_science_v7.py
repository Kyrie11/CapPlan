import importlib.util, hashlib, json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]/'scripts'
def module(name):
    s=importlib.util.spec_from_file_location(name,ROOT/(name+'.py'));m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

def test_carla_paired_negative_control_gate():
    m=module('run_carla_paired_v7')
    def r(mode,stop,valid=True,collision=0):
        return {'mode':mode,'valid':valid,'scene_empty_at_start':True,'geometry':{'A':[1,2],'B':[3,4]},
                'parameters':{'spawn_index':0},'stop_with_dwell':stop,'collision_events':collision,
                'goal':'B' if 'redirect' in mode else 'A','traffic_signals_controlled':True}
    cases={'clear_A':r('clear_A',False),'blocked_A':r('blocked_A',False),'blocked_A_redirect_B':r('blocked_A_redirect_B',True)}
    assert m.evaluate_triplet(cases,2)['status']=='INVALID_CLEAR_STOP_CONTROL'
    cases['clear_A']=r('clear_A',True)
    assert m.evaluate_triplet(cases,2)['status']=='CARLA_VEHICLE_ONLY_CONTROLLED_REDIRECTION'
    assert not m.evaluate_triplet(cases,2)['independently_verified_curb_or_passenger']
    cases['blocked_A']=r('blocked_A',False,collision=1)
    assert m.evaluate_triplet(cases,2)['status']=='BLOCKED_RUN_COLLISION_OR_UNSAFE'

def test_carla_trace_validation(tmp_path):
    m=module('run_carla_paired_v7');path=tmp_path/'clear_A';path.mkdir()
    trace=(json.dumps({'elapsed_s':0,'distance_to_goal_m':4})+'\n'+json.dumps({'elapsed_s':.05,'distance_to_goal_m':3})+'\n').encode()
    (path/'trajectory.jsonl').write_bytes(trace)
    (path/'execution_report.json').write_text(json.dumps({'mode':'clear_A','status':'COMPLETED',
        'trajectory_file_sha256':hashlib.sha256(trace).hexdigest(),'stationary_service_stop_observed':True,
        'service_stop_best_dwell_s':2.0,'collisions':0,'goal':'A','other_vehicles_at_start':0}))
    assert m.validate_run(path,'clear_A',2)['stop_with_dwell']
    assert not m.validate_run(path,'clear_A',3)['stop_with_dwell']
    (path/'trajectory.jsonl').write_text('broken\n')
    # Corrupted sensor trajectory must be rejected, not accepted as a claim.
    try:m.validate_run(path,'clear_A',2)
    except json.JSONDecodeError:pass
    else:assert False,'corrupt record should not pass'

def test_handoff_chain_ignores_simulated_labels(tmp_path):
    m=module('audit_handoff_chain_v7')
    paths=[]
    for name,records in [('sites',[{'episode_id':'e','site_id':'e:A','site_key':'p','city':'boston','pose':{'x':0,'y':0}}]),
                         ('anchors',[{'episode_id':'e','hybrid_physical_site_key':'p','stop_pose':{'x':1,'y':0},'curb_pose':{'x':1,'y':0},'legal_stop':True,'field_provenance':{'legal_stop':{'kind':'simulated'}}}])]:
        f=tmp_path/(name+'.jsonl');f.write_text(''.join(json.dumps(x)+'\n' for x in records));paths.append(f)
    logs=tmp_path/'logs.json';logs.write_text(json.dumps({'episodes':[{'episode_id':'e','status':'EXACT_LIDAR_TOKEN_BOUND','site_results':[]}]}))
    cal=tmp_path/'cal.json';cal.write_text(json.dumps({'calibrated_goal_zone_candidates':[{'stopped_near_goal':False}]}))
    d=m.audit(*paths,logs,cal,tmp_path/'out.json')
    assert d['summary']['field_counts']['legal_stop_simulated']==1
    assert d['summary']['independently_confirmed_pudo_count']==0
    assert d['sites'][0]['real_legal_pudo_verified'] is False
    assert d['summary']['carla_stopped_candidates']==0
