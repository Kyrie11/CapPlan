"""V13 falsification gates. No remote nuPlan data or CARLA needed."""
from __future__ import annotations
import json,random,tempfile,zipfile
from pathlib import Path
from types import SimpleNamespace
import pytest
from capplan.handoff.temporal_recourse_v12 import analyze
from capplan.handoff.semantic_oracle_v13 import evaluate
from scripts.audit_handoff_lineage_v13 import Bundle,build
from scripts.inspect_capplan_outputs_v13 import safe_inventory
from scripts.audit_handoff_midroute_directed_v13 import analyze_pair

ROOT=Path(__file__).resolve().parents[1]

def base_cases():
    return [json.loads(x) for x in (ROOT/'examples/handoff_temporal_v12.jsonl').read_text().splitlines() if x.strip()]

@pytest.mark.parametrize('case',base_cases(),ids=lambda c:c['case_id'])
def test_v12_matches_independent_integer_grid(case):
    assert analyze(case)['status']==evaluate(case)['status']

def test_clairvoyant_not_causal():
    data=next(x for x in base_cases() if x['case_id']=='none_observable_before_commitment')
    result=evaluate(data)
    assert result['clairvoyant_possible'] and result['nonanticipativity_gap']

def test_same_observation_no_peeking():
    data=next(x for x in base_cases() if x['case_id']=='early_obs_two_sites')
    data['one_observation']['outcomes_by_world']={'wA':'indistinguishable','wB':'indistinguishable'}
    assert evaluate(data)['status']=='NO_POLICY_IN_RESTRICTED_CLASS'
    assert analyze(data)['status']=='NO_POLICY_IN_RESTRICTED_CLASS'

def test_known_robust_third_anchor():
    data=next(x for x in base_cases() if x['case_id']=='robust_fixed_third_site')
    r=evaluate(data)
    assert r['fixed_witness']['option_id']=='C' and not r['nonanticipativity_gap']

def test_integer_reference_refuses_fractional_windows():
    data=base_cases()[0];data['options'][0]['vehicle_stop_windows_s']=[[0,4.5]]
    with pytest.raises(ValueError,match='integer endpoints'):evaluate(data)

def test_random_complete_one_observation_versus_grid():
    for seed in range(60):
        r=random.Random(seed);worlds=['w0','w1','w2'];options=[]
        for k in range(3):
            a=r.randrange(0,8);b=a+r.randrange(0,3)
            options.append({'option_id':f'z{k}','contract_feasible':True,
              'passenger_arrival_window_s':[a,b],
              'post_observation_passenger_arrival_window_s':[max(5,a),max(5,a)+b-a],
              'passenger_arrival_quantifier':r.choice(['all','exists']),
              'passenger_wait_remaining_s':r.randrange(2,8),'boarding_duration_s':r.randrange(1,4),
              'vehicle_stop_windows_s':[[0,r.randrange(10,30)]],
              'evidence_validity_windows_s':[[0,r.randrange(8,30)]],
              'interface_windows_s':[[0,25]],
              'world_handoff_open_windows_s':{w:([[0,25]] if r.random()>.4 else []) for w in worlds}})
        case={'case_id':str(seed),'world_support':worlds,'options':options,
              'one_observation':{'available_at_s':5,'outcomes_by_world':{'w0':'red','w1':'red','w2':'green'}}}
        assert analyze(case)['status']==evaluate(case)['status'],seed

def _edge(u,v,l,id):
    return SimpleNamespace(from_node=u,to_node=v,length_m=l,edge_id=id,
        obstacle=False,obstacle_state='clear',step_free=True,width_m=2,slope=0,
        cross_slope=0,confidence=1)

def _pair():
    return {'episode_id':'s','a_anchor':'A','b_anchor':'B',
      'paths':{'origin_to_a':{'node_ids':['O','P','A'],
                              'edges':[{'edge_id':'op','length_m':50},{'edge_id':'pa','length_m':50}]}},
      'screening_contract':{'min_width_m':1,'max_slope':.1,'max_cross_slope':.1,
                            'require_step_free':True,'min_confidence':0,
                            'already_consumed_m':0,'distance_budget_m':160},
      'distances_m':{'origin_to_a':100},'pedestrian_graph_nodes':{'B':'B'}}

def test_directed_midroute_next_node_branch():
    graph=SimpleNamespace(edges=[_edge('O','P',50,'op'),_edge('P','A',50,'pa'),_edge('P','B',70,'pb'),_edge('A','B',150,'ab')])
    before=analyze_pair(_pair(),graph,20,ack_s=0,speed_mps=1)
    late=analyze_pair(_pair(),graph,90,ack_s=0,speed_mps=1)
    assert before['status']=='DIRECTED_B_GRAPH_BUDGET_FEASIBLE' # 20+30+70=120
    assert late['status']=='B_NOT_REACHABLE_WITHIN_DECLARED_DIRECTED_GRAPH_BUDGET' # 90+10+150
    assert late['is_a_true_global_no_recourse_certificate'] is False

def test_symmetric_mined_path_not_directionally_valid():
    graph=SimpleNamespace(edges=[_edge('P','O',50,'op'),_edge('P','A',50,'pa'),_edge('P','B',70,'pb')])
    res=analyze_pair(_pair(),graph,0,ack_s=0)
    assert res['status']=='UNKNOWN_MINED_PATH_NOT_DIRECTIONALLY_VALIDATED'

def test_readonly_inspector_samples_only_keys(tmp_path):
    root=tmp_path/'outputs';d=root/'datasets'/'abilitybench_av_hybrid_test';d.mkdir(parents=True)
    (root/'prepared').mkdir()
    (d/'episodes.jsonl').write_text('{"episode_id":"PRIVATE_X","city":"vegas"}\n')
    (d/'dataset_manifest.json').write_text('{"config":"private","ready":true}')
    r=safe_inventory(root,max_depth=2,count_episodes=True)
    content=json.dumps(r)
    assert 'PRIVATE_X' not in content and 'private' not in content
    assert any(x['episodes_jsonl_line_count']==1 for x in r['directories'])

def test_lineage_zip_reads_prior_and_current_without_missing_artifacts(tmp_path):
    a=tmp_path/'v11.zip';b=tmp_path/'v12.zip'
    with zipfile.ZipFile(a,'w') as z:
        z.writestr('handoff_science_v11/h4_protocol_robustness.summary.json',json.dumps({'protocols':[
        {'max_wait_ticks':0,'allow_parallel_control_actions':False,'cells':8,'winning_cells':7,'fully_explored':True,'three_way_binary_contrast':-1},
        {'max_wait_ticks':0,'allow_parallel_control_actions':True,'cells':8,'winning_cells':8,'fully_explored':True,'three_way_binary_contrast':0},
        {'max_wait_ticks':1,'allow_parallel_control_actions':False,'cells':8,'winning_cells':8,'fully_explored':True,'three_way_binary_contrast':0}]}))
    with zipfile.ZipFile(b,'w') as z:
        z.writestr('handoff_science_v12/triage_v12.json',json.dumps({'missing_required_input_reports':['h4_protocol']}))
    x=Bundle(a);y=Bundle(b)
    try:r=build(x,y)
    finally:x.close();y.close()
    assert r['H4']['original_counterexample_disappears']
    assert r['prior_triage_missing_due_to_cross_run_input']==['h4_protocol']
    assert not r['input_availability']['all_required_bundles_present']

def test_carla_generator_only_uses_passing_clear(monkeypatch,tmp_path):
    import scripts.plan_carla_matched_v13 as planner
    path=tmp_path/'execution_report.json'
    path.write_text(json.dumps({'experiment_config':{'map':'Carla/Maps/Town03','seed':13,
      'target_speed_kmh':20,'arrival_radius_m':6,'max_seconds':60,'dt':0.05,
      'stop_speed_mps':0.5,'service_stop_hold_s':2,'freeze_traffic_lights':True},
      'geometry_parameters':{'spawn_index':0,'a_steps':2,'b_steps':8}}))
    monkeypatch.setattr(planner,'audit',lambda _: {'trials':[
      {'valid_clear':False,'exact_experiment_config_present':True,'trial':'bad','report':str(path)},
      {'valid_clear':True,'exact_experiment_config_present':True,'trial':'good','report':str(path)}]})
    r=planner.construct(tmp_path,tmp_path/'plan',max_controls=2)
    assert r['controls_selected']==1 and r['jobs_planned']==3
    assert all('bad' not in j['trial'] for j in r['jobs'])
    assert not (tmp_path/'plan'/'blocked_A'/'good').exists()
    assert 'blocked_A_redirect_B' in (tmp_path/'plan'/'run_matched_carla_v13.sh').read_text()
    assert 'check_single_clear_v13.py' in (tmp_path/'plan'/'run_matched_carla_v13.sh').read_text()
