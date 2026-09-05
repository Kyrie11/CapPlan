#!/usr/bin/env python
"""Paired, episode-clustered search-efficiency comparison for CapPlan variants."""
from __future__ import annotations
import argparse, json
from pathlib import Path
import numpy as np


def rows(path):
    return [json.loads(x) for x in Path(path).read_text(encoding='utf-8').splitlines() if x.strip()]


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--reference', required=True, help='reference variant directory')
    ap.add_argument('--candidate', required=True, help='candidate variant directory')
    ap.add_argument('--output', required=True)
    ap.add_argument('--bootstrap', type=int, default=10000)
    ap.add_argument('--seed', type=int, default=13)
    args=ap.parse_args()
    ref={ (r['episode_id'],r['passenger_id']):r for r in rows(Path(args.reference)/'episode_metrics.jsonl') }
    cand={ (r['episode_id'],r['passenger_id']):r for r in rows(Path(args.candidate)/'episode_metrics.jsonl') }
    keys=sorted(set(ref)&set(cand))
    if not keys: raise RuntimeError('no paired requests')
    by_ep={}
    by_ep_lat={}
    d_exp=[]; d_lat=[]; decision_mismatch=0; expansion_mismatch=0
    for k in keys:
        a,b=ref[k],cand[k]
        de=float(a.get('search_expansions',0))-float(b.get('search_expansions',0))
        dl=float(a.get('planning_latency_ms',0))-float(b.get('planning_latency_ms',0))
        d_exp.append(de); d_lat.append(dl); by_ep.setdefault(k[0],[]).append(de); by_ep_lat.setdefault(k[0],[]).append(dl)
        decision_mismatch += int(bool(a.get('passenger_complete')) != bool(b.get('passenger_complete')))
        expansion_mismatch += int(abs(de) > 1e-12)
    ep=np.array([np.mean(v) for v in by_ep.values()], dtype=float)
    ep_lat=np.array([np.mean(by_ep_lat[e]) for e in sorted(by_ep_lat)], dtype=float)
    rng=np.random.default_rng(args.seed)
    boot=np.array([rng.choice(ep, size=len(ep), replace=True).mean() for _ in range(args.bootstrap)])
    boot_lat=np.array([rng.choice(ep_lat, size=len(ep_lat), replace=True).mean() for _ in range(args.bootstrap)])
    ref_exp=np.mean([float(ref[k].get('search_expansions',0)) for k in keys])
    cand_exp=np.mean([float(cand[k].get('search_expansions',0)) for k in keys])
    result={
        'paired_requests':len(keys),'paired_episodes':len(by_ep),
        'decision_mismatch_count':decision_mismatch,
        'expansion_mismatch_count':expansion_mismatch,
        'reference_expansions_mean':float(ref_exp),'candidate_expansions_mean':float(cand_exp),
        'expansion_reduction_fraction':float(1.0-cand_exp/max(ref_exp,1e-9)),
        'paired_expansion_delta_reference_minus_candidate_mean':float(np.mean(d_exp)),
        'paired_expansion_delta_ci95_episode_clustered':[float(np.quantile(boot,.025)),float(np.quantile(boot,.975))],
        'request_win_rate':float(np.mean(np.asarray(d_exp)>0)),
        'request_tie_rate':float(np.mean(np.asarray(d_exp)==0)),
        'latency_delta_reference_minus_candidate_mean_ms':float(np.mean(d_lat)),
        'paired_latency_delta_ci95_episode_clustered_ms':[float(np.quantile(boot_lat,.025)),float(np.quantile(boot_lat,.975))],
    }
    Path(args.output).write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))
if __name__=='__main__': main()
