"""Regression tests for claim boundaries, causal timing and research controls."""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import pytest
from capplan.data.schemas import AccessibilityNode, AccessibilityGraph
from capplan.handoff.audited_curb_overlay import apply_reviewed_connectors
from capplan.handoff.evidence import packet_valid, packet_covers_event
from capplan.handoff.finite_oracle import FiniteHandoffOracle,evaluate_baselines

ROOT=Path(__file__).resolve().parents[1]


def mod(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/f'{name}.py')
    obj=importlib.util.module_from_spec(spec);spec.loader.exec_module(obj);return obj


def pair():
    return {'episode_id':'e','a_anchor':'A','b_anchor':'B','origin_anchor':'O',
            'distances_m':{'origin_to_a':130.,'origin_to_b':170.,'a_to_b':120.},
            'screening_contract':{'distance_budget_m':200.,'already_consumed_m':0},
            'two_candidate_distance_and_vehicle_witness':True,
            'a_to_b_vehicle_motion_packet':{'reachable':True,'collision_free':True,
                'legal_stop':True,'interface_verified':True,'method_specific_motion_checked':True,
                'motion_duration_s':15,'valid_start_s':80.,'valid_end_s':120.,'source':'test checker'},
            'motion_checked_event_time_s':90.,'event':{'event_time_s':90},
            'physical_site_pair':['site-a','site-b']}


def test_motion_interval_is_checked_against_actual_sidecar_event_not_miner_clock():
    build=mod('build_handoff_temporal_overlays').build_overlay
    checked=build(pair(),0,90,5,3,.5,event_timing='fixed')
    assert checked['motivation_witness']['vehicle_a_to_b_verified_for_event_time']
    moved=build(pair(),0,90,5,3,.5,event_timing='before_a_arrival',event_lead_s=3)
    assert moved['motivation_witness']['counterfactual_event_time_s']==130
    assert moved['motivation_witness']['vehicle_motion_packet_present']
    assert not moved['motivation_witness']['vehicle_a_to_b_verified_for_event_time']


def test_missing_nonfinite_or_self_contradictory_motion_packets_fail_closed():
    p=pair()['a_to_b_vehicle_motion_packet']
    assert packet_valid(p)
    assert packet_covers_event(p,100)
    assert not packet_covers_event(p,106) # duration passes 120
    for k,value in [('valid_end_s',float('nan')),('motion_duration_s',-1),
                    ('source',''),('collision_free',None)]:
        q=dict(p);q[k]=value
        assert not packet_valid(q)


def test_falsification_distinguishes_fixed_anchor_but_not_verify_then_guide():
    make=mod('build_handoff_falsification_suite').make_variant
    o=FiniteHandoffOracle(make(pair(),0,'two_sided_observable'))
    res=evaluate_baselines(o)
    assert res['exact_finite_support']['world_support_guaranteed']
    assert res['verify_then_guide']['world_support_guaranteed']
    assert not res['robust_fixed_anchor']['world_support_guaranteed']
    assert res['robust_fixed_anchor']['weighted_candidate_success_rate']==.5
    u=evaluate_baselines(FiniteHandoffOracle(make(pair(),0,'two_sided_unobservable')))
    assert not u['exact_finite_support']['world_support_guaranteed']
    assert u['exact_finite_support']['weighted_candidate_success_rate']==0


def test_audited_connector_requires_external_review_and_correct_site():
    episode={'episode_id':'e'}
    anchors=[{'episode_id':'e','anchor_id':'A','adjacent_ped_node_id':'p',
              'hybrid_physical_site_key':'SITE','curb_pose':{'x':1.,'y':0.}}]
    graph=AccessibilityGraph('e',[AccessibilityNode('p',0.,0.,'sidewalk')],[])
    review={'episode_id':'e','anchor_id':'A','ped_node_id':'p', 'physical_site_key':'SITE',
            'curb_x':1.,'curb_y':0.,'connector_length_m':1.2,
            'width_m':1.5,'slope':.02,'cross_slope':.01,'step_free':True,
            'curb_ramp':True,'evidence_basis':'observed','source_reference':'municipal_evidence_ID',
            'reviewer_id':'independent_auditor','reviewed_at':'2026-10-13','review_decision':'PASS'}
    updated,new,stats=apply_reviewed_connectors(episode,anchors,graph,[review])
    assert stats['review_records_applied']==1
    assert updated[0]['adjacent_ped_node_id']!='p'
    assert len(graph.edges)==0 and len(new.edges)==1 # no modification of input
    assert new.edges[0].metadata['reviewed_record_not_independent_ground_truth']
    with pytest.raises(ValueError,match='physical-site'):
        apply_reviewed_connectors(episode,anchors,graph,[{**review,'physical_site_key':'OTHER'}])
    with pytest.raises(ValueError,match='no approved'):
        apply_reviewed_connectors(episode,anchors,graph,[{**review,'evidence_basis':'simulated'}])


def test_coupled_profile_design_thresholds_nested_and_nonpopulation():
    r=mod('build_handoff_profile_suite').coupled_quantile(13,4)
    assert len(r)==32
    a=r[:4]
    assert all(x['sampling_weight'] is None and not x['population_representative'] for x in a)
    assert [p['mobility']['max_access_distance_m'] for p in a]==sorted(
        p['mobility']['max_access_distance_m'] for p in a)
    assert [p['mobility']['min_clear_width_m'] for p in a]==sorted(
        (p['mobility']['min_clear_width_m'] for p in a),reverse=True)
