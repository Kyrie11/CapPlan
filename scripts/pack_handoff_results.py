#!/usr/bin/env python3
"""Produce an auditable bounded results bundle, never package nuPlan raw databases.

The bundle retains per-request examples (deterministically sampled), exact
summary reports, provenance notes, and manifest SHA256. The sampled JSONL
MUST NOT be used for prevalence claims; use full denominators in audits.
"""
from __future__ import annotations
import argparse,hashlib,json,zipfile
from pathlib import Path

FILES=[
 'mining/mining_audit.json','mining/recourse_pairs.jsonl',
 'audit/funnel_report.json','audit/human_review_queue.csv',
 'motion_requests.jsonl','motion_requests.manifest.json',
 'temporal_overlays.jsonl','policies/policy_summary.json',
 'policies/policy_rollouts.jsonl','policies/site_cluster_ci.json',
 'canonical_profiles.manifest.json','profile_sweep/profile_sweep.json',
 'profile_sweep/profile_witnesses.jsonl','research_gate.json',
 'sensitivity/sensitivity_report.json',
]

def pack(root,target,examples=200,include_full_pairs=False):
    root=Path(root).resolve();target=Path(target).resolve()
    if not root.is_dir():raise FileNotFoundError(root)
    if target.is_relative_to(root):raise ValueError('write archive OUTSIDE result directory')
    target.parent.mkdir(parents=True,exist_ok=True)
    manifest={'provenance':'generated output bundle, not independent ground truth',
      'denominators':'read original mining/mining_audit.json; bounded examples not a denominator',
      'sampling':'first N rows, deterministic illustrative selection, not statistical sampling',
      'example_limit':examples,'files':{}}
    with zipfile.ZipFile(target,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for name in FILES:
            p=root/name
            if not p.exists():continue
            if name.endswith('.jsonl') and not include_full_pairs:
                with p.open('rb') as f:
                    contents=b''.join(line for _,line in zip(range(examples),f))
            else:contents=p.read_bytes()
            z.writestr(name,contents)
            manifest['files'][name]={
                'bundle_sha256':hashlib.sha256(contents).hexdigest(),
                'source_bytes':p.stat().st_size,
                'included_bytes':len(contents),
                'truncated':len(contents)!=p.stat().st_size}
        # Each controlled contrast is evidence of simulator behavior only.
        for p in sorted((root/'sensitivity').glob('*/policies/policy_summary.json')):
            name=str(p.relative_to(root));contents=p.read_bytes()
            z.writestr(name,contents)
            manifest['files'][name]={'bundle_sha256':hashlib.sha256(contents).hexdigest(),
              'source_bytes':len(contents),'included_bytes':len(contents),'truncated':False}
        z.writestr('BUNDLE_MANIFEST.json',json.dumps(manifest,indent=2,ensure_ascii=False)+'\n')
    return manifest

if __name__=='__main__':
    a=argparse.ArgumentParser()
    a.add_argument('--results_dir',required=True);a.add_argument('--output',required=True)
    a.add_argument('--examples',type=int,default=200);a.add_argument('--full_jsonl',action='store_true')
    x=a.parse_args()
    if x.examples<0:a.error('--examples must be nonnegative')
    m=pack(x.results_dir,x.output,x.examples,x.full_jsonl)
    print(json.dumps({'included_files':len(m['files']),'output':x.output},ensure_ascii=False))
