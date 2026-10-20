#!/usr/bin/env python3
"""Falsify H2/H3/H4 in a fully specified, controlled joint-service world.

This tool is NOT a reproduction of Sheng et al. ICRA 2024, nor a real-world
CARLA/nuPlan experiment. It deliberately provides a fully capable *generic*
finite-support AND/OR reference with the SAME site/vehicle/observation/action
model as every first-step heuristic. Heuristic results are root action viability
only, not claimed end-to-end service success.

Accepts controlled cases as JSONL. Produces rows with exact reference status,
safe first-action set, counterfactual interaction evidence and resource caps.
"""
from __future__ import annotations
import argparse,json,math,sys
from collections import Counter,defaultdict
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from capplan.handoff.commitment_model_v10 import FiniteCommitmentModel,GenericSupportSolver,cheapest_heuristic_actions,verify_policy_in_all_worlds


def evaluate_cases(cases,max_states=40000):
    result=[]
    for scene in cases:
        model=FiniteCommitmentModel(scene)
        solver=GenericSupportSolver(model,max_states=max_states)
        root=solver.solve()
        policy=solver.memo.get(model.initial, (None,None))[1] if root.status=='WIN' else None
        replay=verify_policy_in_all_worlds(model,policy) if policy is not None else None
        heuristics=cheapest_heuristic_actions(model)
        results={n:{'first_action':act,'first_action_is_certified_recoverable':
                  (act in root.first_safe_actions if root.status=='WIN' else None),
                    'not_a_complete_policy':True} for n,act in heuristics.items()}
        row={'case_id':scene['case_id'],'family':scene.get('family','unknown'),
             'worlds':len(scene['worlds']),'sites':len(scene['sites']),
             'profile_label':scene.get('profile_label','not_population_grounded'),
             'pair_group':scene.get('pair_group'),
             'intervention':scene.get('intervention',{}),
             'outcome':root.__dict__,
             'first_step_heuristics':results,
             'all_world_policy_replay':{'all_success':all(r['success'] for r in replay.values()),
                 'world_count':len(replay)} if replay is not None else None,
             'evidence_type':scene.get('evidence_type','CONTROLLED_SYNTHETIC_NOT_REAL_CITY'),
             'real_handoff_verified':False,
             'publication_strong_baseline_reproduced':False}
        result.append(row)
    counter=Counter(x['outcome']['status'] for x in result)
    groups=defaultdict(list)
    for row in result:
        if row['pair_group'] is not None:groups[row['pair_group']].append(row)
    contrasts=[]
    for g,rows in sorted(groups.items()):
        if len(rows)<2:continue
        exact_action_set_change=len(set(tuple(row['outcome']['first_safe_actions']) for row in rows))>1
        winning_change=len(set(row['outcome']['status'] for row in rows))>1
        best_action_change=len(set(row['outcome']['best_action'] for row in rows))>1
        best_prefix_change=len(set(tuple(x['action'] for x in row['outcome']['policy_prefix'])
                                    for row in rows))>1
        contrasts.append({'pair_group':g,'cases':[x['case_id'] for x in rows],
                          'safe_first_action_set_change':exact_action_set_change,
                          'winning_status_change':winning_change,
                          'best_first_action_change':best_action_change,
                          'best_policy_prefix_change':best_prefix_change,
                          'scope':'CONTROLLED_FINITE_MODEL_ONLY'})
    # Predeclared 2x2x2 finite controlled factorial: interaction is descriptive
    # only. Never treat intentionally constructed truth as an empirical p-value.
    f=[r for r in result if r['family']=='H4_factorial']
    factorial=None
    if len(f)==8:
        y={}
        for r in f:
            v=r['intervention'];key=(v['required_width_m']==1.2,
                        v['b_cert_expiry']==1,v['vehicle_start']=='A')
            y[key]=int(r['outcome']['status']=='WIN')
        if len(y)==8:
            # Discrete 3-way contrast, not a significance test.
            three=sum(((-1)**(3-sum(k)))*val for k,val in y.items())
            factorial={'cells':8,'guaranteed_completion_cells':sum(y.values()),
                       'three_way_binary_contrast':three,
                       'joint_interaction_is_a_controlled_mechanism_only':True}
    # Fair reporting scope: each heuristic gets the exact model's same
    # first-action permissions. This counts whether its PROPOSAL is viable,
    # not whether the heuristic defines a winning policy after observation.
    heuristic_counts={}
    for name in ('nearest_static','vehicle_first','fixed_evidence_anchor'):
        vals=[r['first_step_heuristics'][name]['first_action_is_certified_recoverable']
              for r in result if r['outcome']['status']=='WIN']
        heuristic_counts[name]={
            'winning_case_denominator':len(vals),
            'certified_first_action_count':sum(v is True for v in vals),
            'unsafe_first_action_count':sum(v is False for v in vals),
            'comparison_scope':'ROOT_ACTION_ONLY_NOT_COMPLETE_POLICY_NOT_PUBLISHED_BASELINE'}
    return result,{'num_cases':len(result),'status_counts':dict(counter),
        'heuristic_first_action_on_winning_cases':heuristic_counts,
        'factorial_H4':factorial,
        'num_intervention_groups':len(contrasts),
        'changed_safe_action_groups':sum(x['safe_first_action_set_change'] for x in contrasts),
        'changed_winning_status_groups':sum(x['winning_status_change'] for x in contrasts),
        'changed_best_first_action_groups':sum(x['best_first_action_change'] for x in contrasts),
        'changed_best_policy_prefix_groups':sum(x['best_policy_prefix_change'] for x in contrasts),
        'all_returned_winning_policies_replayed':all(row['all_world_policy_replay'] is None
            or row['all_world_policy_replay']['all_success'] for row in result),
        'num_cases_with_unknown_cap':sum(x['outcome']['unknown_due_to_cap'] for x in result),
        'contrasts':contrasts,
        'conclusions_forbidden':['all generic POMDP shields fail','physical handoff verified',
          'incidence in four cities','published ICRA shield baseline reproduced',
          'heuristic first-action viability equals full-policy success'],
        'interpretation':'Generic exact model is an upper computational reference for the declared finite model, not a full continuous or published shielding method.'}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cases',required=True)
    p.add_argument('--output',required=True)
    p.add_argument('--max_states',type=int,default=40000)
    a=p.parse_args()
    if a.max_states<1:p.error('max_states must be >= 1')
    scenes=[json.loads(x) for x in Path(a.cases).read_text(encoding='utf8').splitlines() if x.strip()]
    rows,summary=evaluate_cases(scenes,a.max_states)
    out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in rows),encoding='utf-8')
    out.with_suffix('.summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False))
if __name__=='__main__':main()
