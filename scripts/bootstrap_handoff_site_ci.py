#!/usr/bin/env python3
"""Bootstrap policy candidate success by connected physical-site components.

No independence assumed for repeated episodes or overlapping A/B sites.
Undefined if sites missing or too few independent components.  This is a
conditional model estimate, NOT an event-prevalence CI.
"""
from __future__ import annotations
import argparse,json,random,statistics
from pathlib import Path
from collections import defaultdict


def read_rows(path):
    for s in Path(path).read_text(encoding='utf-8').splitlines():
        if s.strip():yield json.loads(s)


def report(path,target,seed=13,repeats=1000,min_components=5):
    rows=list(read_rows(path))
    parent={}
    def root(x):
        parent.setdefault(x,x)
        while parent[x]!=x:
            parent[x]=parent[parent[x]]
            x=parent[x]
        return x
    def union(a,b):
        x,y=root(a),root(b)
        if x!=y:parent[y]=x
    excluded=[];usable=[]
    for r in rows:
        sites=r.get('physical_site_key')
        if not isinstance(sites,list) or len(sites)!=2 or not all(sites):
            excluded.append(r.get('overlay_id'));continue
        union(str(sites[0]),str(sites[1]));usable.append(r)
    grouped=defaultdict(list)
    for r in usable:
        grouped[root(str(r['physical_site_key'][0]))].append(r)
    units=list(grouped.values())
    summary={'site_components':len(units),'overlays_with_known_sites':len(usable),
             'excluded_unknown_site_overlays':len(excluded),
             'bootstrap_resamples':repeats,'seed':seed,
             'scope':'conditional controlled scenario results; not a real event incidence interval',
             'methods':{}}
    method_names=sorted(set(n for r in usable for n in r['results']))
    rng=random.Random(seed)
    # Preserve site-component cluster size weighting: sample components, then
    # take the per-overlay mean over all sampled members.
    for method in method_names:
        observations=[[row['results'][method]['weighted_candidate_success_rate']
                       for row in unit if method in row['results']] for unit in units]
        observations=[v for v in observations if v]
        flat=[v for cluster in observations for v in cluster]
        if len(observations)<min_components or len(flat)<min_components:
            summary['methods'][method]={'estimate':statistics.mean(flat) if flat else None,
                  'ci95':None,'reason':'insufficient independently clustered site components'}
            continue
        values=[]
        for _ in range(repeats):
            sampled=[observations[rng.randrange(len(observations))] for _ in observations]
            values.append(sum(map(sum,sampled))/sum(map(len,sampled)))
        values.sort()
        summary['methods'][method]={'estimate':statistics.mean(flat),
          'ci95':[values[int(.025*(len(values)-1))],values[int(.975*(len(values)-1))]],
          'independent_components':len(observations)}
    out=Path(target);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(summary,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    print(json.dumps({'site_components':summary['site_components'],'methods':len(method_names)}))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--rollouts',required=True);p.add_argument('--output',required=True)
    p.add_argument('--seed',type=int,default=13);p.add_argument('--resamples',type=int,default=1000)
    a=p.parse_args();report(a.rollouts,a.output,a.seed,a.resamples)
