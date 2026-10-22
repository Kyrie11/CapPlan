"""Auditable finite-model comparators for handoff commitment research.

This is *not* an implementation/reproduction of the AAAI'23 or ICRA'24
shielding algorithms. It shares only the DECLARED model inputs with the
interval and independent grid reference methods. No private city-state facts
are inferred from absent GIS, simulator or user data.

Distinguish:
  * independent marginal reachability: does NOT certify a handoff;
  * robust fixed site: one site and service-start works in every world;
  * causal one-observation strategy: one action per observation-equivalence class;
  * world-clairvoyant upper bound: intentionally NOT an implementable policy.
"""
from __future__ import annotations
from collections import Counter
import json
from pathlib import Path
from typing import Any

from capplan.handoff.temporal_recourse_v12 import analyze, one_world_windows, intersect
from capplan.handoff.semantic_oracle_v13 import evaluate, _site_start, _admissible, _bound


def _has_nonempty_windows(value: Any) -> bool:
    return (isinstance(value, list) and
            any(isinstance(span, (list, tuple)) and len(span) == 2 and
                all(isinstance(v, (int, float)) for v in span) and
                float(span[1]) >= float(span[0]) for span in value))


def marginal_site(site: dict, worlds: list[str]) -> dict:
    """Measure the *incorrect* endpoint-only shortcut. No semantic safety claim.

    Unknown is not a feasible or infeasible observation. The predicate
    deliberately ignores whether arrival, vehicle dwell and interface occur
    in an intersecting time interval. It is an ablation/baseline ONLY.
    """
    required = ('passenger_arrival_window_s','vehicle_stop_windows_s',
                'interface_windows_s','evidence_validity_windows_s',
                'boarding_duration_s','world_handoff_open_windows_s','contract_feasible')
    absent = [name for name in required if site.get(name) is None]
    if absent:
        return {'option_id': site.get('option_id'), 'status': 'UNKNOWN_MARGINAL_INPUT',
                'missing': absent}
    wmap = site['world_handoff_open_windows_s']
    if not isinstance(wmap,dict) or any(w not in wmap or wmap[w] is None for w in worlds):
        return {'option_id':site.get('option_id'),'status':'UNKNOWN_MARGINAL_WORLD'}
    if site['contract_feasible'] is not True:
        return {'option_id':site.get('option_id'),'status':'MARGINAL_CONTRACT_REJECT'}
    marginal = (site['passenger_arrival_window_s'][1] >= site['passenger_arrival_window_s'][0] and
                all(_has_nonempty_windows(site[f]) for f in (
                    'vehicle_stop_windows_s','interface_windows_s','evidence_validity_windows_s')) and
                all(_has_nonempty_windows(wmap[w]) for w in worlds))
    return {'option_id':site.get('option_id'),
            'status':'MARGINAL_ENDPOINT_ACCEPT' if marginal else 'MARGINAL_ENDPOINT_REJECT',
            'joint_time_intersection_checked': False}


def safe_fixed_actions(case: dict) -> dict:
    """Full-support robust candidate safe-action set with shared start times."""
    worlds = case['world_support']
    actions, unknown = [], []
    for site in case['options']:
        merged = None
        for world in worlds:
            win = one_world_windows(site, world)
            if win is None:
                merged = None
                unknown.append(site['option_id'])
                break
            merged = win if merged is None else intersect(merged, win)
            if not merged:
                break
        if merged:
            actions.append({'option_id':site['option_id'],
                            'start_windows_s':[[a,b] for a,b in merged]})
    return {'safe_actions': actions, 'unknown_actions': sorted(set(unknown)),
            'semantics':'COMMON_START_AND_SITE_ACROSS_ALL_DECLARED_WORLDS',
            'world_support':worlds}


def independent_grid_safe_actions(case: dict, *, max_t: int = 12000) -> dict:
    """Enumerate integer-start safe fixed actions; independent of interval join.

    Used only on integer-window cases. Every hidden world must satisfy the
    SAME site and start. Does not enumerate an entire POMDP policy tree.
    """
    outcomes=[]; unknown=[]
    for site in case['options']:
        values=[_site_start(site,w,False) for w in case['world_support']]
        if any(v is None for v in values):
            unknown.append(site['option_id'])
            continue
        bounds=[_bound(v) for v in values]
        end=min(bounds) if bounds else 0
        if end > max_t:
            raise ValueError('grid safety checking exceeds declared horizon')
        candidates=[]
        for t in range(max(0,end)+1):
            if all(_admissible(t,v) for v in values):candidates.append(t)
        if candidates:
            outcomes.append({'option_id':site['option_id'], 'earliest_start_s':candidates[0],
                             'valid_integer_starts':len(candidates)})
    return {'safe_actions':outcomes,'unknown_actions':sorted(set(unknown)),
            'scope':'INDEPENDENT_INTEGER_GRID_FINITE_MODEL'}


def compare_case(case: dict, *, check_independent_grid=True) -> dict:
    interval = analyze(case)
    grid = evaluate(case) if check_independent_grid else None
    world_support=case['world_support']
    marginals = [marginal_site(o, world_support) for o in case['options']]
    marginal_accepts = [x['option_id'] for x in marginals if x['status']=='MARGINAL_ENDPOINT_ACCEPT']
    fixed = safe_fixed_actions(case)
    independent = independent_grid_safe_actions(case) if check_independent_grid else None
    fixed_set = {x['option_id'] for x in fixed['safe_actions']}
    false_positive_candidates = [x for x in marginal_accepts if x not in fixed_set]
    action_set_agree = (fixed_set == {x['option_id'] for x in independent['safe_actions']}
                        and set(fixed['unknown_actions']) == set(independent['unknown_actions'])) if independent else None
    return {
        'case_id':case.get('case_id'),
        'input_evidence_level':case.get('evidence','UNSPECIFIED'),
        'marginal_endpoint_baseline':marginals,
        'marginal_accepting_actions':marginal_accepts,
        'joint_safe_fixed_actions': fixed,
        'independent_grid_safe_fixed_actions': independent,
        'safe_fixed_action_sets_agree': action_set_agree,
        'marginal_false_fixed_commitment_candidates':false_positive_candidates,
        'nominal_positive_without_robust_fixed':bool(marginal_accepts and not fixed_set),
        'exact_restricted_interval_status':interval['status'],
        'exact_restricted_grid_status':grid['status'] if grid else None,
        'grid_interval_agree':interval['status']==grid['status'] if grid else None,
        'one_observation_recourse_witness':interval.get('witness') if interval['status']=='ONE_OBSERVATION_CAUSAL_POLICY_IN_DECLARED_MODEL' else None,
        'independent_clairvoyant_possible':grid.get('clairvoyant_possible') if grid else None,
        'nonanticipativity_gap':grid.get('nonanticipativity_gap') if grid else None,
        'unknown_inputs_present':bool(fixed['unknown_actions'] or interval['missing_input_options']),
        'scope':'CONTROLLED_FINITE_ONE_OBSERVATION_POLICY_CLASS',
        'not_a_published_shield_implementation':True,
        'not_a_real_world_handoff_claim':True,
    }


def analyze_files(cases: Path, output: Path) -> dict:
    output = Path(output)
    output.parent.mkdir(parents=True,exist_ok=True)
    counts=Counter(); rows=0; mismatch_ids=[]
    with Path(cases).open(encoding='utf-8') as src, output.open('w',encoding='utf-8') as dest:
        for line in src:
            if not line.strip():continue
            case=json.loads(line)
            result=compare_case(case)
            dest.write(json.dumps(result,ensure_ascii=False)+'\n')
            rows+=1
            counts['marginal_accept_no_robust_fixed'] += int(result['nominal_positive_without_robust_fixed'])
            counts['nonanticipativity_gap'] += int(bool(result['nonanticipativity_gap']))
            counts['restricted_policy_exists'] += int(result['exact_restricted_interval_status'] in (
                'ROBUST_FIXED_IN_DECLARED_MODEL','ONE_OBSERVATION_CAUSAL_POLICY_IN_DECLARED_MODEL'))
            counts['unknown_input'] += int(result['unknown_inputs_present'])
            if not result['grid_interval_agree'] or not result['safe_fixed_action_sets_agree']:
                mismatch_ids.append(result['case_id'])
    summary={'cases':rows,'counts':dict(counts),'mismatches':mismatch_ids,
             'mismatch_count':len(mismatch_ids),
             'scientific_scope':'DECLARED_MODELS_NOT_MEASURED_EVENTS',
             'not_published_baselines':True,
             'not_natural_incidence_estimate':True}
    output.with_suffix('.summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return summary
