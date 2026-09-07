#!/usr/bin/env python
from __future__ import annotations
import argparse,json
from pathlib import Path
import numpy as np

def rows(d): return [json.loads(x) for x in (Path(d)/'episode_metrics.jsonl').read_text().splitlines() if x.strip()]
def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--reference',nargs='+',required=True); ap.add_argument('--candidate',nargs='+',required=True); ap.add_argument('--output',required=True); ap.add_argument('--bootstrap',type=int,default=10000); ap.add_argument('--seed',type=int,default=13); a=ap.parse_args()
    if len(a.reference)!=len(a.candidate): raise ValueError('repeat counts differ')
    repeat=[]; all_by={}; mismatch=0; exp_mismatch=0
    for i,(rd,cd) in enumerate(zip(a.reference,a.candidate)):
        r={(x['episode_id'],x['passenger_id']):x for x in rows(rd)}; c={(x['episode_id'],x['passenger_id']):x for x in rows(cd)}; keys=sorted(set(r)&set(c))
        ds=[]; bs=[]; mm=0; em=0
        for k in keys:
            mm+=int(bool(r[k].get('passenger_complete'))!=bool(c[k].get('passenger_complete')))
            em+=int(int(r[k].get('search_expansions',0) or 0)!=int(c[k].get('search_expansions',0) or 0))
            d=float(r[k].get('primary_decision_latency_ms',0) or 0)-float(c[k].get('primary_decision_latency_ms',0) or 0); ds.append(d); all_by.setdefault(k[0],[]).append(d)
            bs.append(float(r[k].get('precondition_build_ms',0) or 0)-float(c[k].get('precondition_build_ms',0) or 0))
        mismatch=max(mismatch,mm); exp_mismatch=max(exp_mismatch,em)
        repeat.append({'repeat_index':i,'latency_delta_reference_minus_candidate_mean_ms':float(np.mean(ds)),'cpk_build_delta_reference_minus_candidate_mean_ms':float(np.mean(bs)),'decision_mismatch_count':mm,'expansion_mismatch_count':em})
    eps=np.asarray([np.mean(v) for v in all_by.values()],float); rng=np.random.default_rng(a.seed); boot=np.asarray([rng.choice(eps,size=len(eps),replace=True).mean() for _ in range(a.bootstrap)])
    out={'repeats':len(repeat),'paired_episodes':len(eps),'decision_mismatch_count_max_over_repeats':mismatch,'expansion_mismatch_count_max_over_repeats':exp_mismatch,'latency_delta_reference_minus_candidate_mean_ms':float(eps.mean()),'paired_latency_delta_ci95_episode_clustered_ms':[float(np.quantile(boot,.025)),float(np.quantile(boot,.975))],'repeat_stats':repeat}
    Path(a.output).write_text(json.dumps(out,indent=2)); print(json.dumps(out,indent=2))
if __name__=='__main__': main()
