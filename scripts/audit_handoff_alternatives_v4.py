#!/usr/bin/env python3
"""Attempt to falsify the two-anchor 'no-recourse' claim with other PUDOs.

Checks distance + screening-edge accessibility only.  It does not assume a
simulated legal_stop is municipal law or that a reachable pedestrian anchor
has a valid curb/vehicle boarding interface.  No global-unreachability claim.
"""
from __future__ import annotations
import argparse,json,math,sys
from collections import Counter,defaultdict
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from capplan.data.accessibility_layer import load_accessibility_graph
from capplan.handoff.gate_evidence import records,dump_json
from scripts.mine_handoff_recourse_pairs import _shortest_tree,_valid_edge


def evaluate(p, graph, anchors, max_alternatives=20):
    c=p['screening_contract'];budget=float(c['distance_budget_m'])
    already=float(c.get('already_consumed_m',0))
    d_oa=float(p['distances_m']['origin_to_a']);anode=p['pedestrian_graph_nodes']['A']
    args=SimpleNamespace(require_step_free=bool(c.get('require_step_free')),
       min_width_m=float(c['min_width_m']),max_slope=float(c['max_slope']),
       max_cross_slope=float(c['max_cross_slope']),min_confidence=float(c.get('min_confidence',0)))
    adj=defaultdict(list)
    for e in graph.edges:
        if _valid_edge(e,args):
            adj[e.from_node].append((e.to_node,e));adj[e.to_node].append((e.from_node,e))
    dist,_=_shortest_tree(adj,anode)
    result=[]
    nodes={n.node_id for n in graph.nodes}
    for a in anchors:
        if a.get('anchor_id') in (p['a_anchor'],p['b_anchor']):continue
        if a.get('kind') not in ('pickup','pickup_dropoff'):continue
        node=a.get('adjacent_ped_node_id') or a.get('anchor_id')
        if node not in nodes:continue
        rem=dist.get(node,float('inf'))
        total=already+d_oa+rem
        if not math.isfinite(total) or total>budget+1e-9:continue
        result.append({'anchor_id':a.get('anchor_id'),'physical_site_key':a.get('hybrid_physical_site_key'),
            'route_total_distance_m':round(total,4),'distance_remaining_margin_m':round(budget-total,4),
            'dataset_legal_stop':a.get('legal_stop'),
            'curb_interface_independently_verified':False,'vehicle_motion_independently_verified':False,
            'interpretation':'GRAPH_DISTANCE_POSSIBLE_NOT_CONFIRMED_PICKUP'})
    result.sort(key=lambda x:(-x['distance_remaining_margin_m'],str(x['anchor_id'])))
    return {'episode_id':p['episode_id'],'a_anchor':p['a_anchor'],'b_anchor':p['b_anchor'],
       'distance_feasible_other_anchors_count':len(result),'examples':result[:max_alternatives],
       'claim':'Original A->B failure does not establish global no-recourse even if no third option appears',
       'vehicle_and_curb_evidence':'NOT_VERIFIED'}


def run(pairs,dataset_dir,output,limit=100):
    root=Path(dataset_dir);chosen=[];episodes=set()
    for p in records(pairs):
        if len(chosen)>=limit:break
        chosen.append(p);episodes.add(p['episode_id'])
    anchor_by_ep=defaultdict(list)
    for a in records(root/'pudo_anchors.jsonl'):
        if a.get('episode_id') in episodes:anchor_by_ep[a['episode_id']].append(a)
    graphs={};rows=[];counts=Counter();errors=[]
    for p in chosen:
        try:
            ep=p['episode_id']
            if ep not in graphs:graphs[ep]=load_accessibility_graph(root,ep)
            r=evaluate(p,graphs[ep],anchor_by_ep[ep]);rows.append(r)
            counts['attempted']+=1
            counts['graph_other_possible']+=int(r['distance_feasible_other_anchors_count']>0)
            counts['total_graph_other_options']+=r['distance_feasible_other_anchors_count']
        except (ValueError,KeyError,TypeError,FileNotFoundError) as e:
            errors.append({'episode_id':p.get('episode_id'),'error':str(e)})
    out=Path(output);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
    summary={'counts':dict(counts),'errors':errors[:20], 'candidate_denominator':len(chosen),
      'case_validation':'graph-only alternatives, not true curb/vehicle service availability',
      'incidence_claim_allowed':False}
    dump_json(out.with_suffix('.summary.json'),summary);return summary

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--pairs',required=True);ap.add_argument('--dataset_dir',required=True)
    ap.add_argument('--output',required=True);ap.add_argument('--limit',type=int,default=100)
    a=ap.parse_args();print(json.dumps(run(a.pairs,a.dataset_dir,a.output,a.limit),ensure_ascii=False))
