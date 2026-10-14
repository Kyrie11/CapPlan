#!/usr/bin/env python3
"""Falsification of terminal-A-only recourse, on an existing pedestrian graph.

IMPORTANT: timestamps are seconds since issued guidance at t=0.  A reroute
instruction issued at notice_s becomes actionable at notice_s + ack_s; the
passenger keeps moving during acknowledgement unless --pause_during_ack.
These graph-only paths provide NECESSARY screening, not a complete PUDO handoff.
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


def _graph_distances(graph,cont, target):
    a=SimpleNamespace(require_step_free=bool(cont.get('require_step_free')),
        min_width_m=float(cont['min_width_m']),max_slope=float(cont['max_slope']),
        max_cross_slope=float(cont['max_cross_slope']),
        min_confidence=float(cont.get('min_confidence',0)))
    adj=defaultdict(list)
    for e in graph.edges:
        if _valid_edge(e,a):
            # Kept symmetric to match the original mining semantics.  Do NOT
            # generalize this to directional road or crossing edges.
            adj[e.from_node].append((e.to_node,e))
            adj[e.to_node].append((e.from_node,e))
    return _shortest_tree(adj,target)[0]


def evaluate_pair(pair,graph,notice_s,speed_mps=1.,ack_s=3.,pause_during_ack=False):
    if not all(math.isfinite(x) and x>=0 for x in (notice_s,ack_s,speed_mps)) or speed_mps==0:
        raise ValueError('invalid times or speed')
    path=pair['paths']['origin_to_a'];nodes=path['node_ids'];edges=path['edges']
    if len(nodes)<2 or len(edges)!=len(nodes)-1:raise ValueError('invalid stored path')
    d_a=float(pair['distances_m']['origin_to_a']);lengths=[float(e['length_m']) for e in edges]
    if abs(sum(lengths)-d_a)>1e-3:raise ValueError('path and pair distance disagree')
    cont=pair['screening_contract']
    b=pair['pedestrian_graph_nodes']['B']
    to_b=_graph_distances(graph,cont,b)
    # An acknowledged message cannot teleport the passenger to an earlier
    # position.  If walking continues, acknowledgement uses additional budget.
    action_s=notice_s+ack_s
    movement_s=notice_s if pause_during_ack else action_s
    progress=min(d_a,max(0.,movement_s*speed_mps))
    used=float(cont.get('already_consumed_m',0))+progress
    if progress>=d_a-1e-8:
        remaining=to_b.get(nodes[-1],float('inf'));segment='ARRIVED_A'
    else:
        cum=0.;remaining=float('inf');segment='UNKNOWN'
        for i,ln in enumerate(lengths):
            if progress<=cum+ln+1e-8:
                from_u=max(0.,progress-cum);from_v=max(0.,ln-from_u)
                remaining=min(from_u+to_b.get(nodes[i],float('inf')),
                              from_v+to_b.get(nodes[i+1],float('inf')))
                segment=f'{nodes[i]} -> {nodes[i+1]}'
                break
            cum+=ln
    total=used+remaining;budget=float(cont['distance_budget_m'])
    return {'episode_id':pair['episode_id'],'a_anchor':pair['a_anchor'],'b_anchor':pair['b_anchor'],
        'city':city(pair),'physical_site_pair':site_key(pair),
        'notice_issued_at_s':notice_s,'notice_actionable_at_s':action_s,
        'movement_assumption':'pauses_during_ack' if pause_during_ack else 'continues_until_ack',
        'speed_mps':speed_mps,'progress_m':progress,'segment':segment,
        'distance_consumed_at_ack_m':used,'remaining_graph_distance_to_B_m':remaining,
        'best_possible_total_distance_to_B_m':total,'distance_budget_m':budget,
        'can_redirect_to_B_in_graph':bool(total<=budget+1e-8),
        'cannot_recover_via_A_to_B':bool(float(pair['reroute_total_m'])>budget+1e-8),
        'scope':'pedestrian graph only; symmetric-edge approximation; no curb, vehicle, deadline or empirical capability validation'}


def run(dataset_dir,pairs,out,notice_times,speed_mps=1.,ack_s=3.,limit=60,pause_during_ack=False):
    root=Path(dataset_dir);target=Path(out);target.parent.mkdir(parents=True,exist_ok=True)
    cache={};counts=Counter();rows=[];failures=[]
    for p in records(pairs):
        if counts['pairs_attempted']>=limit:break
        counts['pairs_attempted']+=1
        try:
            ep=p['episode_id']
            if ep not in cache:cache[ep]=load_accessibility_graph(root,ep)
            for t in notice_times:
                r=evaluate_pair(p,cache[ep],t,speed_mps,ack_s,pause_during_ack)
                rows.append(r)
                counts['graph_possible_'+str(t)]+=int(r['can_redirect_to_B_in_graph'])
            counts['pairs_processed']+=1
        except (KeyError,ValueError,TypeError,FileNotFoundError,OverflowError) as e:
            failures.append({'episode_id':p.get('episode_id'),'error':str(e)})
    target.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
    report={'counts':dict(counts),'errors':failures[:30], 'notice_times_s':notice_times,
        'clock_semantics':'issued at t, actionable at t+ack; optional stop during acknowledgement',
        'denominator':'preselected pair rows; notice times on repeated geometry, not independent events',
        'research_boundary':'Graph-only necessary screening. Other PUDOs, safe return, rest, vehicle repositioning, aid and dynamic arrival constraints not established.',
        'natural_incidence_supported':False}
    dump_json(target.with_suffix('.summary.json'),report)
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--dataset_dir',required=True)
    p.add_argument('--pairs',required=True);p.add_argument('--output',required=True)
    p.add_argument('--notice_times_s',default='0,30,60,90,120,180')
    p.add_argument('--speed_mps',type=float,default=1.0);p.add_argument('--ack_s',type=float,default=3.0)
    p.add_argument('--limit',type=int,default=60);p.add_argument('--pause_during_ack',action='store_true')
    a=p.parse_args();ts=[float(x) for x in a.notice_times_s.split(',')]
    if not ts or any(not math.isfinite(x) or x<0 for x in ts) or a.speed_mps<=0 or a.ack_s<0:
        p.error('invalid times/speed/ack')
    print(json.dumps(run(a.dataset_dir,a.pairs,a.output,ts,a.speed_mps,a.ack_s,a.limit,a.pause_during_ack)['counts']))
