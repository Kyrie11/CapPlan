#!/usr/bin/env python3
"""Paired counterfactual contrast report, NOT empirical population or event rates."""
from __future__ import annotations
import argparse,json
from pathlib import Path


def summarize(folder, output):
    root=Path(folder)
    report={'scope':'controlled-event sensitivity on the SAME selected geometry; no observed incident incidence',
            'not_independent_empirical_evidence':True,'cases':{},'contrasts':{}}
    for p in sorted(root.glob('*/policies/policy_summary.json')):
        data=json.loads(p.read_text(encoding='utf-8'))
        names=data.get('methods',{})
        def metric(method,field):return names.get(method,{}).get(field)
        report['cases'][p.parent.parent.name]={
          'evaluated_overlays':data.get('evaluated_overlays'),
          'errors':len(data.get('errors',[])),
          'privileged_schedule_overlay_count':data.get('privileged_schedule_overlay_count',0),
          'verified_motion_overlays':data.get('vehicle_verified_overlay_count',0),
          'success':{n:metric(n,'mean_weighted_candidate_success') for n in names},
          'distance_m':{n:metric(n,'mean_weighted_distance_m') for n in names},
          'post_commitment_recourse_loss':{n:metric(n,'post_commitment_recourse_loss_rate') for n in names},
          'false_commit':{n:metric(n,'conditional_false_commit_rate') for n in names},
        }
        d=report['cases'][p.parent.parent.name]
        for a,b in [('exact_finite_support','verify_then_guide'),
                    ('exact_finite_support','robust_fixed_anchor'),
                    ('verify_then_guide','actual_ledger_myopic')]:
            x,y=d['success'].get(a),d['success'].get(b)
            report['contrasts'][f'{p.parent.parent.name}:{a}-minus-{b}']=(x-y if x is not None and y is not None else None)
    lengths={c['evaluated_overlays'] for c in report['cases'].values()}
    report['paired_episode_denominators_match']=len(lengths)==1 and bool(lengths)
    report['research_interpretation']=(
        'Only meaningful case-specific differences plus audited GIS, real motion, and external failure evidence '
        'can motivate a general algorithm; the optimal finite action solver is not a full POMDP benchmark.')
    out=Path(output);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'cases':len(report['cases']), 'paired_counts':report['paired_episode_denominators_match']}))
    return report

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--sensitivity_dir',required=True)
    parser.add_argument('--output',required=True)
    a=parser.parse_args()
    summarize(a.sensitivity_dir,a.output)
