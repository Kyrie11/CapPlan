#!/usr/bin/env python3
"""V11 falsification: causal policy vs clairvoyant single-world and fixed-anchor policies.

This script uses the UNMODIFIED V10 generic finite-support AND/OR model. All
comparisons share the same physical state/action transition model. The single-
world oracle deliberately gets the hidden world as advance information; it is
an upper bound, NOT an implementable online baseline. Fixed-site strategies
can observe and wait, but cannot move/guide/board at other sites; they are a
STRONGER control than a one-shot nearest heuristic.

Only COMPLETE exact outcomes support a YES/NO conclusion. Resource-cap hits,
missing physical evidence and incompatible restricted subproblems stay UNKNOWN.
Outputs are conditional on declared synthetic cases, never natural incidence.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
from time import perf_counter
from typing import Any
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capplan.handoff.commitment_model_v10 import FiniteCommitmentModel, GenericSupportSolver


def _run(case: dict[str, Any], max_states: int) -> dict[str, Any]:
    started = perf_counter()
    try:
        model = FiniteCommitmentModel(case)
        r = GenericSupportSolver(model, max_states=max_states).solve()
    except (ValueError, KeyError, TypeError) as exc:
        return {'status': 'INVALID_MODEL', 'error': str(exc), 'complete': False}
    return {
        'status': r.status,
        'complete': bool(r.exact_action_set_complete),
        'safe_first_actions': list(r.first_safe_actions) if r.exact_action_set_complete else None,
        'best_first_action': r.best_action,
        'states': r.explored_states,
        'tested_actions': r.evaluated_actions,
        'motion_requests': r.motion_queries,
        'unique_motion_requests': r.motion_unique_queries,
        'elapsed_s': round(perf_counter() - started, 6),
        'unknown_due_to_cap': r.unknown_due_to_cap,
        'unknown_physical_evidence_sites': list(model.unknown_interface_sites),
    }


def _certified(r: dict[str, Any]) -> bool:
    return bool(r.get('complete') and r.get('status') in ('WIN', 'LOSE'))


def analyze_case(case: dict[str, Any], max_states: int = 20000,
                 use_worlds: bool = True, use_fixed: bool = True) -> dict[str, Any]:
    full = _run(case, max_states)
    singles = {}
    fixed = {}
    if use_worlds:
        for world in case['worlds']:
            singleton = deepcopy(case)
            single_world = deepcopy(world)
            single_world['probability'] = 1.0
            singleton['worlds'] = [single_world]
            singles[world['id']] = _run(singleton, max_states)
    if use_fixed:
        for anchor in sorted(case['sites']):
            limited = deepcopy(case)
            limited['sites'] = {anchor: deepcopy(case['sites'][anchor])}
            # Evidence at non-selected stops has no effect on a policy that
            # never interacts with them. Keep all other state/world semantics.
            limited['initial_evidence'] = {z: data for z, data in
                                           case.get('initial_evidence', {}).items() if z == anchor}
            # Historical guidance to a different site may continue to execute;
            # refusing to rewrite it prevents magical retroactive commitment.
            if case['passenger'].get('active_instruction') not in (None, anchor):
                fixed[anchor] = {'status': 'LOSE', 'complete': True,
                                 'reason': 'FIXED_ANCHOR_INCOMPATIBLE_WITH_EXECUTED_GUIDANCE'}
                continue
            fixed[anchor] = _run(limited, max_states)

    all_worlds_complete = bool(singles) and all(_certified(x) for x in singles.values())
    all_fixed_complete = bool(fixed) and all(_certified(x) for x in fixed.values())
    # Known single-world WIN does not imply a causal strategy can select a world.
    causal_information_gap = (
        'YES' if _certified(full) and full['status'] == 'LOSE' and
                 all_worlds_complete and all(x['status'] == 'WIN' for x in singles.values())
        else 'NO' if _certified(full) and all_worlds_complete
        else 'UNKNOWN') if use_worlds else 'NOT_EVALUATED'
    contingent_anchor_advantage = (
        'YES' if _certified(full) and full['status'] == 'WIN' and
                 all_fixed_complete and all(x['status'] == 'LOSE' for x in fixed.values())
        else 'NO' if _certified(full) and all_fixed_complete
        else 'UNKNOWN') if use_fixed else 'NOT_EVALUATED'

    # UNKNOWN evidence is not evidence of physical infeasibility, even if a
    # finite closed-world result exists under a deliberately simulated truth.
    return {
        'case_id': case.get('case_id'), 'family': case.get('family'),
        'scope': 'DECLARED_FINITE_SYNTHETIC_MODEL_NOT_CITY_MEASUREMENT',
        'generic_causal_exact': full,
        'clairvoyant_worlds_upper_bound': singles,
        'fixed_anchor_full_policy': fixed,
        'causal_information_gap_certified': causal_information_gap,
        'contingent_vs_any_fixed_anchor_certified': contingent_anchor_advantage,
        'no_published_shield_or_carla_baseline_implemented': True,
    }


def run(cases: list[dict[str, Any]], max_states: int = 20000,
        use_worlds: bool = True, use_fixed: bool = True):
    rows = [analyze_case(case, max_states, use_worlds, use_fixed) for case in cases]
    count = Counter(r['generic_causal_exact']['status'] for r in rows)
    return rows, {
        'number_cases': len(rows), 'exact_status_counts': dict(count),
        'causal_information_gap': dict(Counter(r['causal_information_gap_certified'] for r in rows)),
        'contingent_anchor_advantage': dict(Counter(r['contingent_vs_any_fixed_anchor_certified'] for r in rows)),
        'causal_exact_complete_count': sum(_certified(r['generic_causal_exact']) for r in rows),
        'max_states_per_subproblem': max_states,
        'scope': 'FINITE_CONSTRUCTED_MECHANISMS_NO_REAL_WORLD_INCIDENCE',
        'interpretation': 'Single-world clairvoyance is an unattainable upper bound; fixed-site is a full restricted policy; no published shielding implementation is compared.',
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cases', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--max_states', type=int, default=20000)
    p.add_argument('--limit', type=int, default=0, help='0 evaluates all rows')
    p.add_argument('--skip_single_world', action='store_true')
    p.add_argument('--skip_fixed_anchor', action='store_true')
    args = p.parse_args()
    if args.max_states < 1 or args.limit < 0: p.error('invalid max_states/limit')
    with Path(args.cases).open(encoding='utf-8') as handle:
        cases = [json.loads(line) for line in handle if line.strip()]
    if args.limit: cases = cases[:args.limit]
    rows, summary = run(cases, args.max_states, not args.skip_single_world,
                        not args.skip_fixed_anchor)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(''.join(json.dumps(x, ensure_ascii=False) + '\n' for x in rows), encoding='utf-8')
    out.with_suffix('.summary.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
