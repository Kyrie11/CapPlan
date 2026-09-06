#!/usr/bin/env python
"""Paired episode-clustered comparison for V15 proposal/necessity controls."""
from __future__ import annotations
import argparse,json
from pathlib import Path
import numpy as np

def rows(d): return [json.loads(x) for x in (Path(d)/'episode_metrics.jsonl').read_text().splitlines() if x.strip()]

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--reference',required=True); ap.add_argument('--candidate',required=True); ap.add_argument('--output',required=True); ap.add_argument('--bootstrap',type=int,default=10000); ap.add_argument('--seed',type=int,default=13); a=ap.parse_args()
    r={(x['episode_id'],x['passenger_id']):x for x in rows(a.reference)}; c={(x['episode_id'],x['passenger_id']):x for x in rows(a.candidate)}; keys=sorted(set(r)&set(c))
    if not keys: raise RuntimeError('no paired requests')
    metrics={'expansion':'search_expansions','primary_latency_ms':'primary_decision_latency_ms','end_to_end_latency_ms':'planning_latency_ms'}
    by={name:{} for name in metrics}; raw={name:[] for name in metrics}; mismatch=0
    for k in keys:
        mismatch+=int(bool(r[k].get('passenger_complete'))!=bool(c[k].get('passenger_complete')))
        for name,key in metrics.items():
            d=float(r[k].get(key,0) or 0)-float(c[k].get(key,0) or 0); raw[name].append(d); by[name].setdefault(k[0],[]).append(d)
    rng=np.random.default_rng(a.seed); out={'paired_requests':len(keys),'paired_episodes':len(set(k[0] for k in keys)),'decision_mismatch_count':mismatch}
    for name,key in metrics.items():
        eps=np.asarray([np.mean(v) for v in by[name].values()],float); boot=np.asarray([rng.choice(eps,size=len(eps),replace=True).mean() for _ in range(a.bootstrap)])
        out[f'{name}_delta_reference_minus_candidate_mean']=float(np.mean(raw[name])); out[f'{name}_delta_ci95_episode_clustered']=[float(np.quantile(boot,.025)),float(np.quantile(boot,.975))]
        out[f'reference_{name}_mean']=float(np.mean([float(r[k].get(key,0) or 0) for k in keys])); out[f'candidate_{name}_mean']=float(np.mean([float(c[k].get(key,0) or 0) for k in keys]))
    # Diagnostic-only mechanism localization.  This never enters the GO gate,
    # but shows whether a capability-conditioned router helps particular
    # counterfactual capability axes rather than only aggregate easy cases.
    axes=sorted({str(c[k].get('counterfactual_axis') or 'base') for k in keys})
    by_axis={}
    for axis in axes:
        kk=[k for k in keys if str(c[k].get('counterfactual_axis') or 'base')==axis]
        if not kk: continue
        by_axis[axis]={
            'requests':len(kk),
            'expansion_delta_reference_minus_candidate_mean':float(np.mean([float(r[k].get('search_expansions',0) or 0)-float(c[k].get('search_expansions',0) or 0) for k in kk])),
            'primary_latency_delta_reference_minus_candidate_mean_ms':float(np.mean([float(r[k].get('primary_decision_latency_ms',0) or 0)-float(c[k].get('primary_decision_latency_ms',0) or 0) for k in kk])),
        }
    out['by_counterfactual_axis']=by_axis
    Path(a.output).write_text(json.dumps(out,indent=2)); print(json.dumps(out,indent=2))
if __name__=='__main__': main()
