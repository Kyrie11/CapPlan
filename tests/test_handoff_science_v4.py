import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.audit_handoff_midroute import evaluate_pair
from scripts.audit_handoff_evidence_v4 import source_kind,same_pose
from scripts.replay_handoff_live_state_v4 import replay
from scripts.audit_handoff_alternatives_v4 import evaluate as alternatives


def graph():
    E=lambda a,b,l:SimpleNamespace(from_node=a,to_node=b,length_m=l,obstacle=False,
      obstacle_state=None,step_free=True,width_m=2.0,slope=0.01,cross_slope=0.01,confidence=1.0)
    return SimpleNamespace(edges=[E('O','A',60),E('O','B',65),E('A','B',75),E('A','C',15)],
                            nodes=[SimpleNamespace(node_id=n) for n in 'OABC'])

def pair():
    return {'episode_id':'ep','a_anchor':'pa','b_anchor':'pb','map_name':'us-ma-boston',
      'paths':{'origin_to_a':{'node_ids':['O','A'],'edges':[{'length_m':60}]}},
      'distances_m':{'origin_to_a':60,'origin_to_b':65},'pedestrian_graph_nodes':{'A':'A','B':'B'},
      'screening_contract':{'require_step_free':True,'min_width_m':1,'max_slope':0.1,
         'max_cross_slope':0.05,'distance_budget_m':80},'reroute_total_m':135}

def test_acknowledgement_time_progresses_not_rewinds():
    p=pair();g=graph()
    immediate=evaluate_pair(p,g,10,speed_mps=1,ack_s=0)
    delayed=evaluate_pair(p,g,10,speed_mps=1,ack_s=10)
    assert immediate['progress_m']==10
    assert delayed['progress_m']==20
    assert delayed['notice_actionable_at_s']==20
    paused=evaluate_pair(p,g,10,speed_mps=1,ack_s=10,pause_during_ack=True)
    assert paused['progress_m']==10
    assert delayed['best_possible_total_distance_to_B_m']>=immediate['best_possible_total_distance_to_B_m']

def test_alternative_does_not_claim_full_recovery():
    a={'anchor_id':'pc','kind':'pickup','adjacent_ped_node_id':'C','legal_stop':True}
    out=alternatives(pair(),graph(),[a])
    assert out['distance_feasible_other_anchors_count']==1
    assert out['examples'][0]['curb_interface_independently_verified'] is False

def test_field_provenance_not_truth():
    assert source_kind({'kind':'derived','method':'same_physical_site_static_evidence_transfer'})=='derived_site_transfer'
    assert source_kind({'kind':'simulated'})=='simulated'
    assert same_pose({'x':1,'y':2,'heading':0},{'x':1,'y':2,'heading':0}) is True

def test_replay_holds_outdated_or_unverified_evidence(tmp_path):
    p={'profile_id':'p','consent_scope':'trip_planning',
       'mobility':{'max_access_distance_m':20},'wait':{'max_wait_exposure_s':5}}
    ev=tmp_path/'events.jsonl'
    ev.write_text('\n'.join(map(json.dumps,[
       {'timestamp_s':0,'type':'observe','field':'curb_connector','value':True,'valid_until_s':5,
        'source':'simulation','evidence_class':'simulated'},
       {'timestamp_s':6,'type':'move','distance_m':10,'source':'wheel_sensor'},
       {'timestamp_s':7,'type':'wait','duration_s':7}
    ]))+'\n')
    o=tmp_path/'out.jsonl'; s=replay(p,ev,o)
    rows=[json.loads(x) for x in o.read_text().splitlines()]
    assert s['events']==3
    assert rows[1]['remaining_distance_m']==10
    assert 'curb_connector' in rows[0]['unverified_fields']
    assert rows[2]['screening_status']=='HOLD_RESOURCE_VIOLATION'
    assert all(not x['can_issue_guidance_from_this_replay'] for x in rows)

def test_nonmonotone_events_fail(tmp_path):
    ev=tmp_path/'events.jsonl'
    ev.write_text('\n'.join(map(json.dumps,[
       {'timestamp_s':2,'type':'wait','duration_s':0},
       {'timestamp_s':1,'type':'wait','duration_s':0}]))+'\n')
    with pytest.raises(ValueError):
        replay({'profile_id':'p','consent_scope':'trip_planning',
                'mobility':{'max_access_distance_m':20},'wait':{'max_wait_exposure_s':5}},ev,tmp_path/'out')
