"""Tests: synthetic information gap and evidence non-overclaiming."""
from __future__ import annotations
import json
from pathlib import Path
from copy import deepcopy

from scripts.run_handoff_decidability_v11 import analyze_case
from scripts.audit_handoff_gap_v11 import audit_graph, review_status, REQUIRED_CHECKS

ROOT=Path(__file__).resolve().parents[1]


def test_clairvoyance_is_not_causal_policy():
    case=json.loads((ROOT/'examples/handoff_nonanticipativity_v11.jsonl').read_text().strip())
    r=analyze_case(case, max_states=20000)
    assert r['generic_causal_exact']['status']=='LOSE'
    assert r['generic_causal_exact']['complete']
    assert {v['status'] for v in r['clairvoyant_worlds_upper_bound'].values()}=={'WIN'}
    assert r['causal_information_gap_certified']=='YES'
    assert r['contingent_vs_any_fixed_anchor_certified']=='NO'


def test_observation_contingent_beats_all_fixed_sites():
    examples=(ROOT/'examples/handoff_finite_v10.jsonl').read_text().splitlines()
    case=next(x for line in examples if (x:=json.loads(line))['case_id']=='observe_mutually_exclusive')
    r=analyze_case(case, max_states=20000)
    assert r['generic_causal_exact']['status']=='WIN'
    assert r['contingent_vs_any_fixed_anchor_certified']=='YES'
    assert {v['status'] for v in r['fixed_anchor_full_policy'].values()}=={'LOSE'}


def test_cap_is_unknown_not_false_claim():
    case=json.loads((ROOT/'examples/handoff_nonanticipativity_v11.jsonl').read_text().strip())
    r=analyze_case(case, max_states=1)
    assert r['causal_information_gap_certified']=='UNKNOWN'


def test_missing_physical_reviews_remain_unknown():
    row={'episode_id':'ep','physical_site_pair':['alpha','beta'],
         'notice_issued_at_s':0,'strict_explicit_curb_count':0,
         'optimistic_without_curb_count':3,
         'optimistic_unverified_examples':[{'anchor_id':'C'}]}
    q={'episode_id':'ep','physical_site_pair':str(['alpha','beta']),
       'candidate_review_queue':[{'anchor_id':'C','physical_site_key':'siteC'}]}
    result,s=audit_graph([row],[q])
    assert s['counts']['site_pairs_optimistic_only']==1
    assert result[0]['reviewed_candidates'][0]['status']=='UNKNOWN'
    assert not result[0]['global_no_recovery_proved']


def test_complete_external_assertions_are_marked_conditional_not_real_proof():
    full={'checks':{k:{'result':'PASS','source':'independent_review_1',
                         'checked_at_utc':'2026-10-20T00:00:00Z'} for k in REQUIRED_CHECKS}}
    assert review_status(full)=='AUDITED_OPTION_PASS_CONDITIONAL'
    one_missing=deepcopy(full)
    del one_missing['checks']['continuous_vehicle_motion']['source']
    assert review_status(one_missing)=='UNKNOWN'
    one_bad=deepcopy(full)
    one_bad['checks']['legal_stop_and_pose']['result']='FAIL'
    assert review_status(one_bad)=='AUDITED_OPTION_FAIL'


def test_full_candidate_enumerator_never_truncates(monkeypatch):
    # No server GIS graph is available here; test the exact/full invocation
    # contract rather than pretending that our synthetic object is a real map.
    import scripts.audit_all_existing_pudos_v11 as module
    from scripts.audit_handoff_all_alternatives_v8 import evaluate
    import inspect
    assert inspect.signature(evaluate).parameters['max_returned_alternates'].default==30
    assert 'max_returned_alternates=None' in inspect.getsource(module.run)
