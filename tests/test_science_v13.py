"""Regression/falsification tests for V13; no physical claims."""
from __future__ import annotations
import json
from pathlib import Path
from capplan.handoff.structural_gap_v13 import (
    PASS,FAIL,UNKNOWN,conj,local_predicates,episode_signature,provenance_kind)
from scripts.structural_gap_toy_v13 import run as toy
from scripts.reanalyse_heuristics_v13 import report
from scripts.audit_natural_structural_v13 import run as natural
from scripts.probe_capplan_tree_v13 import inspect


def anchor(**override):
    a={'episode_id':'ep1','anchor_id':'a','kind':'pickup','legal_stop':True,
       'blockage_risk':.1,'sidewalk_width_m':1.5,'curb_ramp':True,
       'curb_height_m':.02,'deployment_clearance_m':1.8,'stop_pose':{'x':1,'y':2}}
    a.update(override);return a


def test_three_valued_semantics():
    assert conj(PASS,UNKNOWN)==UNKNOWN
    assert conj(UNKNOWN,FAIL)==FAIL
    assert conj(PASS,PASS)==PASS
    assert local_predicates(anchor())['declared_local_joint']==PASS
    assert local_predicates(anchor(legal_stop=False))['declared_local_joint']==FAIL
    assert local_predicates(anchor(curb_height_m=None))['declared_local_joint']==UNKNOWN
    assert local_predicates(anchor())['certified_full_handoff']==UNKNOWN


def test_provenance_nested_kind_priority():
    a=anchor(field_provenance={'curb_height_m':{'kind':'hybrid_simulated','source':'municipal_geom'}})
    assert provenance_kind(a,'curb_height_m')=='simulated'
    assert provenance_kind(anchor(),'curb_height_m')=='unlabeled'
    assert provenance_kind(anchor(field_provenance={'legal_stop':{'kind':'observed','source':'city GIS'}}),'legal_stop')=='observed_claim_unverified'


def test_independent_existential_not_joint():
    a=anchor(curb_height_m=.1) # passenger FAIL, vehicle PASS, interface PASS
    b=anchor(legal_stop=False) # passenger PASS, vehicle FAIL, interface PASS
    sig=episode_signature([a,b])
    assert sig['independent_existential_gap'] is True
    assert sig['declared_local_joint_exists'] is False
    assert sig['full_physical_service_certified'] is False
    assert episode_signature([anchor(),a])['declared_local_joint_exists'] is True


def test_toy_complete_enumeration():
    d=toy();assert d['universe_size']==64
    assert d['false_join_patterns']>0
    assert d['nearest_A_trap_with_available_B']>0


def test_natural_sample_does_not_lose_unknown_or_censored(tmp_path):
    ds=tmp_path/'abilitybench_av_hybrid_test';ds.mkdir()
    (ds/'pudo_anchors.jsonl').write_text(''.join(json.dumps(x)+'\n' for x in [
        anchor(),anchor(anchor_id='b',episode_id='ep2',curb_height_m=None)]))
    sample=tmp_path/'cohort.jsonl'
    sample.write_text(''.join(json.dumps({'episode_id':e,'city':'boston','sampling_design':'blind'})+'\n'
                   for e in ('ep1','ep2','ep3')))
    result=natural(ds,sample,tmp_path/'out.jsonl')
    assert result['counts']['sampled_episodes']==3
    assert result['counts']['declared_local_joint_exists']==1
    assert result['counts']['pickup_anchor_records']==2
    rows=[json.loads(x) for x in (tmp_path/'out.jsonl').read_text().splitlines()]
    assert rows[-1]['anchors']==0
    assert rows[1]['predicates']['pedestrian']=={UNKNOWN:1}


def test_probe_readonly(tmp_path):
    (tmp_path/'outputs'/'prepared'/'train').mkdir(parents=True)
    (tmp_path/'outputs'/'datasets').mkdir(parents=True)
    (tmp_path/'outputs'/'prepared'/'train'/'sample.jsonl').write_text('{"episode_id":"x","scene": {"a":1}}\n')
    info=inspect(tmp_path,1000,6)
    assert info['nodes_seen']>=2
    assert not info['cutoff']
    assert any('episode_id' in x.get('schema_keys',[]) for x in info['json_schema_previews'])


def test_grouped_analysis():
    x={'case_id':'x::base','family':'f','oracle_status':'WIN','oracle_exact':True,
       'baseline':{'nearest_static':{'certified_safe_first_action':False,'planning_ms':.1,'evaluated_model_successors':0}}}
    y=dict(x,case_id='x::stress');z=dict(x,case_id='y::base',baseline={
       'nearest_static':{'certified_safe_first_action':True,'planning_ms':.2,'evaluated_model_successors':0}})
    summary=report([x,y,z],40)
    assert summary['independent_parent_cases']==2
    assert summary['baselines']['nearest_static']['unsafe_actions']==2


def test_nuplan_scenario_tag_selector(tmp_path):
    from scripts.audit_pudo_event_tags_v13 import analyze
    ep=tmp_path/'episodes.jsonl'
    ep.write_text(''.join(json.dumps(x)+'\n' for x in [
        {'episode_id':'a','metadata':{'scenario_type':'on_pickup_dropoff'},'map_name':'city'},
        {'episode_id':'b','metadata':{'scenario_type':'stationary'},'map_name':'city'},
        {'episode_id':'c','map_name':'city'}]))
    r=analyze(ep)
    assert r['counts']=={'cohort':3,'tagged':2,'pudo_exposure_tagged':1,'missing_tags':1}
    assert r['pudo_tagged_episodes'][0]['episode_id']=='a'
