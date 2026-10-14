#!/usr/bin/env python3
"""Stage-aware evidence router. 7 gates, no artificial PASS for external work.

'prepare' produces study files without silently allowing subsequent scientific
claims; 'report' rechecks reviewed evidence and chooses a next *research action*.
"""
from __future__ import annotations
import argparse,hashlib,json,subprocess,sys,zipfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from capplan.handoff.gate_evidence import (inspect_pairs,inspect_overlays,inspect_profiles,
    inspect_review,dump_json)
from scripts.select_gate1_site_review import select
from scripts.audit_gate2_motion import audit
from scripts.build_gate4_profile_time_grid import build
from scripts.export_handoff_motion_requests import export


def _load(path):
    return json.loads(Path(path).read_text(encoding='utf-8')) if Path(path).is_file() else None


def report(output_dir,review_csv=None):
    root=Path(output_dir)
    pre=_load(root/'gate0_preflight.json') or {}
    site=_load(root/'gate1'/'gate1_selection.json') or {}
    motion=_load(root/'gate2'/'gate2_motion_audit.json') or {}
    mid=_load(root/'gate3'/'midroute.summary.json') or {}
    grid=_load(root/'gate4'/'profile_time_grid.manifest.json') or {}
    review=inspect_review(review_csv) if review_csv and Path(review_csv).is_file() else {}
    bad=(pre.get('overlays') or {}).get('counts',{}).get('invalid_overlay',0)
    n=(pre.get('pairs') or {}).get('counts',{}).get('pairs',0)
    motion_bound=motion.get('log_bound_attestation_count',0)
    peer_sites=review.get('strictly_completed_unique_sites',0)
    # Scope distinction: a log-bound checker attestation is NOT independent
    # motion validation, and no synthetic policy experiment is a strong baseline.
    gates={
      'G0_protocol_and_temporal_integrity':{'status':'BLOCKED' if not n or bad else 'TECHNICAL_SCREEN_ONLY',
          'reason':f'candidate pairs={n}; invalid overlays={bad}; assess sampling and timing in preflight'},
      'G1_real_curb_and_pudo':{'status':'EXTERNAL_REVIEW_PENDING' if peer_sites==0 else 'MANUAL_ATTESTATIONS_RECEIVED_REVIEW_REQUIRED',
          'confirmed_independent_site_pairs_under_structured_review':peer_sites,
          'note':'A reviewer PASS is an external assertion; spot-check primary source and negative controls'},
      'G2_method_specific_vehicle_motion':{'status':'EXTERNAL_MOTION_PENDING' if not motion_bound else 'CHECKER_ATTESTATIONS_RECEIVED_REVIEW_REQUIRED',
          'log_bound_attestations':motion_bound,'independent_closed_loop_confirmations':0},
      'G3_expanded_recourse_and_midroute':{'status':'GRAPH_FALSIFICATION_ONLY' if mid.get('counts',{}).get('pairs_processed',0)>0 else 'PENDING_GRAPH_FALSIFICATION',
          'note':'Third PUDO, vehicle move, safe return/assistance and full causal belief action space NOT solved'},
      'G4_capability_and_observation_interaction':{'status':'PARTIAL_CANONICAL_GRID' if grid.get('overlays_generated',0)>0 else 'PENDING_PROFILE_BINDING',
          'note':'Only distance + synthetic wait controlled; NO complete personal capabilities or empirical population'},
      'G5_published_strength_belief_shield':{'status':'BLOCKED_NOT_IMPLEMENTED',
          'note':'Generic resource shield proxy is NOT a reproduced peer-reviewed strong baseline'},
      'G6_frozen_dropoff_transfer':{'status':'BLOCKED_UNTIL_PREVIOUS_GATES',
          'note':'Requires frozen protocol and independently checked drop-off execution'},
    }
    no_curb=(pre.get('pairs') or {}).get('counts',{}).get('risk:TYPED_CURB_CONNECTOR_NOT_BOTH',0)
    coverage=(pre.get('pairs') or {}).get('pair_file_coverage','UNVERIFIED_COMPLETENESS')
    if n==0 or bad:next_branch='FIX_G0_SCHEMA_AND_TIME'
    elif n and no_curb/n>.5 and not peer_sites:next_branch='G1_FIX_CURB_REPRESENTATION_AND_AUDIT'
    elif peer_sites==0:next_branch='G1_INDEPENDENT_PHYSICAL_PUDO_AUDIT'
    elif not motion_bound:next_branch='G2_RUN_EXTERNAL_MOTION_CHECKER'
    else:next_branch='G3_ENLARGE_REAL_ACTION_SPACE_THEN_G5_STRONG_BASELINE'
    d={'decision':next_branch,'pair_file_coverage':coverage,
       'population_inference_warning':'Selection file is a truncated example bundle or its completeness is unknown' if coverage!='MATCHED_COMPLETE_PAIRS' else 'Full geometric screening is not representative of natural events',
       'gates':gates,
       'claim_boundaries':{'mechanism_possible':'controlled model and geometry only',
           'failure_non_negligible_population_frequency':'NOT_ESTABLISHED',
           'scientific_novelty_vs_strong_shield':'NOT_ESTABLISHED',
           'true_vehicle_and_passenger_completion':'NOT_ESTABLISHED'},
       'artifacts':{'preflight':bool(pre),'site_selection':bool(site),'motion_attestations':bool(motion),
           'midroute_test':bool(mid),'profile_grid':bool(grid),'review_file_supplied':bool(review)},
       'human_review':review}
    dump_json(root/'research_gate_decision.json',d)
    return d


def prepare(args):
    root=Path(args.output_dir);root.mkdir(parents=True,exist_ok=True)
    p=inspect_pairs(args.pairs)
    o=inspect_overlays(args.overlays)
    info={'pairs':p,'overlays':o,'profiles':inspect_profiles(args.profiles) if args.profiles else None,
          'warnings':['Pair and overlay denominators are conditioned on mining settings',
                      'Controlled events do not estimate natural incident frequency',
                      'Existing two-anchor oracle cannot prove global no-recourse']}
    dump_json(root/'gate0_preflight.json',info)
    select(args.pairs,root/'gate1',args.per_city)
    chosen=root/'gate1'/'selected_review_pairs.jsonl'
    from scripts.audit_handoff_evidence_v4 import analyze as evidence_audit
    evidence_audit(chosen,root/'gate1'/'physical_evidence_v4.json')
    req=root/'gate2'/'motion_requests.jsonl';req.parent.mkdir(exist_ok=True)
    export(chosen,req,False)
    audit(req,args.motion_claims,root/'gate2',log_root=args.log_root)
    if args.dataset_dir:
        from scripts.sample_unscreened_handoff_controls_v4 import run as sample_unscreened
        sample_unscreened(args.dataset_dir,root/'gate0_unscreened_episode_controls.jsonl',per_city=20)
        from scripts.select_gate1_anchor_controls import build as controls
        controls(args.dataset_dir,chosen,root/'gate1'/'anchor_control_queue.csv')
        from scripts.audit_handoff_midroute import run
        run(args.dataset_dir,chosen,root/'gate3'/'midroute.jsonl',args.notice_times,
            args.speed_mps,args.ack_s,args.midroute_limit)
        from scripts.audit_handoff_alternatives_v4 import run as alternative_audit
        alternative_audit(chosen,args.dataset_dir,root/'gate3'/'alternative_pudos.jsonl',args.midroute_limit)
    if args.profiles and args.dataset_dir:
        from scripts.audit_handoff_profile_paths_v4 import run as profile_paths
        profile_paths(chosen,args.profiles,args.dataset_dir,root/'gate4'/'profile_paths_v4.jsonl',
                      max_pairs=args.midroute_limit,max_profiles=40)
    if args.profiles:
        r=build(args.overlays,args.profiles,root/'gate4'/'profile_time_grid.jsonl',
            max_sites=args.max_grid_sites,max_profiles=args.max_grid_profiles,
            wait_budgets=args.wait_budgets)
        if args.evaluate_grid and r['overlays_generated']:
            from scripts.evaluate_handoff_policies import evaluate
            result=evaluate(root/'gate4'/'profile_time_grid.jsonl',root/'gate4'/'policies',
                            speed_mps=args.speed_mps)
            if result:raise RuntimeError('profile grid policy evaluator reported errors')
    result=report(root,args.review_csv)
    from scripts.triage_handoff_science_v4 import triage
    triage(root,root/'research_triage_v4.json')
    return result


def bundle(root,output):
    root=Path(root).resolve();out=Path(output);out.parent.mkdir(parents=True,exist_ok=True)
    include=['gate0_unscreened_episode_controls.manifest.json','gate0_preflight.json','research_gate_decision.json','research_triage_v4.json',
             'gate1/physical_evidence_v4.json','gate1/gate1_selection.json',
             'gate1/site_review_queue.csv','gate1/site_review_queue_REGENERATED_UNREVIEWED.csv',
             'gate1/anchor_control_queue.csv','gate1/anchor_control_queue.manifest.json',
             'gate1/selected_review_pairs.jsonl','gate2/gate2_motion_audit.json',
             'gate2/motion_requests.manifest.json','gate3/midroute.summary.json',
             'gate3/alternative_pudos.summary.json',
             'gate4/profile_time_grid.manifest.json','gate4/profile_paths_v4.summary.json',
             'gate4/policies/policy_summary.json']
    files=[root/f for f in include if (root/f).is_file()]
    manifest={'scope':'small research evidence bundle; no original nuPlan data or raw trajectory logs',
              'files':{str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}}
    with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for p in files:z.write(p,p.relative_to(root))
        z.writestr('REPRODUCIBILITY_MANIFEST.json',json.dumps(manifest,indent=2,ensure_ascii=False))
    return {'bundle':str(out),'files':len(files)}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('phase',choices=('prepare','report','pack'))
    p.add_argument('--pairs');p.add_argument('--overlays');p.add_argument('--profiles')
    p.add_argument('--dataset_dir');p.add_argument('--output_dir',required=True)
    p.add_argument('--review_csv');p.add_argument('--motion_claims');p.add_argument('--log_root')
    p.add_argument('--per_city',type=int,default=10);p.add_argument('--midroute_limit',type=int,default=40)
    p.add_argument('--notice_times_s',default='0,30,60,90,120,180')
    p.add_argument('--speed_mps',type=float,default=1.0);p.add_argument('--ack_s',type=float,default=3.0)
    p.add_argument('--max_grid_sites',type=int,default=8);p.add_argument('--max_grid_profiles',type=int,default=8)
    p.add_argument('--wait_budgets',default='30,120,none');p.add_argument('--evaluate_grid',action='store_true')
    p.add_argument('--bundle_output')
    a=p.parse_args()
    if a.phase=='pack':
        if not a.bundle_output:p.error('--bundle_output required for pack')
        d=bundle(a.output_dir,a.bundle_output)
    elif a.phase=='report':d=report(a.output_dir,a.review_csv)
    else:
        if not a.pairs or not a.overlays:p.error('--pairs and --overlays required for prepare')
        if a.per_city<=0 or a.midroute_limit<=0 or a.max_grid_sites<=0 or a.max_grid_profiles<=0:
            p.error('sample sizes must be positive')
        a.notice_times=[float(x) for x in a.notice_times_s.split(',')]
        a.wait_budgets=[None if x.lower()=='none' else float(x) for x in a.wait_budgets.split(',')]
        if any(not (x>=0 and x<float('inf')) for x in a.notice_times):p.error('invalid notice times')
        if any(x is not None and (x<0 or x==float('inf')) for x in a.wait_budgets):p.error('invalid wait budgets')
        d=prepare(a)
    print(json.dumps(d if a.phase!='prepare' else {'decision':d['decision'],'gate_status':{k:v['status'] for k,v in d['gates'].items()}},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
