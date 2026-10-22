"""Soundness tests for exact finite-model experimental comparators.

These tests do not establish any real curb/boarding guarantees.
"""
from __future__ import annotations
import json
import random
from pathlib import Path
from types import SimpleNamespace

from capplan.handoff.baselines import compare_case, analyze_files
from capplan.handoff.research import inspect_graph_pair, run_graph_screen
from capplan.handoff.review_gates import review
from scripts.benchmark_observation_v13 import make
from scripts.handoff_study import main

ROOT=Path(__file__).resolve().parents[1]


def controls():
    return [json.loads(s) for s in (ROOT/'examples/handoff_protocol_controls.jsonl').read_text().splitlines()]


def test_marginal_false_accept_when_handoff_start_does_not_overlap():
    v=compare_case(controls()[1]);assert v['grid_interval_agree']
    assert v['marginal_accepting_actions']==['A']
    assert v['joint_safe_fixed_actions']['safe_actions']==[]
    assert v['safe_fixed_action_sets_agree']
    assert v['exact_restricted_interval_status']=='NO_POLICY_IN_RESTRICTED_CLASS'


def test_full_boarding_duration_required():
    v=compare_case(controls()[2]);assert v['grid_interval_agree']
    assert v['marginal_false_fixed_commitment_candidates']==['A']


def test_positive_controls_do_not_show_false_rejection():
    for c in (controls()[0],controls()[3]):
        v=compare_case(c)
        assert not v['marginal_false_fixed_commitment_candidates']
        assert v['joint_safe_fixed_actions']['safe_actions']
        assert v['safe_fixed_action_sets_agree']


def test_random_safe_action_set_equals_completely_independent_integer_grid():
    for j in range(70):
        case=make(j+41,80,4,3)
        v=compare_case(case)
        assert v['safe_fixed_action_sets_agree'],j
        assert v['grid_interval_agree'],j


def test_nonanticipativity_gap_is_not_a_failure_of_generic_shield():
    data=[json.loads(x) for x in (ROOT/'examples/handoff_temporal_v12.jsonl').read_text().splitlines()]
    q=next(x for x in data if x['case_id']=='none_observable_before_commitment')
    v=compare_case(q)
    assert v['nonanticipativity_gap'] and not v['joint_safe_fixed_actions']['safe_actions']
    assert v['exact_restricted_interval_status']=='NO_POLICY_IN_RESTRICTED_CLASS'


def test_direction_missing_branch_never_throws_keyerror():
    graph=SimpleNamespace(edges=[])
    p={'episode_id':'z','map_name':'sg-one-north','a_anchor':'A','b_anchor':'B',
       'pedestrian_graph_nodes':{'origin':'O','A':'A','B':'B'},
       'screening_contract':{'min_width_m':0.8,'max_slope':0.1,
         'max_cross_slope':0.04,'min_confidence':0,'require_step_free':True,
         'distance_budget_m':180}}
    a=inspect_graph_pair(p,graph,[0],speed_mps=0.8,ack_s=3, directed=True)
    assert a['direction_mode']=='AS_ENCODED'
    assert a['status']=='NO_TWO_DIRECT_PATHS_UNDER_ENCODING'
    assert a['real_physical_handoff_verified'] is False


def test_remine_denominators_even_when_original_path_missing(tmp_path,monkeypatch):
    from capplan.data import accessibility_layer
    p={'episode_id':'z','map_name':'us-ma-boston','a_anchor':'A','b_anchor':'B',
       'physical_site_pair':['p1','p2'],
       'pedestrian_graph_nodes':{'origin':'O','A':'A','B':'B'},
       'screening_contract':{'min_width_m':0.8,'max_slope':0.1,
         'max_cross_slope':0.04,'min_confidence':0,'require_step_free':True,
         'distance_budget_m':180}}
    dataset=tmp_path/'dataset'; dataset.mkdir()
    pairs=tmp_path/'pairs.jsonl';pairs.write_text(json.dumps(p)+'\n')
    monkeypatch.setattr(accessibility_layer,'load_accessibility_graph',
                        lambda directory, episode:SimpleNamespace(edges=[]))
    summary=run_graph_screen(dataset,pairs,tmp_path/'out.jsonl',per_city=1)
    assert not summary['errors']
    assert summary['original_selected_pairs']==summary['evaluated_pair_count']==1
    assert summary['direction_evaluation_rows_written']==2
    assert summary['counts']['AS_ENCODED::NO_TWO_DIRECT_PATHS_UNDER_ENCODING']==1
    assert summary['counts']['OPTIMISTIC_SYMMETRIZED::NO_TWO_DIRECT_PATHS_UNDER_ENCODING']==1


def test_research_gate_does_not_infer_real_incidence(tmp_path):
    (tmp_path/'directed_remine.summary.json').write_text(json.dumps({
      'original_selected_pairs':1,'counts':{'AS_ENCODED::DIRECTED_TERMINAL_AB_ASYMMETRY_EXISTS':1}}))
    r=review(tmp_path)
    assert r['H2']['incidence_estimable'] is False
    assert r['H1']['independent_real_handoff_count']==0
    assert r['H3']['published_shield_implementation_reproduced'] is False
    assert r['manifest_errors']==['MANIFEST_NOT_PRESENT']


def test_cli_writes_structured_comparison(tmp_path):
    out=tmp_path/'baseline_comparison.jsonl'
    assert main(['baseline-audit','--cases',str(ROOT/'examples/handoff_protocol_controls.jsonl'),
                  '--output',str(out)])==0
    r=json.loads(out.with_suffix('.summary.json').read_text())
    assert r['mismatch_count']==0 and r['counts']['marginal_accept_no_robust_fixed']==2


def test_five_way_control_requires_b_direct_before_blame_on_blocker(tmp_path):
    from capplan.handoff.carla_experiments import audit
    from capplan.handoff.carla_calibration_v12 import audit as calibration
    cases=['clear_A','blocked_A','clear_B','clear_A_redirect_B','redirect_B']
    geom={'start':[0,0,0],'A':[10,0,0],'B':[30,0,0]}
    for mode in cases:
        path=tmp_path/mode/'trial0';path.mkdir(parents=True)
        is_b=mode in ('clear_B','clear_A_redirect_B','redirect_B')
        config={'map':'Town03','seed':13,'a_steps':2,'b_steps':5,'spawn_index':0,
                'service_stop_hold_s':2.0,'stop_speed_mps':0.5,'redirect_at_s':8}
        record={'mode': 'blocked_A_redirect_B' if mode=='redirect_B' else mode,
          'goal':'B' if is_b else 'A','carla_map':'Carla/Maps/Town03','seed':13,
          'status':'COMPLETED','collisions':0,'experiment_config':config,
          'geometry_parameters':{'arrival_radius_m':6,'spawn_index':0,'a_steps':2,'b_steps':5},
          'carla_locations':geom,'service_stop_hold_s_required':2,
          'stop_speed_threshold_mps':0.5,'sample_dt_s':1}
        if 'redirect' in mode:record['redirect_command_applied']=True
        (path/'execution_report.json').write_text(json.dumps(record))
        target='B' if is_b else 'A'
        history=['A','B','B','B'] if 'redirect' in mode else [target]*4
        (path/'trajectory.jsonl').write_text(''.join(
            json.dumps({'elapsed_s':i,'target':t,'distance_to_goal_m':3,
                        'speed_mps':0})+'\n' for i,t in enumerate(history)))
    res=audit(tmp_path)
    assert res['counts']['complete_fiveway_controls']==1
    assert res['counts']['FIVE_WAY_B_STOP_SURROGATE_PASS']==1
    # Corrupt direct B. A blocked-B failure alone must not be blamed on blocker.
    b=tmp_path/'clear_B'/'trial0'/'execution_report.json'
    d=json.loads(b.read_text());d['goal']='A';b.write_text(json.dumps(d))
    res=audit(tmp_path)
    assert res['counts']['B_DIRECT_CONTROL_FAILED']==1
    assert not res['physical_handoff_verified']


def test_extended_carla_plan_is_only_plan_and_adds_two_fair_controls(tmp_path,monkeypatch):
    from scripts.plan_carla_matched_v13 import construct
    import capplan.handoff.carla_calibration_v12 as cal
    clear=tmp_path/'clear_A'/'trial0';clear.mkdir(parents=True)
    rep={'experiment_config':{'map':'Town03','seed':13,'target_speed_kmh':20,
        'arrival_radius_m':4,'max_seconds':50,'dt':0.05,
        'service_stop_hold_s':2,'stop_speed_mps':0.5},
         'geometry_parameters':{'spawn_index':0,'a_steps':2,'b_steps':4}}
    file=clear/'execution_report.json';file.write_text(json.dumps(rep))
    monkeypatch.setattr(cal,'audit',lambda p:{'trials':[
        {'trial':'trial0','valid_clear':True,'exact_experiment_config_present':True,
         'report':str(file)}]})
    from scripts import plan_carla_matched_v13 as mod
    monkeypatch.setattr(mod,'audit',cal.audit)
    result=construct(tmp_path/'clear_A',tmp_path/'planned',max_controls=1,extended_controls=True)
    assert result['jobs_planned']==5 and result['status']=='PLAN_ONLY_NOT_EXECUTED'
    assert result['extended_controls'] is True
    command=(tmp_path/'planned'/'run_matched_carla_v13.sh').read_text()
    assert '--mode clear_B' in command
    assert '--mode clear_A_redirect_B' in command
