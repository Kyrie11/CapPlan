"""The V9 diagnostic layer must avoid upgrading missing evidence to physical truth."""
from __future__ import annotations
import json
from pathlib import Path
from types import SimpleNamespace
from scripts.audit_handoff_coupled_windows_v9 import assess_option, assess_case
from scripts.audit_handoff_alternative_evidence_v9 import screen_pair
from scripts.audit_handoff_carla_traces_v9 import diagnose


def option():
    return {
        'option_id':'A', 'distance_from_actionable_position_m':40,
        'vehicle_arrival_earliest_s':25, 'vehicle_arrival_latest_s':70,
        'vehicle_max_dwell_s':30, 'boarding_duration_s':20,
        'curb_window_start_s':0, 'curb_window_end_s':150,
        'interface_executable':True,'vehicle_pose_executable':True,
        'evidence_certificate':{'from_s':0,'through_s':90,'guarantees_service_interval':True}}


def passenger():
    return {'already_consumed_m':20,'distance_budget_m':100,
            'walking_speed_mps':1,'ack_delay_s':10,'wait_used_s':0,'wait_budget_s':30}


def test_joint_window_closes_even_if_all_marginals_feasible():
    early=assess_option(passenger(),option(),0)
    late=assess_option(passenger(),option(),40)
    assert early['decision']=='MODEL_WINDOW_EXISTS'
    assert late['decision']=='MODEL_WINDOW_EMPTY'
    assert late['all_marginals_feasible']
    assert late['joint_window_empty_despite_feasible_marginals']


def test_missing_ttl_does_not_become_valid():
    o=option();del o['evidence_certificate']
    assert assess_option(passenger(),o,0)['decision']=='UNKNOWN_EVIDENCE_VALIDITY'
    case={'episode_id':'synthetic','passenger':passenger(),
          'handoffs':[o],'notice_times_s':[0,30]}
    rows=assess_case(case)
    assert all(r['decision']=='UNKNOWN' and not r['can_claim_real_handoff'] for r in rows)


def test_real_profile_changes_decision_in_controlled_model():
    p=passenger();p['distance_budget_m']=30
    assert assess_option(p,option(),0)['decision']=='MODEL_WINDOW_EMPTY'
    assert assess_option(passenger(),option(),0)['decision']=='MODEL_WINDOW_EXISTS'


def test_dual_curb_screen_does_not_claim_missing_connector_is_impossible():
    def edge(u,v,length,curb=False):
        return SimpleNamespace(from_node=u,to_node=v,length_m=length,
                obstacle=False,obstacle_state=None,step_free=True,width_m=2,
                slope=0,cross_slope=0,confidence=1,
                crossing_type='curb' if curb else 'sidewalk')
    graph=SimpleNamespace(nodes=[SimpleNamespace(node_id=n) for n in ('O','M','A','B')],
        edges=[edge('O','M',10),edge('M','A',10),edge('M','B',3)])
    pair={'episode_id':'synthetic','a_anchor':'A','b_anchor':'B',
          'distances_m':{'origin_to_a':20},'reroute_total_m':50,
          'screening_contract':{'distance_budget_m':40,'already_consumed_m':0,
             'min_width_m':1,'max_slope':.1,'max_cross_slope':.1},
          'paths':{'origin_to_a':{'node_ids':['O','M','A'],
                                'edges':[{'length_m':10},{'length_m':10}]}}}
    anchors=[{'anchor_id':x,'kind':'pickup','legal_stop':True,
              'blockage_risk':0,'adjacent_ped_node_id':x,
              'hybrid_physical_site_key':x} for x in ('A','B')]
    row=screen_pair(pair,anchors,graph,[0],1,10)[0]
    assert row['strict_explicit_curb_count']==0
    assert row['optimistic_without_curb_count']==1
    assert row['graph_evidence_class']=='ONLY_OPTIMISTIC_UNVERIFIED_CONNECTOR_CANDIDATES'
    assert not row['physical_handoff_verified']


def test_carla_diagnoses_report_without_trusting_success_label(tmp_path):
    d=tmp_path/'spawn0_asteps4';d.mkdir()
    rows=[{'elapsed_s':i*0.1,'x':i*.1,'y':0,'speed_mps':2,
           'distance_to_goal_m':abs(5-i*.1)} for i in range(100)]
    (d/'trajectory.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    (d/'execution_report.json').write_text(json.dumps({'geometry_parameters':{'spawn_index':0,'a_steps':4},
         'sample_dt_s':.1,'collisions':0,'stopped_near_goal':True}))
    r=diagnose(tmp_path)
    assert r['preflight_gate']=='FAIL_NO_VALID_CLEAR_STOP'
    assert r['counts']['moved_away_after_approach']==1
