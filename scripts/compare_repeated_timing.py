#!/usr/bin/env python
"""Repeated paired timing comparison with episode-cluster bootstrap.

Each --reference directory is paired with the --candidate directory at the same
position. Per-request latency is averaged across repeats first, so the final
confidence interval is not dominated by treating repeated executions of the same
request as independent samples. This is used by V12 for micro-latency claims
whose sign changed between the V10 and V11 one-pass experiments.
"""
from __future__ import annotations
import argparse, json
from pathlib import Path
from typing import Dict, Tuple
import numpy as np

Key = Tuple[str, str]

def _rows(d: str):
    p=Path(d)/'episode_metrics.jsonl'
    return [json.loads(x) for x in p.read_text(encoding='utf-8').splitlines() if x.strip()]

def _map(d: str) -> Dict[Key, dict]:
    return {(str(r['episode_id']),str(r['passenger_id'])):r for r in _rows(d)}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--reference', nargs='+', required=True)
    ap.add_argument('--candidate', nargs='+', required=True)
    ap.add_argument('--output', required=True)
    ap.add_argument('--bootstrap', type=int, default=10000)
    ap.add_argument('--seed', type=int, default=13)
    a=ap.parse_args()
    if len(a.reference)!=len(a.candidate):
        raise RuntimeError('reference/candidate repeat counts differ')
    refs=[_map(x) for x in a.reference]; cands=[_map(x) for x in a.candidate]
    common=set(refs[0]) & set(cands[0])
    for m in refs[1:]+cands[1:]: common &= set(m)
    keys=sorted(common)
    if not keys: raise RuntimeError('no requests common to all repeats')
    per_req_lat={}; per_req_exp={}; per_req_ref_lat={}; per_req_cand_lat={}; decision_mismatch=0; expansion_mismatch=0
    repeat_stats=[]
    for i,(r,c) in enumerate(zip(refs,cands)):
        dlat=[]; dexp=[]; dm=0; em=0
        for k in keys:
            rr,cc=r[k],c[k]
            dl=float(rr.get('planning_latency_ms',0))-float(cc.get('planning_latency_ms',0))
            de=float(rr.get('search_expansions',0))-float(cc.get('search_expansions',0))
            per_req_lat.setdefault(k,[]).append(dl); per_req_exp.setdefault(k,[]).append(de)
            per_req_ref_lat.setdefault(k,[]).append(float(rr.get('planning_latency_ms',0)))
            per_req_cand_lat.setdefault(k,[]).append(float(cc.get('planning_latency_ms',0)))
            dm += int(bool(rr.get('passenger_complete'))!=bool(cc.get('passenger_complete')))
            em += int(abs(de)>1e-12)
            dlat.append(dl); dexp.append(de)
        decision_mismatch=max(decision_mismatch,dm); expansion_mismatch=max(expansion_mismatch,em)
        repeat_stats.append({
            'repeat_index':i,
            'latency_delta_reference_minus_candidate_mean_ms':float(np.mean(dlat)),
            'expansion_delta_reference_minus_candidate_mean':float(np.mean(dexp)),
            'decision_mismatch_count':dm,
            'expansion_mismatch_count':em,
        })
    req_lat={k:float(np.mean(v)) for k,v in per_req_lat.items()}
    req_exp={k:float(np.mean(v)) for k,v in per_req_exp.items()}
    req_ref_lat={k:float(np.mean(v)) for k,v in per_req_ref_lat.items()}
    req_cand_lat={k:float(np.mean(v)) for k,v in per_req_cand_lat.items()}
    episodes=sorted({k[0] for k in keys})
    ep_lat=np.asarray([np.mean([req_lat[k] for k in keys if k[0]==e]) for e in episodes],dtype=float)
    ep_exp=np.asarray([np.mean([req_exp[k] for k in keys if k[0]==e]) for e in episodes],dtype=float)
    rng=np.random.default_rng(a.seed)
    boot_lat=np.asarray([rng.choice(ep_lat,size=len(ep_lat),replace=True).mean() for _ in range(a.bootstrap)])
    boot_exp=np.asarray([rng.choice(ep_exp,size=len(ep_exp),replace=True).mean() for _ in range(a.bootstrap)])
    result={
        'repeats':len(refs),'paired_requests':len(keys),'paired_episodes':len(episodes),
        'decision_mismatch_count_max_over_repeats':decision_mismatch,
        'expansion_mismatch_count_max_over_repeats':expansion_mismatch,
        'reference_latency_mean_ms':float(np.mean(list(req_ref_lat.values()))),
        'candidate_latency_mean_ms':float(np.mean(list(req_cand_lat.values()))),
        'latency_delta_reference_minus_candidate_mean_ms':float(np.mean(list(req_lat.values()))),
        'paired_latency_delta_ci95_episode_clustered_ms':[float(np.quantile(boot_lat,.025)),float(np.quantile(boot_lat,.975))],
        'expansion_delta_reference_minus_candidate_mean':float(np.mean(list(req_exp.values()))),
        'paired_expansion_delta_ci95_episode_clustered':[float(np.quantile(boot_exp,.025)),float(np.quantile(boot_exp,.975))],
        'repeat_stats':repeat_stats,
    }
    Path(a.output).write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))
if __name__=='__main__': main()
