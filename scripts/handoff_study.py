#!/usr/bin/env python3
"""Stable CapPlan handoff science CLI. New work must extend THIS tool, not add _vNN scripts.

Read-only research commands never update original nuPlan/GIS graphs or labels.
Historical _vNN scripts remain runnable for prior study reproduction only.
"""
from __future__ import annotations
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from capplan.handoff.research import _atomic_json, classify_carla_triplet, run_graph_screen, site_attestation_audit, legacy_research_inventory


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    s = p.add_subparsers(dest="command", required=True)
    a = s.add_parser("carla-triplets", help="Require actual redirect_B and matched per-trial controls")
    a.add_argument("--study", required=True, help="directory containing clear_A/blocked_A/redirect_B")
    a.add_argument("--output", required=True)
    five = s.add_parser("carla-fiveway", help="Audit clear-A, blocked-A, clear-B, unblocked-redirect, blocked-redirect controls")
    five.add_argument("--study", required=True)
    five.add_argument("--output", required=True)
    b = s.add_parser("directed-remine", help="Fresh directional shortest paths, strict+optimistic sensitivity")
    b.add_argument("--dataset-dir", required=True)
    b.add_argument("--pairs", required=True)
    b.add_argument("--output", required=True)
    b.add_argument("--per-city", type=int, default=8)
    b.add_argument("--times", default="0,15,30,60,90")
    b.add_argument("--speed-mps", type=float, default=0.8)
    b.add_argument("--ack-s", type=float, default=3.)
    b.add_argument("--third-options", action="store_true")
    a2 = s.add_parser("site-evidence", help="Fail-closed physical site attestation audit")
    a2.add_argument("--packets", required=True)
    a2.add_argument("--output", required=True)
    a3 = s.add_parser("legacy-inventory", help="Non-destructive versioned script migration inventory")
    a3.add_argument("--repository", default=str(Path(__file__).resolve().parents[1]))
    a3.add_argument("--output", required=True)
    a4 = s.add_parser("dataset-inventory", help="Bounded read-only scan of local dataset/prepared folders")
    a4.add_argument("--outputs-root", required=True)
    a4.add_argument("--output", required=True)
    a4.add_argument("--count-episodes", action="store_true")
    a5 = s.add_parser("carla-plan", help="Plan CARLA matched controls; does not execute")
    a5.add_argument("--clear-dir", required=True)
    a5.add_argument("--output-dir", required=True)
    a5.add_argument("--max-controls", type=int, default=6)
    a5.add_argument("--redirect-at-s", type=float, default=8)
    a5.add_argument("--host", default="127.0.0.1")
    a5.add_argument("--port", type=int, default=2000)
    a5.add_argument("--extended-controls", action="store_true",
                    help="also run clear_B and clear_A_redirect_B to isolate route vs blocker vs redirect")
    a7 = s.add_parser("observation-benchmark", help="Restricted one-observation synthetic comparison, NOT published shield")
    a7.add_argument("--output", required=True)
    a7.add_argument("--seed", type=int, default=13)
    a7.add_argument("--replicates", type=int, default=500)
    a7.add_argument("--horizon", type=int, default=100)
    a7.add_argument("--options", type=int, default=5)
    a7.add_argument("--worlds", type=int, default=4)
    ba = s.add_parser("baseline-audit", help="Marginal/separated vs joint safe actions vs independent exact grid; NO published shield reproduction")
    ba.add_argument("--cases", required=True)
    ba.add_argument("--output", required=True)
    rg = s.add_parser("review-gates", help="Verify current compact review bundle checksums and fail-closed H1-H5 status")
    rg.add_argument("--review-dir", required=True)
    rg.add_argument("--output", required=True)
    a6 = s.add_parser("review-pack", help="Create small, checksum-audited evidence ZIP")
    a6.add_argument("--study", required=True)
    a6.add_argument("--output", required=True)
    c = s.add_parser("temporal-check", help="Existing restricted interval vs independent grid; not a published shield")
    c.add_argument("--cases", required=True)
    c.add_argument("--output", required=True)
    args = p.parse_args(argv)
    if args.command == "carla-triplets":
        result = classify_carla_triplet(Path(args.study))
        _atomic_json(Path(args.output), result)
    elif args.command == "carla-fiveway":
        from capplan.handoff.carla_experiments import audit
        result = audit(Path(args.study))
        _atomic_json(Path(args.output), result)
    elif args.command == "directed-remine":
        result = run_graph_screen(Path(args.dataset_dir), Path(args.pairs), Path(args.output),
            per_city=args.per_city, times=tuple(float(x) for x in args.times.split(',')),
            speed_mps=args.speed_mps, ack_s=args.ack_s, third_options=args.third_options)
    elif args.command == "site-evidence":
        result = site_attestation_audit(Path(args.packets))
        _atomic_json(Path(args.output), result)
    elif args.command == "legacy-inventory":
        result = legacy_research_inventory(Path(args.repository))
        _atomic_json(Path(args.output), result)
    elif args.command == "dataset-inventory":
        from scripts.inspect_capplan_outputs_v13 import safe_inventory
        result=safe_inventory(args.outputs_root,count_episodes=args.count_episodes)
        _atomic_json(Path(args.output),result)
        result={"directories_scanned":len(result["directories"]),
                "truncated_directories":len(result["warnings"]),
                "not_a_full_dataset_semantic_audit":True}
    elif args.command == "carla-plan":
        from scripts.plan_carla_matched_v13 import construct
        result=construct(args.clear_dir,args.output_dir,max_controls=args.max_controls,
                         port=args.port,host=args.host,redirect_s=args.redirect_at_s,
                         extended_controls=args.extended_controls)
        result={"controls_selected":result["controls_selected"],
                "jobs_planned":result["jobs_planned"],
                "script":result["run_script"],"executed":False}
    elif args.command == "observation-benchmark":
        from scripts.benchmark_observation_v13 import run
        if min(args.replicates,args.horizon,args.options,args.worlds)<1:
            p.error("replicates, horizon, options, worlds must be positive")
        result=run(args.seed,args.replicates,args.horizon,args.options,args.worlds)
        _atomic_json(Path(args.output),result)
    elif args.command == "baseline-audit":
        from capplan.handoff.baselines import analyze_files
        result = analyze_files(Path(args.cases), Path(args.output))
    elif args.command == "review-gates":
        from capplan.handoff.review_gates import review
        result = review(Path(args.review_dir))
        _atomic_json(Path(args.output),result)
    elif args.command == "review-pack":
        from zipfile import ZipFile, ZIP_DEFLATED
        from capplan.handoff.research import _digest_file
        study=Path(args.study)
        output=Path(args.output)
        output.parent.mkdir(parents=True,exist_ok=True)
        names=("carla_triplets.json","directed_remine.summary.json",
               "site_evidence.json","temporal.summary.json","observation_benchmark.json",
               "legacy_inventory.json","outputs_inventory.json",
               "baseline_comparison.summary.json","research_gates.json", "carla_fiveway.json")
        manifest={"schema":"capplan_handoff_study_review_bundle",
                  "scope":"METADATA_ONLY_NO_RAW_GRAPH_OR_VEHICLE_TRAJECTORIES",
                  "files":{},"missing":[]}
        for name in names:
            candidate=study/name
            if not candidate.is_file():
                manifest["missing"].append(name)
                continue
            if candidate.stat().st_size>3_000_000:
                manifest["missing"].append(name+":FILE_TOO_LARGE")
                continue
            manifest["files"][name]={"sha256":_digest_file(candidate),
                                      "bytes":candidate.stat().st_size}
        with ZipFile(output,"w",compression=ZIP_DEFLATED) as z:
            for name in sorted(manifest["files"]):z.write(study/name,arcname=name)
            z.writestr("bundle_manifest.json",json.dumps(manifest,ensure_ascii=False,indent=2))
        result={"output":str(output),"included":list(manifest["files"]),
                "missing":manifest["missing"],"no_raw_scene_data_included":True}
    else:
        from capplan.handoff.temporal_recourse_v12 import analyze
        from capplan.handoff.semantic_oracle_v13 import evaluate
        from capplan.handoff.research import _read_jsonl
        out=Path(args.output);out.parent.mkdir(parents=True,exist_ok=True)
        n=0;counts={};errors=[]
        with out.open('w',encoding='utf-8') as f:
            for case in _read_jsonl(Path(args.cases)):
                a=analyze(case);b=evaluate(case);n+=1
                x={'case_id':case.get('case_id'),'interval_status':a['status'],
                   'independent_grid_status':b['status'],'match':a['status']==b['status'],
                   'scope':'RESTRICTED_ONE_OBSERVATION_POLICY_CLASS'}
                f.write(json.dumps(x,ensure_ascii=False)+'\n')
                counts[a['status']]=counts.get(a['status'],0)+1
                if not x['match']: errors.append(x)
        result={'n':n,'counts':counts,'mismatches':errors[:20],'mismatch_count':len(errors),
                'published_pomdp_shield_reproduced':False,
                'no_claims_outside_restricted_policy_class':True}
        _atomic_json(out.with_suffix('.summary.json'),result)
    print(json.dumps(result,ensure_ascii=False))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
