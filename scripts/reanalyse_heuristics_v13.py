#!/usr/bin/env python3
"""Cluster-aware postanalysis of the supplied V12 *synthetic* benchmark.

Metrics conditioned on exact WIN and exact-known safe action set. Groups stress
variants by original parent case. Never treat the 168 variants as IID scenes.
"""
from __future__ import annotations
import argparse,json,math,random,statistics
from collections import Counter,defaultdict
from pathlib import Path


def quantile(xs,q):
    if not xs:return None
    ys=sorted(xs);i=math.ceil(q*len(ys))-1;return ys[max(0,min(i,len(ys)-1))]


def report(rows,n_boot=1000,seed=20261020):
    if not rows:raise ValueError('empty rows')
    groups=defaultdict(list);families=defaultdict(list)
    names=sorted({name for r in rows for name in r.get('baseline',{})})
    for r in rows:
        key=str(r['case_id']).split('::')[0];groups[key].append(r);families[r['family']].append(r)
    wins=[r for r in rows if r.get('oracle_status')=='WIN' and r.get('oracle_exact') is True]
    unknown=[r['case_id'] for r in rows if not r.get('oracle_exact')]
    methods={};rng=random.Random(seed);keys=sorted(groups)
    for name in names:
        wr=[r for r in wins if name in r['baseline']]
        err=sum(r['baseline'][name].get('certified_safe_first_action') is not True for r in wr)
        ms=[r['baseline'][name].get('planning_ms',0) for r in rows if name in r['baseline']]
        effort=[r['baseline'][name].get('evaluated_model_successors',0) for r in rows if name in r['baseline']]
        bootstrap=[]
        for _ in range(n_boot):
            selected=[r for k in (rng.choice(keys) for __ in keys) for r in groups[k]
                      if r.get('oracle_status')=='WIN' and r.get('oracle_exact') and name in r.get('baseline',{})]
            if selected:
                bootstrap.append(sum(r['baseline'][name]['certified_safe_first_action'] is not True for r in selected)/len(selected))
        methods[name]={'win_cases':len(wr),'unsafe_actions':err,'unsafe_rate':err/len(wr) if wr else None,
          'bootstrap_95_cluster_ci': [quantile(bootstrap,.025),quantile(bootstrap,.975)] if bootstrap else None,
          'planning_ms_median':statistics.median(ms) if ms else None,'planning_ms_p95':quantile(ms,.95),
          'successor_evals_median':statistics.median(effort) if effort else None,'successor_evals_p95':quantile(effort,.95)}
    return {'model':'FINITE_SYNTHETIC_CONTROLLED_V12','rows':len(rows),'independent_parent_cases':len(groups),
            'complete_oracle_win':len(wins),'nonexact_case_ids':unknown,'baselines':methods,
            'family_counts':{k:len(v) for k,v in families.items()},
            'limitations':['CI clusters by parent synthetic case; not a real-city population interval',
              'first action only, not end-to-end safe policy','lookup oracle shares simulator semantics',
              'costs are local same-machine measurements and sensitive to hardware']}

def main():
    p=argparse.ArgumentParser();p.add_argument('--inputs',nargs='+',required=True)
    p.add_argument('--output',required=True);p.add_argument('--bootstrap',type=int,default=1000)
    args=p.parse_args()
    rows=[]
    for input in args.inputs:
        rows.extend(json.loads(line) for line in Path(input).read_text().splitlines() if line.strip())
    out=Path(args.output);out.parent.mkdir(parents=True,exist_ok=True)
    s=report(rows,args.bootstrap);out.write_text(json.dumps(s,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps({'rows':s['rows'],'parents':s['independent_parent_cases'],'oracle_wins':s['complete_oracle_win']},ensure_ascii=False))
if __name__=='__main__':main()
