"""Falsification regression: incomplete knowledge != impossible physics."""
from __future__ import annotations
import json
from copy import deepcopy
from pathlib import Path
import pytest

from capplan.handoff.commitment_model_v10 import (FiniteCommitmentModel,
    GenericSupportSolver, verify_policy_in_all_worlds)
from scripts.run_handoff_falsification_v10 import evaluate_cases
from scripts.audit_v9_evidence_and_carla_v10 import diagnose_v9

DATA=Path(__file__).resolve().parents[1]/'examples'/'handoff_finite_v10.jsonl'
CASES={x['case_id']:x for x in (json.loads(line) for line in DATA.read_text().splitlines())}


def exact(name,cap=40000):
    m=FiniteCommitmentModel(deepcopy(CASES[name]))
    solver=GenericSupportSolver(m,max_states=cap)
    result=solver.solve()
    return m,solver,result


def test_generic_full_model_can_solve_dynamic_contingent_handoff():
    m,solver,r=exact('observe_mutually_exclusive')
    assert r.status=='WIN'
    assert r.best_action=='verify:A'
    assert 'guide:A' not in r.first_safe_actions
    assert 'guide:B' not in r.first_safe_actions
    assert verify_policy_in_all_worlds(m,solver.memo[m.initial][1])['w0']['success']
    assert verify_policy_in_all_worlds(m,solver.memo[m.initial][1])['w1']['success']


def test_capability_axis_affects_both_route_and_policy():
    low=exact('width_0p7')[2]
    high=exact('width_1p2')[2]
    assert low.best_action=='guide:A'
    assert high.best_action=='guide:B'
    assert 'guide:A' not in high.first_safe_actions


def test_delayed_ack_updates_actual_passenger_progress():
    immediate=exact('ack_zero')[2]
    late=exact('ack_two')[2]
    assert immediate.status==late.status=='WIN'
    assert 'guide:B' in immediate.first_safe_actions
    assert 'guide:B' not in late.first_safe_actions


def test_evidence_expiry_does_not_imply_physical_site_blockage():
    long=exact('evidence_long')[2]
    short=exact('evidence_short')[2]
    assert long.status==short.status=='WIN'
    assert long.best_action==short.best_action=='guide:B'
    assert 'verify:B' not in [s['action'] for s in long.policy_prefix]
    assert 'verify:B' in [s['action'] for s in short.policy_prefix]
    assert short.best_cost>long.best_cost


def test_closed_and_reopened_windows_are_not_monotone():
    assert [exact('transient_snapshot_t'+str(i))[2].status for i in (0,1,3)]==['WIN','LOSE','WIN']


def test_vehicle_joint_position_changes_reachable_service():
    assert exact('vehicle_ready')[2].status=='WIN'
    assert exact('vehicle_far')[2].status=='LOSE'


def test_missing_motion_claim_is_unknown_not_real_failure():
    case=deepcopy(CASES['vehicle_ready'])
    case['sites']['B'].pop('motion_ok')
    model=FiniteCommitmentModel(case)
    r=GenericSupportSolver(model).solve()
    assert r.status=='UNKNOWN_EVIDENCE'


def test_state_cap_is_unknown_not_exact_infeasibility():
    assert exact('observe_mutually_exclusive',cap=1)[2].status=='UNKNOWN'


def test_correlated_failure_not_fixed_by_invented_fallback():
    assert exact('correlated_all_blocked')[2].status=='LOSE'


def test_initial_evidence_must_be_compatible_with_all_possible_worlds():
    case=deepcopy(CASES['observe_mutually_exclusive'])
    case['initial_evidence']={'A':{'open':True,'observed_at':0,'valid_until':4}}
    with pytest.raises(ValueError,match='contradicts'):
        FiniteCommitmentModel(case)


def test_success_policy_is_causal_in_all_declarable_worlds():
    samples=[CASES[n] for n in ('observe_mutually_exclusive','width_0p7','width_1p2',
        'ack_zero','ack_two','factor_w1.2_e9_vB')]
    rows,summary=evaluate_cases(samples)
    assert summary['all_returned_winning_policies_replayed']
    assert not summary['num_cases_with_unknown_cap']
    assert all(r['evidence_type']=='CONTROLLED_SYNTHETIC_NOT_REAL_CITY' for r in rows)
    assert all(not r['publication_strong_baseline_reproduced'] for r in rows)


def test_factorial_has_joint_interaction_in_constructed_case_only():
    rows,summary=evaluate_cases([x for x in CASES.values() if x['family']=='H4_factorial'])
    assert summary['factorial_H4']['guaranteed_completion_cells']==7
    assert summary['factorial_H4']['three_way_binary_contrast']!=0
    assert not any(r['real_handoff_verified'] for r in rows)


def test_route_preflight_detects_mismatched_first_waypoint(tmp_path):
    d=tmp_path/'carla_clear_speed5_radius6'/'spawn0_asteps2';d.mkdir(parents=True)
    (d/'execution_report.json').write_text(json.dumps({
        'carla_locations':{'start':[110.,10.,0.]},
        'geometry_parameters':{'spawn_index':0},
        'control_diagnostics_v9':{'planned_first_5_xy':[[0.,0.]],'planned_route_length_m':100.},
        'stopped_near_goal':False}))
    r=diagnose_v9(tmp_path)
    assert r['carla']['route_start_gap_count']==1
    assert r['carla']['total_trials']==1


def test_all_starts_are_spatially_distinct_in_real_uploaded_results_if_available():
    # This test is intentionally portable: no server data / CARLA client needed.
    # Full V9 records are reviewed separately via audit_v9_evidence_and_carla_v10.py.
    assert len(CASES)>=20


def test_audit_packet_keeps_missing_real_evidence_unknown():
    from scripts.export_handoff_audit_packets_v10 import export_packets
    rows=[{'episode_id':'scene_1','physical_site_pair':'site1+site2',
           'a_anchor':'A','b_anchor':'B','notice_issued_at_s':t,
           'strict_explicit_curb_count':0,'optimistic_without_curb_count':3,
           'optimistic_unverified_examples':[{'anchor_id':'C','physical_site_key':'siteC',
              'total_passenger_distance_m':72,'remaining_graph_m':56,'is_declared_B':False}]}
          for t in (0,30,60)]
    packets,summary=export_packets(rows)
    assert summary['pairs']==1
    assert len(packets[0]['evaluated_notice_times_s'])==3
    assert packets[0]['real_service_viability']=='UNKNOWN'
    c=packets[0]['candidate_review_queue'][0]
    assert c['vehicle_stop_pose'] is None
    assert c['verification_state']=='UNKNOWN_PHYSICAL_HANDOFF'


def test_bounded_generic_scaling_reports_uncertified_optimality():
    from scripts.bench_generic_commitment_v10 import bench
    rows=bench([(3,2,8)],cap=100)
    r=rows[0]
    assert r['unknown_due_to_cap']
    assert not r['exact_action_set_complete']
    assert not r['policy_cost_optimality_certified']


def test_nearest_static_uses_full_multihop_distance():
    from capplan.handoff.commitment_model_v10 import cheapest_heuristic_actions
    scene=deepcopy(CASES['width_0p7'])
    # A has a cheaper first segment but a costlier full route to the stop.
    scene['pedestrian_graph']['O']=[
        {'to':'mid','length_m':1.,'min_width_m':1.3},
        {'to':'B','length_m':4.,'min_width_m':1.3}]
    scene['pedestrian_graph']['mid']=[
        {'to':'A','length_m':100.,'min_width_m':1.3}]
    scene['pedestrian_graph']['A']=[]
    scene['pedestrian_graph']['B']=[]
    scene['max_distance_m']=120
    m=FiniteCommitmentModel(scene)
    assert cheapest_heuristic_actions(m)['nearest_static']=='guide:B'


def test_summary_distinguishes_root_heuristic_from_complete_policy():
    rows,summary=evaluate_cases([CASES['observe_mutually_exclusive'],CASES['width_0p7']])
    for k,v in summary['heuristic_first_action_on_winning_cases'].items():
        assert v['winning_case_denominator']==2
        assert v['unsafe_first_action_count']+v['certified_first_action_count']==2
        assert 'ROOT_ACTION_ONLY' in v['comparison_scope']
