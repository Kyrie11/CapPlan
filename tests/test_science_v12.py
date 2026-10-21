"""V12 deliberately adversarial research-falsification tests."""
from copy import deepcopy
from pathlib import Path
import itertools,json,random,tempfile
from capplan.handoff.temporal_recourse_v12 import analyze,intervals,intersect,shift_ends_for_full_duration,one_world_windows
from capplan.handoff.carla_calibration_v12 import audit

ROOT=Path(__file__).resolve().parents[1]
CASES=[json.loads(s) for s in (ROOT/'examples/handoff_temporal_v12.jsonl').read_text().splitlines()]

def test_restricted_policy_class_branches_never_clairvoyant():
    got={c['case_id']:analyze(c) for c in CASES}
    assert got['early_obs_two_sites']['status']=='ONE_OBSERVATION_CAUSAL_POLICY_IN_DECLARED_MODEL'
    assert len(got['early_obs_two_sites']['observation_branches'])==2
    assert got['late_obs_arrival_window_expired']['status']=='NO_POLICY_IN_RESTRICTED_CLASS'
    assert got['robust_fixed_third_site']['status']=='ROBUST_FIXED_IN_DECLARED_MODEL'
    assert got['none_observable_before_commitment']['status']=='NO_POLICY_IN_RESTRICTED_CLASS'
    assert got['missing_interface_unknown']['status']=='UNKNOWN_MISSING_INPUT'
    assert not got['robust_fixed_third_site']['evidence']['physical_evidence_verified']


def test_indistinguishable_worlds_cannot_take_different_branches():
    ex=deepcopy(CASES[0]);ex['one_observation']['outcomes_by_world']={'wA':'same','wB':'same'}
    assert analyze(ex)['status']=='NO_POLICY_IN_RESTRICTED_CLASS'


def test_exact_intervals_match_grid_enumeration_for_integer_points():
    rng=random.Random(8)
    for k in range(200):
        a=intervals([[rng.randint(0,18),19] for i in range(rng.randint(0,6))],field='a')
        b=intervals([[rng.randint(0,18),19] for i in range(rng.randint(0,6))],field='b')
        duration=rng.randint(1,5)
        got=intersect(a,shift_ends_for_full_duration(b,duration))
        direct=[]
        for t in range(20):
            if any(lo<=t<=hi for lo,hi in a) and any(lo<=t and t+duration<=hi for lo,hi in b):
                direct.append(t)
        from_intervals=[t for t in range(20) if any(lo-1e-8<=t<=hi+1e-8 for lo,hi in got)]
        assert from_intervals==direct


def test_missing_later_world_is_unknown_not_lose():
    ex=deepcopy(CASES[0]);ex['options'][0]['world_handoff_open_windows_s']['wA']=[]
    ex['options'][0]['world_handoff_open_windows_s']['wB']=None
    assert ex['options'][0]['world_handoff_open_windows_s']['wB'] is None
    assert one_world_windows(ex['options'][0],'wB') is None
    assert analyze(ex)['status']=='UNKNOWN_MISSING_INPUT'


def test_nonfinite_physical_window_rejected():
    ex=deepcopy(CASES[0]);ex['options'][0]['vehicle_stop_windows_s']=[[0,float('inf')]]
    try:analyze(ex)
    except ValueError:pass
    else:raise AssertionError('infinite interval cannot be attested')


def test_carla_clear_v11_is_partial_calibration_if_fixture_provided():
    import os
    fixture=os.environ.get('V12_REAL_V11_CARLA_CLEAR')
    if fixture:
        result=audit(fixture)
        assert result['clear_trials']==12
        assert result['clear_stop_surrogate_success']==3
        assert result['gate']=='PARTIALLY_CALIBRATED'
        assert result['independent_real_handoff_claims']==0
        assert result['blocked_comparison_gate']=='NOT_TESTED'
    else:
        with tempfile.TemporaryDirectory() as tmp:
            assert audit(tmp)['gate']=='UNKNOWN_NO_CLEAR_CONTROL'


def test_interval_vs_grid_independent_randomized():
    from scripts.benchmark_temporal_join_v12 import make_case,slow_integer_grid
    for h in (30,80,190):
        for seed in range(42):
            case=make_case(seed,h,5,3)
            fast=analyze(case)['status']=='ROBUST_FIXED_IN_DECLARED_MODEL'
            assert fast==slow_integer_grid(case,h)


def test_uncertain_passenger_arrival_has_different_quantifier():
    ex=deepcopy(CASES[2]);site=ex['options'][2]
    site['passenger_arrival_window_s']=[1,8]
    site['passenger_wait_remaining_s']=1
    site['passenger_arrival_quantifier']='all'
    assert one_world_windows(site,'wA')==()
    site['passenger_arrival_quantifier']='exists'
    assert one_world_windows(site,'wA')


def test_carla_pair_requires_calibrated_clear_and_identical_config():
    import hashlib
    from capplan.handoff.carla_calibration_v12 import audit
    def emit(parent,mode,stop=True,config=True):
        d=Path(parent)/'spawn0_asteps2';d.mkdir(parents=True,exist_ok=True)
        times=[i*.05 for i in range(48)]
        rows=[{'elapsed_s':t,'distance_to_goal_m':3 if stop else 9,
               'speed_mps':0.0,'target':mode} for t in times]
        b=''.join(json.dumps(x)+'\n' for x in rows).encode()
        (d/'trajectory.jsonl').write_bytes(b)
        report={'mode':mode,'carla_map':'Town03','status':'COMPLETED','collisions':0,'seed':13,
                'geometry_parameters':{'spawn_index':0,'a_steps':2,'b_steps':8,'arrival_radius_m':4},
                'service_stop_hold_s_required':2.0,'stop_speed_threshold_mps':0.5,
                'sample_dt_s':.05,'trajectory_file_sha256':hashlib.sha256(b).hexdigest()}
        if config:
            report['experiment_config']={'mode':mode,'map':'Town03','seed':13,
                                         'target_speed_kmh':20,'blocker_blueprint':'vehicle.tesla.model3',
                                         'redirect_at_s':8}
        (d/'execution_report.json').write_text(json.dumps(report))
    with tempfile.TemporaryDirectory() as temp:
        root=Path(temp);emit(root/'clear','clear_A',True)
        emit(root/'blocked','blocked_A',True)
        r=audit(root/'clear',root/'blocked')
        assert r['blocked_comparison_gate']=='ELIGIBLE_CARLA_SURROGATE_ONLY'
        emit(root/'blocked','blocked_A',True,config=False)
        r=audit(root/'clear',root/'blocked')
        assert r['comparisons'][0]['decision']=='UNKNOWN_MATCHED_CONFIGURATION_NOT_RECORDED'
        assert r['blocked_comparison_gate']=='NOT_VERIFIED'
        emit(root/'clear','clear_A',False)
        r=audit(root/'clear',root/'blocked')
        assert r['comparisons'][0]['decision']=='INVALID_FAILED_CLEAR_CONTROL'


def test_response_cannot_precede_observation():
    ex=deepcopy(CASES[0]);ex['one_observation']['available_at_s']=8
    # These after-observation travel intervals [6,7] could only have arisen
    # through an action issued BEFORE outcome revelation; forbid their use.
    assert analyze(ex)['status']=='NO_POLICY_IN_RESTRICTED_CLASS'
