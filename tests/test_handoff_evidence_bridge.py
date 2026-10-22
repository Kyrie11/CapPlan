"""Fail-closed bridge tests: no hybrid/synthetic data may be called real proof."""
import json
from pathlib import Path

import pytest

from capplan.handoff.evidence_bridge import (
    export_progress_packets, controlled_factorial, audit_dataset_sources,
)
from capplan.handoff.review_gates import review


def jsonl(path, records):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(''.join(json.dumps(x)+'\n' for x in records),encoding='utf-8')


def fixture(tmp_path):
    folder=tmp_path/'dataset'
    pair={
        'episode_id':'nuplan_001','a_anchor':'A','b_anchor':'B',
        'screening_contract':{'distance_budget_m':100.0,'already_consumed_m':0},
        'map_name':'us-ma-boston'}
    rp=tmp_path/'remine.jsonl'
    jsonl(rp,[{
        'episode_id':'nuplan_001','a_anchor':'A','b_anchor':'B','city':'boston',
        'direction_mode':'AS_ENCODED','status':'DIRECTED_TERMINAL_AB_ASYMMETRY_EXISTS',
        'O_A_m':50,'O_B_m':80,'A_B_m':90,
        'path_O_A_edges':[{'edge_id':'edge_a','from':'O','to':'A','length_m':50}],
        'midroute':[{'notice_issued_s':0,'actionable_at_s':3,
                     'distance_consumed_m':0,'distance_to_B_m':80,
                     'B_within_distance_budget':True,'third_graph_feasible_count':0},
                    {'notice_issued_s':15,'actionable_at_s':18,
                     'distance_consumed_m':20,'distance_to_B_m':70,
                     'B_within_distance_budget':True,'third_graph_feasible_count':1}]
    }])
    pairs=tmp_path/'pairs.jsonl';jsonl(pairs,[pair])
    jsonl(folder/'pudo_anchors.jsonl',[
       {'episode_id':'nuplan_001','anchor_id':'A','hybrid_physical_site_key':'site_A',
        'legal_stop':True,'field_provenance':{'legal_stop':{'kind':'simulated'}}},
       {'episode_id':'nuplan_001','anchor_id':'B','hybrid_physical_site_key':'site_B',
        'legal_stop':True,'field_provenance':{'legal_stop':{'kind':'observed'}}}
    ])
    return rp,pairs,folder


def test_bridge_provenance_and_budget(tmp_path):
    rp,pairs,folder=fixture(tmp_path)
    out=tmp_path/'packets.jsonl'
    result=export_progress_packets(remine=rp,pairs=pairs,dataset=folder,output=out)
    assert result['packet_count']==2
    packets=list(map(json.loads,out.read_text().splitlines()))
    assert all(x['unobserved_execution']['vehicle_stop_windows_s'] is None for x in packets)
    assert all(x['research_labels']['real_handoff_verified'] is False for x in packets)
    assert packets[0]['pudo_hybrid_A']['fields']['legal_stop']['provenance_kind']=='simulated'
    assert packets[1]['action_history']['cumulative_movement_needed_for_B_m']==90
    assert packets[0]['physical_site_pair']==['site_A','site_B']


def test_bridge_missing_contract_unknown_not_pass(tmp_path):
    rp,_,_=fixture(tmp_path)
    output=tmp_path/'packets.jsonl'
    export_progress_packets(remine=rp,output=output)
    obj=json.loads(output.read_text().splitlines()[0])
    assert obj['action_history']['within_screening_budget'] is None
    assert obj['research_labels']['unknown_not_equivalent_to_infeasible']


def test_factorial_full_2x2x2_reference_and_flips(tmp_path):
    rp,pairs,folder=fixture(tmp_path)
    progress=tmp_path/'packets.jsonl'
    export_progress_packets(remine=rp,pairs=pairs,dataset=folder,output=progress)
    assumptions=tmp_path/'interventions.json'
    assumptions.write_text(json.dumps({
      'truth_claim_scope':'CONTROLLED_SYNTHETIC_INTERVENTION_NOT_REAL_MEASUREMENT',
      'speed_mps':1,'boarding_duration_s':20,'passenger_wait_remaining_s':45,
      'passenger_arrival_uncertainty_s':5,'vehicle_dwell_s':40,
      'budget_offsets_m':[-80,0],'evidence_expiry_offsets_s':[-100,200],
      'vehicle_start_offsets_s':[0,80]}))
    output=tmp_path/'factorial.jsonl'
    result=controlled_factorial(packets=progress,assumptions=assumptions,output=output)
    assert result['factorial_rows']==16
    assert result['reference_disagreement_count']==0
    assert result['paired_flip_counts']['paired_groups']==2
    assert result['paired_flip_counts']['has_some_safe_and_unsafe']==2
    assert all(result['paired_flip_counts'][f+'_sensitivity']>0 for f in ('capability','evidence','vehicle'))
    rows=[json.loads(s) for s in output.read_text().splitlines()]
    assert all(r['has_physical_vehicle_window_evidence'] is False for r in rows)
    assert {r['synthetic_window_source'] for r in rows}=={'CONTROLLED_SYNTHETIC_INTERVENTION_NOT_REAL_MEASUREMENT'}


def test_factorial_refuses_city_ground_truth_assumption(tmp_path):
    rp,pairs,_=fixture(tmp_path)
    packets=tmp_path/'packets.jsonl'
    export_progress_packets(remine=rp,pairs=pairs,output=packets)
    assumptions=tmp_path/'bad.json'
    assumptions.write_text(json.dumps({'truth_claim_scope':'REAL_BOSTON_MEASUREMENT'}))
    with pytest.raises(ValueError,match='Refuse'):
        controlled_factorial(packets=packets,assumptions=assumptions,output=tmp_path/'out.jsonl')


def test_hybrid_source_audit_keeps_cross_split_site_and_unknown(tmp_path):
    root=tmp_path/'datasets'
    for split,site in [('train','site_shared'),('val','site_shared'),('test','site_other')]:
        jsonl(root/f'abilitybench_av_hybrid_{split}'/'pudo_anchors.jsonl', [
          {'episode_id':f'{split}_1','anchor_id':'A','hybrid_physical_site_key':site,
           'field_provenance':{'sidewalk_width_m':{'kind':'simulated'}},
           'sidewalk_width_m':1.1,'stop_pose':None}])
    jsonl(root/'abilitybench_av_hybrid_train'/'episodes.jsonl',[{'episode_id':'train_1','map_name':'us-ma-boston'}])
    result=audit_dataset_sources(dataset_root=root,output=tmp_path/'audit.json')
    assert result['unique_physical_site_keys_in_scan']==2
    assert result['physical_site_keys_across_splits_in_scan']==1
    assert result['split_summary']['train']['city_joined_from_episodes_rows']==1
    assert result['split_summary']['train']['city_known_rows']['boston']==1
    assert result['core_field_provenance_counts']['sidewalk_width_m']['simulated']==3
    assert result['does_not_certify_real_field_truth']


def test_review_zip_uses_attached_manifest_integrity():
    path=Path('/mnt/data/capplan_handoff_review_small.zip')
    if not path.is_file():pytest.skip('uploaded external review unavailable in distribution tests')
    result=review(path)
    assert result['bundle_integrity']=='VERIFIED_UPLOAD_MANIFEST'
    assert result['manifest_errors']==[]
    assert result['H1']['fiveway_matched']==4
    assert result['H2']['directional_pairs']==25
