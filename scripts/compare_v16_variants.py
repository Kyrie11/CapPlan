#!/usr/bin/env python
"""Paired request/episode comparison for exact-equivalent V16 kernel compilation."""
from __future__ import annotations
import argparse,json
from pathlib import Path
import numpy as np

def rows(d): return [json.loads(x) for x in (Path(d)/'episode_metrics.jsonl').read_text().splitlines() if x.strip()]
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--reference',required=True); ap.add_argument('--candidate',required=True); ap.add_argument('--output',required=True); ap.add_argument('--bootstrap',type=int,default=10000); ap.add_argument('--seed',type=int,default=13); a=ap.parse_args()
    r={(x['episode_id'],x['passenger_id']):x for x in rows(a.reference)}; c={(x['episode_id'],x['passenger_id']):x for x in rows(a.candidate)}; keys=sorted(set(r)&set(c))
    if not keys: raise RuntimeError('no paired requests')
    metrics={'expansion':'search_expansions','cpk_build_ms':'precondition_build_ms','primary_latency_ms':'primary_decision_latency_ms','end_to_end_latency_ms':'planning_latency_ms'}
    rng=np.random.default_rng(a.seed); out={'paired_requests':len(keys),'paired_episodes':len(set(k[0] for k in keys)),'decision_mismatch_count':sum(bool(r[k].get('passenger_complete'))!=bool(c[k].get('passenger_complete')) for k in keys)}
    for name,key in metrics.items():
        raw=[]; by={}
        for k in keys:
            d=float(r[k].get(key,0) or 0)-float(c[k].get(key,0) or 0); raw.append(d); by.setdefault(k[0],[]).append(d)
        eps=np.asarray([np.mean(v) for v in by.values()],float); boot=np.asarray([rng.choice(eps,size=len(eps),replace=True).mean() for _ in range(a.bootstrap)])
        out[f'{name}_delta_reference_minus_candidate_mean']=float(np.mean(raw)); out[f'{name}_delta_ci95_episode_clustered']=[float(np.quantile(boot,.025)),float(np.quantile(boot,.975))]
        out[f'reference_{name}_mean']=float(np.mean([float(r[k].get(key,0) or 0) for k in keys])); out[f'candidate_{name}_mean']=float(np.mean([float(c[k].get(key,0) or 0) for k in keys]))
    # Exact-reuse mechanism telemetry from candidate rows.
    for key in ['parametric_kernel_cache_hit','parametric_kernel_cache_miss','parametric_kernel_thresholds_erased','parametric_kernel_lookup_ms','parametric_kernel_source_build_ms']:
        out[f'candidate_{key}_mean']=float(np.mean([float(c[k].get(key,0) or 0) for k in keys]))
    shape_by_ep={}
    for k in keys:
        sid=str(c[k].get('parametric_kernel_shape_id','') or '')
        if sid: shape_by_ep.setdefault(k[0],set()).add(sid)
    out['candidate_shape_count_per_episode_mean']=float(np.mean([len(v) for v in shape_by_ep.values()])) if shape_by_ep else 0.0
    Path(a.output).write_text(json.dumps(out,indent=2)); print(json.dumps(out,indent=2))
if __name__=='__main__': main()
