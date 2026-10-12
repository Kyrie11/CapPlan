#!/usr/bin/env python3
"""Conservative preregistered research gate; never infer CCF-A novelty from toy tests."""
from __future__ import annotations
import argparse,csv,json
from pathlib import Path


def assess(funnel_path,policy_path,review_csv,output):
    funnel=json.loads(Path(funnel_path).read_text(encoding='utf-8'))
    policies=json.loads(Path(policy_path).read_text(encoding='utf-8'))
    rows=list(csv.DictReader(Path(review_csv).open(encoding='utf-8',newline='')))
    reviewed=[r for r in rows if str(r.get('decision','')).strip().upper() in ('PASS','FAIL')]
    reviewed_positive=[r for r in reviewed if r['decision'].strip().upper()=='PASS']
    counts=funnel['denominators'];methods=policies.get('methods',{})
    baseline=methods.get('actual_ledger_myopic',{})
    verify=methods.get('verify_then_guide',{})
    fixed=methods.get('robust_fixed_anchor',{})
    exact=methods.get('exact_finite_support',{})
    margin=(verify.get('mean_weighted_candidate_success',0)-baseline.get('mean_weighted_candidate_success',0)) if methods else 0
    burden=(fixed.get('mean_weighted_distance_m',0)-exact.get('mean_weighted_distance_m',0)) if methods else 0
    gates={
      'nontrivial_geometry_candidates':counts.get('screened_A_B_pairs',0)>=20,
      'site_variety_for_screening':funnel.get('known_distinct_site_pairs',0)>=5,
      'method_specific_vehicle_motion_packets':counts.get('vehicle_verified_pairs',0)>=10,
      'five_independently_reviewed_sites':len({str(r.get('physical_site_pair')) for r in reviewed_positive})>=5,
      'baseline_difference_in_controlled_model':bool(margin>0 or burden>0),
      'independent_actual_vehicle_handoff_confirmations':False,
      'published_strength_resource_shield_reproduced':False,
      'empirical_population_calibration':False,
    }
    stage='HOLD_GEOMETRY_EVIDENCE' if not gates['nontrivial_geometry_candidates'] else (
      'HOLD_MOTION_AND_REVIEW' if not gates['method_specific_vehicle_motion_packets'] or
                                  not gates['five_independently_reviewed_sites'] else (
      'HOLD_BASELINE_MOTIVATION' if not gates['baseline_difference_in_controlled_model'] else
      'READY_TO_IMPLEMENT_STRONG_BASELINE_NOT_CCF_A_GO'))
    data={'status':stage,'evidence_checks':gates,'review_rows_completed':len(reviewed),
          'review_rows_positive':len(reviewed_positive),
          'synthetic_baseline_success_gap_verify_vs_myopic':margin,
          'synthetic_burden_gap_fixed_minus_exact_m':burden,
          'interpretation':'Screening readiness only, never a formal novelty or safety proof; false until audited external evidence exists',
          'next_actions':[
              'Finish method-specific vehicle motion trajectories and time-window proofs',
              'Complete real PUDO human audit with both positive and negative controls',
              'Reproduce strong resource-constrained belief-space shielding, not only the fixed-B proxy',
              'Vary information validity, observation timing, two-sided unavailability and arrival/dwell constraints',
              'Evaluate frozen policy on drop-off and integrated independently verified vehicle ride',
          ]}
    dest=Path(output);dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps({'research_stage':stage,'evidence_checks':gates},ensure_ascii=False))
    return data

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--funnel_report',required=True)
    p.add_argument('--policy_summary',required=True);p.add_argument('--review_csv',required=True)
    p.add_argument('--output',required=True);a=p.parse_args()
    assess(a.funnel_report,a.policy_summary,a.review_csv,a.output)
