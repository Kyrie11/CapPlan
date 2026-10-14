#!/usr/bin/env python3
"""Fail-closed audit of CapPlan G0--G4 reports. NO claim of real-world feasibility.

The review bundle contains *summaries* only. It cannot retroactively validate
individual edges, CARLA trajectories, reviewers or support-world correctness.
"""
from __future__ import annotations
import argparse, hashlib, json, zipfile
from pathlib import Path


def inspect_zip(path):
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        manifest = json.loads(z.read('REPRODUCIBILITY_MANIFEST.json')) if 'REPRODUCIBILITY_MANIFEST.json' in names else {}
        mismatched=[]; missing=[]
        for name, expected in manifest.get('files',{}).items():
            if name not in names: missing.append(name)
            elif hashlib.sha256(z.read(name)).hexdigest()!=expected:mismatched.append(name)
        def load(name):
            return json.loads(z.read(name)) if name in names else {}
        g0=load('gate0_preflight.json');g1=load('gate1/gate1_selection.json')
        phy=load('gate1/physical_evidence_v4.json');g2=load('gate2/gate2_motion_audit.json')
        g3=load('gate3/midroute.summary.json');third=load('gate3/alternative_pudos.summary.json')
        g4=load('gate4/profile_time_grid.manifest.json');paths=load('gate4/profile_paths_v4.summary.json')
        pol=load('gate4/policies/policy_summary.json')
    oracle=(pol.get('methods',{}).get('exact_finite_support') or {})
    fixed=(pol.get('methods',{}).get('robust_fixed_anchor') or {})
    orate=oracle.get('conditional_support_guarantee_rate'); frate=fixed.get('conditional_support_guarantee_rate')
    report={'review_input':str(path),'integrity':{'manifest_checked':bool(manifest),'hash_mismatches':mismatched,'missing_files':missing},
     'gate0':{'pairs':g0.get('pairs',{}).get('counts',{}).get('pairs'),'pair_file_coverage':g0.get('pairs',{}).get('pair_file_coverage'),'representative_natural_event_rate':False},
     'gate1':{'selected_site_pairs':g1.get('review_queue_size',0),'independent_physical_confirmations':0,'missing_typed_curb_connector_endpoints':phy.get('risks',{}).get('missing_typed_curb_connector_endpoints')},
     'gate2':{'motion_requests':g2.get('requests',0),'log_bound_attestations':g2.get('log_bound_attestation_count',0),'independent_real_handoff_confirmations':0},
     'gate3':{'graph_pairs':g3.get('counts',{}).get('pairs_processed'),'graph_early_possible':g3.get('counts',{}).get('graph_possible_0.0'),'graph_90s_possible':g3.get('counts',{}).get('graph_possible_90.0'),'other_graph_pudo_pairs':third.get('counts',{}).get('graph_other_possible'),'positive_is_upper_bound_only':True,'does_not_prove_global_no_recourse':True},
     'gate4':{'controlled_overlays':g4.get('overlays_generated'),'full_capability_validation':g4.get('full_capability_validation'),'partial_profile_path_checks':paths.get('counts',{}).get('profile_pair_evaluations'),'oracle_rate':orate,'fixed_rate':frate,'matched_oracle_fixed':orate is not None and frate is not None and abs(orate-frate)<1e-10,'strong_shield_reproduced':False},
     'scientific_claims':{'math_counterexample_possible':'SUPPORTED_ONLY_IN_CONTROLLED_FINITE_MODEL','real_scenario_verified':'NOT_YET','natural_incidence_non_negligible':'NOT_YET','complex_online_recourse_required':'NOT_ESTABLISHED','full_capability_comparison':'NOT_YET'},
     'recommended_next_gate':'PHYSICAL_SITE_AUDIT_AND_INDEPENDENT_MOTION',
     'pass':not bool(mismatched or missing)}
    if not report['pass']: report['recommended_next_gate']='FIX_INPUT_INTEGRITY'
    return report


def main():
    p=argparse.ArgumentParser();p.add_argument('--review_zip',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();d=inspect_zip(a.review_zip);out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(d,indent=2,ensure_ascii=False)+'\n');print(json.dumps(d['scientific_claims'],ensure_ascii=False))
    if not d['pass']:raise SystemExit(2)
if __name__=='__main__': main()
