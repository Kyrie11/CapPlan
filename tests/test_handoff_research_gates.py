"""Gate scripts must refuse promotion of constructed scenarios to physical truth."""
from __future__ import annotations
import csv, hashlib, json
from pathlib import Path
from types import SimpleNamespace
import pytest
from capplan.handoff.gate_evidence import inspect_pairs,inspect_overlays,site_key,inspect_review
from scripts.select_gate1_site_review import select
from scripts.audit_gate2_motion import audit
from scripts.build_gate4_profile_time_grid import build
from scripts.run_handoff_research_gates import report
from scripts.audit_handoff_midroute import evaluate_pair
from capplan.data.schemas import AccessibilityEdge,AccessibilityNode,AccessibilityGraph
from tests.test_handoff_funnel_oracle import fixture


def write(path,rows):
    Path(path).write_text(''.join(json.dumps(x)+'\n' for x in rows),encoding='utf-8')


def pair(ep='e',sites=('s1','s2'),m='us-nv-las-vegas-strip'):
    return {'episode_id':ep,'map_name':m,'a_anchor':'A','b_anchor':'B','origin_anchor':'O',
       'physical_site_pair':list(sites),'scenario_token':'t',
       'distances_m':{'origin_to_a':130,'origin_to_b':170,'a_to_b':120},
       'distance_excess_m':50,'reroute_total_m':250,
       'screening_contract':{'distance_budget_m':200,'already_consumed_m':0,'min_width_m':.9,
           'max_slope':.08,'max_cross_slope':.04,'require_step_free':True,'min_confidence':0},
       'pedestrian_graph_nodes':{'origin':'O','A':'A','B':'B'},
       'paths':{'origin_to_a':{'node_ids':['O','A'],'edges':[{'length_m':130}]}}}


def test_balancing_and_missing_site_identity_not_representative(tmp_path):
    rows=[pair('e1'),pair('e2'),pair('e3',('s3','s4'),'us-ma-boston'),pair('e4',(None,'s9'))]
    src=tmp_path/'pairs.jsonl';write(src,rows)
    dest=tmp_path/'selection';m=select(src,dest,2)
    assert m['review_queue_size']==2
    assert m['unknown_identity_pairs_held']==1
    assert m['by_city']=={'boston':1,'pittsburgh':0,'singapore':0,'vegas':1}
    assert len(list(csv.DictReader((dest/'site_review_queue.csv').open())))==2
    (dest/'site_review_queue.csv').write_text('DO_NOT_OVERWRITE')
    select(src,dest,2)
    assert (dest/'site_review_queue.csv').read_text()=='DO_NOT_OVERWRITE'
    assert (dest/'site_review_queue_REGENERATED_UNREVIEWED.csv').exists()


def test_preflight_preserves_evidence_denominators(tmp_path):
    src=tmp_path/'pairs.jsonl';write(src,[pair('e1'),pair('e2')]);p=inspect_pairs(src)
    assert p['counts']['pairs']==2
    assert p['by_city']['vegas']['distinct_known_site_pairs']==1
    over=fixture();over['provenance']['source_pair']=pair()
    source=tmp_path/'over.jsonl';write(source,[over]);s=inspect_overlays(source)
    assert s['counts']['overlays']==1
    assert s['by_event_family']['original'].get('wait_budget_unspecified',0)==0
    assert s['by_event_family']['original']['profile_not_bound']==1


def test_unattested_motion_is_quarantined_even_if_all_booleans_true(tmp_path):
    request={'episode_id':'e','from_anchor':'A','to_anchor':'B','scenario_token':'t',
             'map_name':'us-ma-boston','traffic_event_s':90}
    req=tmp_path/'req.jsonl';write(req,[request])
    log=tmp_path/'real_log.json';log.write_text('{"test":true}')
    claim={**request,'reachable':True,'collision_free':True,'legal_stop':True,
      'interface_verified':True,'method_specific_motion_checked':True,
      'motion_duration_s':5,'valid_start_s':90,'valid_end_s':110,'source':'external checker',
      'planner_hash':'run','trajectory_id':'traj','simulator_log':str(log),
      'simulator_log_sha256':hashlib.sha256(log.read_bytes()).hexdigest(),
      'checker_version':'v1','time_reference':'scene_relative_seconds'}
    cl=tmp_path/'claims.jsonl';write(cl,[claim]);result=audit(req,cl,tmp_path/'audit')
    assert result['log_bound_attestation_count']==1
    assert result['independent_real_handoff_confirmations']==0
    claim['simulator_log_sha256']='0'*64;write(cl,[claim])
    bad=audit(req,cl,tmp_path/'auditbad')
    assert bad['log_bound_attestation_count']==0
    assert 'SIMULATOR_LOG_HASH_MISMATCH' in bad['results'][0]['reasons']
    none=audit(req,None,tmp_path/'empty');assert none['unanswered_requests']==1


def test_midroute_redirect_falsifies_all_the_way_to_A(tmp_path):
    # Direct edges O-A 130, O-B 170, A-B 120; at 30 m on O-A reverse to O and
    # take O-B: 30+30+170=230 >200; at 8 m total 8+8+170=186 fits.
    nodes=[AccessibilityNode(x,0,0,'sidewalk') for x in ('O','A','B')]
    edges=[AccessibilityEdge('oa','O','A',130,width_m=1.5,slope=.01,cross_slope=.01,step_free=True),
           AccessibilityEdge('ob','O','B',170,width_m=1.5,slope=.01,cross_slope=.01,step_free=True),
           AccessibilityEdge('ab','A','B',120,width_m=1.5,slope=.01,cross_slope=.01,step_free=True)]
    g=AccessibilityGraph('e',nodes,edges);p=pair()
    early=evaluate_pair(p,g,11,1,3)
    late=evaluate_pair(p,g,33,1,3)
    assert early['can_redirect_to_B_in_graph']
    assert not late['can_redirect_to_B_in_graph']
    assert late['cannot_recover_via_A_to_B']


def test_profile_grid_binds_budgets_and_marks_ignored_dimensions(tmp_path):
    o=fixture();o['provenance']['source_pair']=pair()
    os=tmp_path/'o.jsonl';write(os,[o])
    ps=tmp_path/'p.jsonl';write(ps,[{'profile_id':'p','evaluation_suite':'canonical_counterfactual',
       'population_representative':False,'mobility':{'max_access_distance_m':180}}])
    result=build(os,ps,tmp_path/'grid.jsonl',wait_budgets=(30.,None))
    assert result['overlays_generated']==2
    rows=list(json.loads(x) for x in (tmp_path/'grid.jsonl').read_text().splitlines())
    assert [x['capability_budget']['wait_budget_s'] for x in rows]==[30.,None]
    assert all(x['capability_budget']['profile_id']=='p' for x in rows)
    assert all(x['provenance']['profile_test']['same_event_worlds_as_base'] is True for x in rows)


def test_report_never_promotes_synthetic_to_novelty(tmp_path):
    r=report(tmp_path)
    assert r['gates']['G5_published_strength_belief_shield']['status']=='BLOCKED_NOT_IMPLEMENTED'
    assert r['claim_boundaries']['failure_non_negligible_population_frequency']=='NOT_ESTABLISHED'


def test_comparator_anchors_are_audit_tasks_not_physical_truth(tmp_path):
    from scripts.select_gate1_anchor_controls import build as controls
    src=tmp_path/'selection.jsonl';write(src,[pair()])
    write(tmp_path/'pudo_anchors.jsonl',[
       {'episode_id':'e','anchor_id':'A','kind':'pickup','legal_stop':True},
       {'episode_id':'e','anchor_id':'B','kind':'pickup','legal_stop':True},
       {'episode_id':'e','anchor_id':'C','kind':'pickup','legal_stop':True,'truth_mode':'simulated'},
       {'episode_id':'e','anchor_id':'D','kind':'pickup','legal_stop':False,'truth_mode':'derived'}])
    stats=controls(tmp_path,src,tmp_path/'comparators.csv')
    assert stats['audit_only_controls']==2
    assert stats['independently_verified_negative_controls']==0
    rr=list(csv.DictReader((tmp_path/'comparators.csv').open()))
    assert {r['anchor_id'] for r in rr}=={'C','D'}
    assert all(r['review_decision']=='PENDING' for r in rr)


def test_duplicate_checker_claim_cannot_promote_motion(tmp_path):
    request={'episode_id':'e','from_anchor':'A','to_anchor':'B','scenario_token':'t',
             'map_name':'us-ma-boston','traffic_event_s':90}
    req=tmp_path/'req.jsonl';write(req,[request]);log=tmp_path/'log.json';log.write_text('{}')
    claim={**request,'reachable':True,'collision_free':True,'legal_stop':True,
      'interface_verified':True,'method_specific_motion_checked':True,
      'motion_duration_s':5,'valid_start_s':90,'valid_end_s':110,'source':'checker',
      'planner_hash':'run','trajectory_id':'traj','simulator_log':str(log),
      'simulator_log_sha256':hashlib.sha256(log.read_bytes()).hexdigest(),
      'checker_version':'v1','time_reference':'scene_relative_seconds'}
    cl=tmp_path/'cl.jsonl';write(cl,[claim,claim])
    audited=audit(req,cl,tmp_path/'audit')
    assert audited['log_bound_attestation_count']==0
    assert audited['counts']['quarantined']==2
    assert audited['results'][0]['reasons']==['DUPLICATE_CLAIM_KEY']
