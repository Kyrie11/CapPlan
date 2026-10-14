#!/usr/bin/env python3
"""Geometric falsification of the 'must go to A before redirecting' assumption.

Passenger position is interpolated along the stored O->A pedestrian path.
From the interpolation point they may walk to either edge endpoint and then
follow the *same graph feasibility rules* to B. This does not establish a
real-time vehicle arrival, real-world accessibility, or complete recourse.
"""
from __future__ import annotations
import argparse,json,math,sys
from collections import Counter,defaultdict
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from capplan.data.accessibility_layer import load_accessibility_graph
from capplan.handoff.gate_evidence import records,site_key,city,dump_json
from scripts.mine_handoff_recourse_pairs import _valid_edge,_shortest_tree


def evaluate_pair(pair,graph,notice_s,speed_mps=1.,ack_s=3.):
    path=pair['paths']['origin_to_a'];nodes=path['node_ids'];edges=path['edges']
    if len(nodes)<2 or len(edges)!=len(nodes)-1:raise ValueError('invalid stored path')
    d_a=float(pair['distances_m']['origin_to_a']);d_b=float(pair['distances_m']['origin_to_b'])
    lengths=[float(e['length_m']) for e in edges]
    if abs(sum(lengths)-d_a)>1e-3:raise ValueError('path and pair distance disagree')
    cont=pair['screening_contract'];a=SimpleNamespace(
        require_step_free=bool(cont.get('require_step_free')),
        min_width_m=float(cont['min_width_m']),max_slope=float(cont['max_slope']),
        max_cross_slope=float(cont['max_cross_slope']),min_confidence=float(cont.get('min_confidence',0)))
    adj=defaultdict(list)
    for e in graph.edges:
        if _valid_edge(e,a):
            adj[e.from_node].append((e.to_node,e));adj[e.to_node].append((e.from_node,e))
    b=pair['pedestrian_graph_nodes']['B']
    to_b,_=_shortest_tree(adj,b)
    progress=min(d_a,max(0.,(float(notice_s)-ack_s)*speed_mps))
    used=float(cont.get('already_consumed_m',0))+progress
    if progress>=d_a-1e-8:
        remaining=to_b.get(nodes[-1],float('inf'))
        segment='ARRIVED_A'
    else:
        cum=0.;remaining=float('inf');segment='UNKNOWN'
        for i,ln in enumerate(lengths):
            if progress<=cum+ln+1e-8:
                from_u=max(0.,progress-cum)
                from_v=max(0.,ln-from_u)
                remaining=min(from_u+to_b.get(nodes[i],float('inf')),
                              from_v+to_b.get(nodes[i+1],float('inf')))
                segment=f'{nodes[i]} -> {nodes[i+1]}'
                break
            cum+=ln
    total=used+remaining;budget=float(cont['distance_budget_m'])
    return {'episode_id':pair['episode_id'],'a_anchor':pair['a_anchor'],'b_anchor':pair['b_anchor'],
            'city':city(pair),'physical_site_pair':site_key(pair),
            'notice_s':notice_s,'progress_m':progress,'segment':segment,
            'distance_consumed_at_notice_m':used,'remaining_graph_distance_to_B_m':remaining,
            'best_possible_total_distance_to_B_m':total,'distance_budget_m':budget,
            'can_redirect_to_B_in_graph':bool(total<=budget+1e-8),
            'cannot_recover_via_A_to_B':bool(float(pair['reroute_total_m'])>budget+1e-8),
            'scope':'graph-only from interpolated segment; no timing / vehicle / empirical walking evidence'}


def run(dataset_dir,pairs,out,notice_times,speed_mps=1.,ack_s=3.,limit=60):
    root=Path(dataset_dir);target=Path(out);target.parent.mkdir(parents=True,exist_ok=True)
    cache={};counts=Counter();rows=[];failures=[]
    for p in records(pairs):
        if counts['pairs_attempted']>=limit:break
        counts['pairs_attempted']+=1
        try:
            ep=p['episode_id']
            if ep not in cache:cache[ep]=load_accessibility_graph(root,ep)
            for t in notice_times:
                r=evaluate_pair(p,cache[ep],t,speed_mps,ack_s)
                rows.append(r)
                counts['graph_possible_'+str(t)]+=int(r['can_redirect_to_B_in_graph'])
            counts['pairs_processed']+=1
        except (KeyError,ValueError,TypeError,FileNotFoundError,OverflowError) as e:
            failures.append({'episode_id':p.get('episode_id'),'error':str(e)})
    target.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
    report={'counts':dict(counts),'errors':failures[:30], 'notice_times_s':notice_times,
            'denominator':'preselected pair rows; times reused on identical geometry, not independent cases',
            'research_boundary':'If early mid-route retarget succeeds, A->B failure is not global irreversibility.\n'
             'If it fails, alternative PUDO, return to O, rest, vehicle move and assistance remain untested.',
            'natural_incidence_supported':False}
    dump_json(target.with_suffix('.summary.json'),report)
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--dataset_dir',required=True)
    p.add_argument('--pairs',required=True);p.add_argument('--output',required=True)
    p.add_argument('--notice_times_s',default='0,30,60,90,120,180')
    p.add_argument('--speed_mps',type=float,default=1.0);p.add_argument('--ack_s',type=float,default=3.0)
    p.add_argument('--limit',type=int,default=60)
    a=p.parse_args();ts=[float(x) for x in a.notice_times_s.split(',')]
    if not ts or any(not math.isfinite(x) or x<0 for x in ts) or a.speed_mps<=0 or a.ack_s<0:
        p.error('invalid times/speed/ack')
    print(json.dumps(run(a.dataset_dir,a.pairs,a.output,ts,a.speed_mps,a.ack_s,a.limit)['counts']))
