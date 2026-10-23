from __future__ import annotations
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from capplan.handoff.causal_policy_v2 import compare,compare_jsonl,State,HandoffModel,ExactShield
from capplan.handoff.factorial_science_v2 import analyze_factorial
from capplan.handoff.site_review_queue_v2 import make_review_queue

CASE = Path(__file__).resolve().parents[1]/'examples/handoff_science_v2/causal_cases.jsonl'


def _cases():
    return [json.loads(x) for x in CASE.read_text().splitlines() if x.strip()]


def test_initial_safe_actions_and_observation_causality():
    a,b = _cases()
    ra=compare(a);rb=compare(b)
    assert ra['safe_actions_agree'] is True
    assert rb['safe_actions_agree'] is True
    assert 'guide:A' in ra['grid']['safe_initial_actions']
    # World-conditional choices are forbidden until a sensor observation.
    assert 'guide:A' not in rb['grid']['safe_initial_actions']
    assert 'guide:B' not in rb['grid']['safe_initial_actions']
    assert 'verify:A' in rb['grid']['safe_initial_actions']


def test_missing_evidence_is_unknown_not_negative():
    a,_ = _cases()
    del a['worlds'][0]['handoff_windows']['A']['stop']
    r=compare(a)
    assert r['status']=='UNKNOWN_INPUT'
    assert r['safe_actions_agree'] is None


def test_loss_of_directed_recovery_changes_safe_action():
    a,_=_cases()
    a['directed_edges_m']['A'].pop('B')
    assert 'guide:A' not in compare(a)['grid']['safe_initial_actions']
    assert 'guide:B' in compare(a)['grid']['safe_initial_actions']


def test_monotonic_budget_and_reference_output():
    a,_=_cases()
    higher=set(compare(a)['grid']['safe_initial_actions'])
    a['capability']['max_distance_m']=6
    lower=set(compare(a)['grid']['safe_initial_actions'])
    assert 'guide:A' in higher and 'guide:A' not in lower
    assert 'guide:B' in lower
    with TemporaryDirectory() as temp:
        out=Path(temp)/'out.jsonl'
        summary=compare_jsonl(CASE,out,max_states=20000)
        assert summary['cases']==2
        assert summary['counts']['disagreements']==0
        assert (Path(temp)/'out.summary.json').is_file()


def test_no_sensor_window_cannot_expose_future_world():
    _,b = _cases()
    for w in b['worlds']:
        for site in ('A','B'):
            w['handoff_windows'][site]['evidence']=[]
    r=compare(b)
    assert r['safe_actions_agree'] is True
    assert r['grid']['safe_initial_actions']==[]


def test_factorial_interaction_reports_nonzero_and_monotonicity():
    with TemporaryDirectory() as t:
        p=Path(t)/'f.jsonl'
        with p.open('w') as f:
            for c in (0,1):
                for e in (0,1):
                    for v in (0,1):
                        f.write(json.dumps({'episode_id':'x','a_anchor':'A','b_anchor':'B',
                        'notice_issued_s':0,'factors':{'capability_budget_level':c,
                        'evidence_expiry_level':e,'vehicle_arrival_level':v},
                        'robust_fixed_B_safe':bool(c and e and v),
                        'synthetic_window_source':'CONTROLLED_SYNTHETIC'})+'\n')
        result=analyze_factorial(p,Path(t)/'out.json')
        assert result['summary']['nonzero_three_factor_boolean_contrast']==1
        assert result['summary']['strict_all_three_relaxations_required_pattern']==1
        assert result['summary'].get('capability_relaxation_monotonicity_violation',0)==0


def test_site_queue_never_claims_attestation():
    with TemporaryDirectory() as t:
        p=Path(t)/'geo.jsonl';out=Path(t)/'review.jsonl'
        row={'city':'boston','episode_id':'z','a_anchor':'A','b_anchor':'B',
             'physical_site_pair':['s1','s2'],
             'graph_witness':{'complete_B_suffix_available':True,
                              'arc_orientation_semantics_independently_verified':False},
             'pudo_hybrid_A':{'real_physical_validated':False},
             'pudo_hybrid_B':{'real_physical_validated':False},
             'action_history':{'notice_issued_s':5}}
        p.write_text(json.dumps(row)+'\n')
        summary=make_review_queue(p,out)
        assert summary['attested_real_handoffs_created']==0
        obj=json.loads(out.read_text().splitlines()[0])
        assert obj['physical_handoff_verified'] is False
        assert all(x['status']=='UNKNOWN' for x in obj['fields_to_independently_audit'].values())


def test_boarding_requires_whole_vehicle_and_evidence_window():
    a,_=_cases()
    model=HandoffModel(a)
    s=State('A',5,3.0,1,'A',1,('clear_A',))
    assert model.board(s,interval=False,count=__import__('collections').Counter())
    assert model.board(s,interval=True,count=__import__('collections').Counter())
    # Starts in the evidence interval, but boarding end exceeds expiry.
    later=State('A',9,3.0,1,'A',1,('clear_A',))
    assert not model.board(later,interval=False,count=__import__('collections').Counter())
    assert not model.board(later,interval=True,count=__import__('collections').Counter())


def test_state_cap_unknown_not_infeasible():
    a,_=_cases()
    result=compare(a,max_states=1)
    assert result['status']=='UNKNOWN_COMPUTATION_LIMIT'
    assert result['safe_actions_agree'] is None
