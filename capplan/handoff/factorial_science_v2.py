"""Factorial falsification audit; only reads declared 2x2x2 controlled rows.

Interaction counts are descriptive model contrasts, NOT causal effect estimates
for naturally occurring curb incidents or representative passengers.
"""
from __future__ import annotations
from collections import Counter,defaultdict
from pathlib import Path
import json


def analyze_factorial(rows_path: Path, output: Path) -> dict:
    groups=defaultdict(dict);duplicates=[];scope=Counter()
    count=0
    with Path(rows_path).open(encoding='utf-8') as f:
        for line in f:
            if not line.strip():continue
            r=json.loads(line);count+=1
            factors=r['factors']
            bits=tuple(int(factors[k]) for k in (
                'capability_budget_level','evidence_expiry_level','vehicle_arrival_level'))
            if any(v not in (0,1) for v in bits):raise ValueError('requires 2x2x2 binary factors')
            key=(str(r['episode_id']),str(r['a_anchor']),str(r['b_anchor']),
                 str(r['notice_issued_s']))
            if bits in groups[key]:duplicates.append({'group':key,'factors':bits})
            groups[key][bits]=bool(r['robust_fixed_B_safe'])
            scope[str(r.get('synthetic_window_source','UNDECLARED'))]+=1
    if duplicates:raise ValueError(f'duplicate factorial groups: {duplicates[:2]}')
    summary=Counter();hist=Counter();examples=[]
    for key,out in groups.items():
        if len(out)!=8:
            summary['incomplete_groups']+=1
            continue
        summary['complete_groups']+=1
        if len(set(out.values()))==2:summary['any_safe_unsafe_flip']+=1
        for axis,name in enumerate(('capability','evidence','vehicle')):
            if any(out[k] != out[k[:axis]+(1-k[axis],)+k[axis+1:]]
                   for k in out): summary[name+'_paired_sensitivity']+=1
            if axis<=1: # level 1 is the controlled relaxation
                if any(out[k] and not out[k[:axis]+(1,)+k[axis+1:]]
                       for k in out if k[axis]==0):
                    summary[name+'_relaxation_monotonicity_violation']+=1
        # Seven low-order contrasts plus the three-way interaction. A nonzero
        # third difference does NOT imply a unique three-factor physical cause.
        effect=(int(out[1,1,1])-int(out[1,1,0])-int(out[1,0,1])-int(out[0,1,1])
                +int(out[1,0,0])+int(out[0,1,0])+int(out[0,0,1])-int(out[0,0,0]))
        hist[str(effect)]+=1
        if effect:
            summary['nonzero_three_factor_boolean_contrast']+=1
            if len(examples)<20:examples.append({'group':key,'third_difference':effect,
                            'table':{''.join(map(str,k)):int(v) for k,v in sorted(out.items())}})
        # Sensitivity for joint factor combinations, without claiming causality.
        if out[0,0,0] != out[1,1,1]:summary['corner_000_vs_111_flip']+=1
        if all(out[k] == 0 for k in out if k!=(1,1,1)) and out[1,1,1]:
            summary['strict_all_three_relaxations_required_pattern']+=1
    result={'schema':'capplan_factorial_falsification_v2','rows':count,
            'unique_groups':len(groups),'summary':dict(summary),
            'third_difference_distribution':dict(hist),'examples':examples,
            'input_scope_counts':dict(scope),'natural_incidence_estimable':False,
            'independent_physical_site_results':False,
            'explanation':'Three-way Boolean contrast is an interaction in declared synthetic responses, not independent physical proof.'}
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return result
