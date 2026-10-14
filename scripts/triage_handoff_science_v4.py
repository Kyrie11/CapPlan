#!/usr/bin/env python3
"""Evidence-aware research pivot. This is a protocol report, NOT novelty classifier."""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from capplan.handoff.gate_evidence import dump_json,inspect_review

def read(p):
    try:return json.loads(Path(p).read_text(encoding='utf-8'))
    except (FileNotFoundError,ValueError):return None

def triage(root,out):
    root=Path(root)
    pre=read(root/'gate0_preflight.json') or {}
    selection=read(root/'gate1'/'gate1_selection.json') or {}
    physical=read(root/'gate1'/'physical_evidence_v4.json') or {}
    motion=read(root/'gate2'/'gate2_motion_audit.json') or {}
    mid=read(root/'gate3'/'midroute.summary.json') or {}
    alt=read(root/'gate3'/'alternative_pudos.summary.json') or {}
    pf=read(root/'gate4'/'policies'/'policy_summary.json') or {}
    n=int((pre.get('pairs') or {}).get('counts',{}).get('pairs',0))
    # Structured reviewer declarations are not independent physical proof.
    reviewed_file=root/'gate1'/'site_review_queue.csv'
    reviewer_claims=inspect_review(reviewed_file) if reviewed_file.is_file() else {}
    reviewed_pairs=int(reviewer_claims.get('strictly_completed_unique_sites',0))
    checker_attestations=int(motion.get('log_bound_attestation_count',0))
    confirmed_real=0  # Independent end-to-end confirmation requires external examination.
    tested=mid.get('counts',{}).get('pairs_processed',0)
    from_start=mid.get('counts',{}).get('graph_possible_0.0')
    observations={'screened_candidate_pairs':n,
      'site_pairs_selected_for_audit':selection.get('review_queue_size',0),
      'physical_handoff_confirmations':confirmed_real,
      'structured_review_pass_declarations':reviewed_pairs,
      'log_bound_motion_attestations':checker_attestations,
      'motion_requests':motion.get('requests',0),
      'independently_confirmed_motion':motion.get('independent_real_handoff_confirmations',0),
      'graph_midroute_cases':tested,'graph_redirect_possible_at_issued_t0':from_start,
      'midroute_clock_semantics':mid.get('clock_semantics','LEGACY_OR_UNKNOWN_REQUIRES_RERUN'),
      'graph_other_pudos_possible':(alt.get('counts') or {}).get('graph_other_possible'),
      'synthetic_fixed_support_rate':(pf.get('methods') or {}).get('robust_fixed_anchor',{}).get('conditional_support_guarantee_rate'),
      'synthetic_oracle_support_rate':(pf.get('methods') or {}).get('exact_finite_support',{}).get('conditional_support_guarantee_rate')}
    if n==0:
        branch='G0_FIX_INPUT_PROTOCOL'
    elif reviewed_pairs==0:
        branch='G1_INDEPENDENT_PUDO_SITE_AUDIT_REQUIRED'
    elif checker_attestations==0:
        branch='G2_RUN_AND_REVIEW_METHOD_SPECIFIC_VEHICLE_MOTION'
    else:
        branch='G3_G4_REVIEW_PAIRED_PHYSICAL_AND_MOTION_ATTESTATIONS' 
    scientific={'mechanism_math':'SUPPORTED_WITHIN_FINITE_CONSTRUCTED_MODEL' if n else 'NOT_TESTED',
      'urban_geometry':'SCREENED_CANDIDATE_ONLY' if n else 'NOT_TESTED',
      'real_physical_handoff_failure':'NOT_ESTABLISHED',
      'natural_event_incidence':'NOT_ESTABLISHED_NO_UNSELECTED_DENOMINATOR',
      'unique_algorithms_required':'NOT_ESTABLISHED_STRONG_BASELINES_MISSING',
      'gate3_global_no_recourse':'NOT_ESTABLISHED_THIRD_PUDO_AND_MOTION_UNKNOWN',
      'gate4_true_profile_decision':'NOT_ESTABLISHED_MOST_CAPABILITY_AXES_NOT_BOUND'}
    cautions=[]
    if reviewed_pairs and checker_attestations:
        cautions.append('Separate reviewer and checker PASS fields do not prove the same actual handoff world; require independent matched event and pose audit.')
    if physical.get('risks',{}).get('missing_typed_curb_connector_endpoints',0):
        cautions.append('Missing typed curb links are data unknowns, NOT measured physical obstructions.')
    if physical.get('risks',{}).get('stop_pose_equals_curb_proxy_endpoints',0):
        cautions.append('Stop and curb poses coincide in some records: validate orientation/deployment geometry.')
    if tested and 'clock_semantics' not in mid:
        cautions.append('Old midroute summary has no acknowledgement clock semantics; re-run on updated code before claiming an exact time threshold.')
    if from_start and tested:
        cautions.append('Some screened A->B failures are avoidable by early redirect, NOT global strandedness.')
    if observations['synthetic_fixed_support_rate'] is not None and observations['synthetic_oracle_support_rate'] is not None:
        if abs(observations['synthetic_fixed_support_rate']-observations['synthetic_oracle_support_rate'])<1e-9:
            cautions.append('Current finite controlled policy grid cannot establish a benefit over robust fixed anchor.')
    d={'research_branch':branch,'observations':observations,'claim_status':scientific,
      'cautions':cautions,'next_protocol':[
       'Audit curb connection, stop permission, deployment footprint and pedestrian route with primary source and negative controls.',
       'Bind verified vehicle stop pose, motion log, time window and passenger/vehicle rendezvous.',
       'Recompute Gate3 with actionable acknowledgement clock and third-PUDO falsification.',
       'Use pre-selected unscreened episodes to estimate only sampling-domain prevalence after event incidence data exist.',
       'Evaluate strong verification, fixed-anchor, vehicle move, and published shield under exactly equal observations/actions.'
      ],'real_incidence_estimate':None,'novelty_pass':False}
    dump_json(out,d);return d

if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--study_dir',required=True);a.add_argument('--output',required=True)
    args=a.parse_args();d=triage(args.study_dir,args.output)
    print(json.dumps({'branch':d['research_branch'],'claim_status':d['claim_status']},ensure_ascii=False,indent=2))
