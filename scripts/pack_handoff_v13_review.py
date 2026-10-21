#!/usr/bin/env python3
"""Package a small V13 research review. NEVER include heavy nuPlan graph/db files."""
from __future__ import annotations
import argparse,hashlib,json,zipfile
from pathlib import Path

ALLOW=[
 'evidence_lineage_v13.json','outputs_inventory_v13.json',
 'oracle_results_v13.jsonl','oracle_results_v13.summary.json',
 'observation_benchmark_v13.json','midroute_directed_v13.summary.json',
 'carla_v13/paired_plan_manifest_v13.json','carla_v13/paired_gate_v13.json',
 'study_notes_v13.json'
]

def package(study,out,max_file_mb=20):
    study=Path(study);out=Path(out);out.parent.mkdir(parents=True,exist_ok=True)
    if not study.is_dir():raise ValueError('study directory missing')
    selected=[];missing=[]
    for rel in ALLOW:
        p=study/rel
        if p.is_symlink() or not p.is_file():missing.append(rel);continue
        if p.stat().st_size>max_file_mb*1024*1024:raise ValueError(f'too large to include: {rel}')
        raw=p.read_bytes();selected.append((rel,raw))
    manifest={'schema':'capplan_v13_minimal_research_review','selected':[{'file':r,'bytes':len(x),'sha256':hashlib.sha256(x).hexdigest()} for r,x in selected],
              'not_included':missing,'no_heavy_database_or_trajectory_export':True,
              'notes':'Physical attestation and CARLA trajectories must be supplied separately when specifically needed.'}
    with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for rel,raw in selected:z.writestr(rel,raw)
        z.writestr('review_manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    return {'zip':str(out),'files':len(selected),'bytes':out.stat().st_size,'missing_optional':missing}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--study_dir',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();print(json.dumps(package(a.study_dir,a.output),ensure_ascii=False))
