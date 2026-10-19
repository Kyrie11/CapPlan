#!/usr/bin/env python3
"""Try to *falsify* two-anchor no-recourse claims by checking all existing PUDOs.

For each selected A->B mined pair, inspect the passenger's position when a
new instruction becomes actionable, retain consumed distance, and search the
existing filtered pedestrian graph for ANY legal/unblocked alternate anchor.
A candidate is only hybrid-GIS graph plausible, never physically verified.
If none is found, absence of safe external help, vehicle relocation and real
curb connectivity is NOT established. No new PUDO is fabricated.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
from types import SimpleNamespace
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from capplan.data.accessibility_layer import load_accessibility_graph
from capplan.handoff.gate_evidence import records, dump_json, site_key
from scripts.mine_handoff_recourse_pairs import _valid_edge, _shortest_tree


def usable_anchor(a,exclude_id):
    if a.get('anchor_id') == exclude_id:return False
    if a.get('kind') not in ('pickup','pickup_dropoff') or a.get('legal_stop') is not True:return False
    risk=a.get('blockage_risk')
    if risk is None or not isinstance(risk,(int,float)) or not math.isfinite(risk) or risk>=0.85:return False
    return bool(a.get('adjacent_ped_node_id'))


def position_on_path(pair,notice_s,speed_mps,ack_s,pause):
    if not all(math.isfinite(v) for v in (notice_s,speed_mps,ack_s)) or min(notice_s,speed_mps,ack_s)<0 or speed_mps==0:
        raise ValueError('invalid time or speed')
    path=pair['paths']['origin_to_a'];nodes=path['node_ids'];edges=path['edges']
    if len(nodes)<2 or len(edges)!=len(nodes)-1:raise ValueError('invalid path')
    lens=[float(e['length_m']) for e in edges]
    if any(not math.isfinite(x) or x<0 for x in lens):raise ValueError('invalid edge length')
    da=float(pair['distances_m']['origin_to_a'])
    if abs(sum(lens)-da)>1e-3:raise ValueError('path length discrepancy')
    progress=min(da,(notice_s if pause else notice_s+ack_s)*speed_mps)
    if progress>=da-1e-9:return (nodes[-1],0.0,nodes[-1],0.0,progress)
    subtotal=0.0
    for i,length in enumerate(lens):
        if progress<=subtotal+length+1e-9:
            x=min(length,max(0,progress-subtotal))
            # Passenger is inside an edge, can approach either endpoint under
            # the SAME undirected-edge approximation as the original mining.
            return (nodes[i],x,nodes[i+1],length-x,progress)
        subtotal+=length
    raise ValueError('progress could not be mapped onto pedestrian path')


def filtered_adjacency(graph,contract):
    c=SimpleNamespace(require_step_free=bool(contract.get('require_step_free')),
       min_width_m=float(contract['min_width_m']),max_slope=float(contract['max_slope']),
       max_cross_slope=float(contract['max_cross_slope']),
       min_confidence=float(contract.get('min_confidence',0)))
    adj=defaultdict(list);explicit_curb=set();nodes=set(n.node_id for n in graph.nodes)
    for e in graph.edges:
        if not _valid_edge(e,c):continue
        adj[e.from_node].append((e.to_node,e));adj[e.to_node].append((e.from_node,e))
        if str(e.crossing_type).lower()=='curb':
            explicit_curb.update((e.from_node,e.to_node))
    return adj,explicit_curb,nodes


def evaluate(pair,anchors,graph,notice_s,*,speed_mps=.8,ack_s=10.,pause=False,require_curb=True,compiled=None):
    cont=pair['screening_contract']
    if compiled is None:
        adj,curb,nodes=filtered_adjacency(graph,cont)
        cache={}
    else:
        adj,curb,nodes,cache=compiled
    u,du,v,dv,progress=position_on_path(pair,notice_s,speed_mps,ack_s,pause)
    # Two Dijkstra traversals, regardless of number of candidate PUDOs.
    def tree(origin):
        if origin not in cache:
            cache[origin]=_shortest_tree(adj,origin)[0]
            # Bound memory: 32 large GIS graphs may have millions of edges.
            if len(cache)>8:
                oldest=next(iter(cache))
                if oldest!=origin:cache.pop(oldest)
        return cache[origin]
    from_u=tree(u)
    from_v=from_u if u==v else tree(v)
    consumed=float(cont.get('already_consumed_m',0))+progress
    budget=float(cont['distance_budget_m'])
    possible=[];reason=Counter();examined=0
    bad_site=(pair.get('physical_site_pair') or [None,None])[0]
    for anchor in anchors:
        if not usable_anchor(anchor,pair['a_anchor']):reason['not_eligible_or_A']+=1;continue
        if bad_site and anchor.get('hybrid_physical_site_key')==bad_site:
            reason['same_physical_site_as_A']+=1;continue
        examined+=1;node=anchor['adjacent_ped_node_id']
        if node not in nodes:reason['anchor_node_missing']+=1;continue
        if require_curb and node not in curb:reason['no_explicit_valid_typed_curb_edge']+=1;continue
        path=min(du+from_u.get(node,math.inf),dv+from_v.get(node,math.inf))
        if not math.isfinite(path):reason['graph_path_unreachable']+=1;continue
        if consumed+path>budget+1e-9:reason['distance_budget_exceeded']+=1;continue
        possible.append({'anchor_id':anchor['anchor_id'],'physical_site_key':anchor.get('hybrid_physical_site_key'),
          'remaining_graph_m':round(path,4),'total_passenger_distance_m':round(consumed+path,4),
          'is_declared_B':anchor['anchor_id']==pair['b_anchor'],
          'physical_legality_independently_verified':False,
          'full_curb_interface_independently_verified':False})
    possible.sort(key=lambda x:(x['total_passenger_distance_m'],x['anchor_id']))
    b_feasible=any(p['is_declared_B'] for p in possible)
    c_feasible=any(not p['is_declared_B'] for p in possible)
    return {'episode_id':pair['episode_id'],'a_anchor':pair['a_anchor'],'b_anchor':pair['b_anchor'],
      'physical_site_pair':site_key(pair),'notice_issued_at_s':notice_s,
      'notice_actionable_at_s':notice_s+ack_s,
      'passenger_pauses_during_ack':pause,'distance_consumed_at_ack_m':round(consumed,4),
      'distance_budget_m':budget,'legal_unblocked_candidates_checked':examined,
      'graph_eligible_alternates_count':len(possible),'B_graph_feasible':b_feasible,
      'non_B_alternate_graph_feasible':c_feasible,'alternates':possible[:30],
      'filter_reasons':dict(reason),'terminal_A_to_B_over_budget_claim':bool(pair['reroute_total_m']>budget),
      'interpretation':'graph-level counterexample ONLY; no independent real curb, stop, vehicle, aid, physical board',
      'no_alternates_does_NOT_certify_stranding':True}


def run(dataset_dir,pairs,output,notice_times,limit=32,speed_mps=.8,ack_s=10.,pause=False,require_curb=True):
    if limit<1:raise ValueError('limit must be positive')
    selected=[]
    for p in records(pairs):
        selected.append(p)
        if len(selected)>=limit:break
    eps={p['episode_id'] for p in selected}
    per_episode=defaultdict(list)
    for anchor in records(Path(dataset_dir)/'pudo_anchors.jsonl'):
        if anchor.get('episode_id') in eps:per_episode[anchor['episode_id']].append(anchor)
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    counts=Counter();errors=[]
    # Group selected pairs by episode. Load one large graph at a time,
    # build one filtered adjacency per pair and reuse endpoints across times.
    pair_groups=defaultdict(list)
    for p in selected:pair_groups[p['episode_id']].append(p)
    with output.open('w',encoding='utf-8') as f:
        for ep,episode_pairs in pair_groups.items():
            try:
                graph=load_accessibility_graph(dataset_dir,ep)
            except (ValueError,TypeError,FileNotFoundError) as exc:
                counts['attempted_pairs']+=len(episode_pairs)
                errors.append({'episode_id':ep,'exception':repr(exc)})
                continue
            for p in episode_pairs:
                counts['attempted_pairs']+=1
                try:
                    adjacency,curb,nodes=filtered_adjacency(graph,p['screening_contract'])
                    compiled=(adjacency,curb,nodes,{})
                    for t in notice_times:
                        row=evaluate(p,per_episode.get(ep,()),graph,t,speed_mps=speed_mps,ack_s=ack_s,
                            pause=pause,require_curb=require_curb,compiled=compiled)
                        f.write(json.dumps(row,ensure_ascii=False)+'\n');counts['grid_rows']+=1
                        counts['B_graph_feasible']+=int(row['B_graph_feasible'])
                        counts['third_option_graph_feasible']+=int(row['non_B_alternate_graph_feasible'])
                        counts['no_graph_eligible_candidate']+=int(not row['graph_eligible_alternates_count'])
                    counts['processed_pairs']+=1
                except (KeyError,ValueError,TypeError,OverflowError) as exc:
                    errors.append({'episode_id':ep,'a_anchor':p.get('a_anchor'),'exception':repr(exc)})
    summary={'counts':dict(counts),'errors':errors,'times_s':notice_times,
       'symmetric_pedestrian_edges_assumed':True,'require_typed_curb_connector':require_curb,
       'exact_graph_only_not_physical':'No verified passenger boarding, curb permission or motion trajectories',
       'cannot_estimate_incidence':True,'selection':'previously mined conditional asymmetry pairs; no random population sample'}
    dump_json(output.with_suffix('.summary.json'),summary)
    return summary


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dataset_dir',required=True);p.add_argument('--pairs',required=True);p.add_argument('--output',required=True)
    p.add_argument('--notice_times_s',default='0,30,60,90');p.add_argument('--speed_mps',type=float,default=.8)
    p.add_argument('--ack_s',type=float,default=10);p.add_argument('--limit',type=int,default=32)
    p.add_argument('--pause_during_ack',action='store_true')
    p.add_argument('--allow_missing_typed_curb',action='store_true',help='OPTIMISTIC sensitivity ONLY; never physical truth')
    a=p.parse_args();ts=[float(v) for v in a.notice_times_s.split(',')]
    if not ts or any(not math.isfinite(t) or t<0 for t in ts):p.error('invalid notice times')
    summary=run(a.dataset_dir,a.pairs,a.output,ts,a.limit,a.speed_mps,a.ack_s,a.pause_during_ack,
                not a.allow_missing_typed_curb)
    print(json.dumps(summary['counts'],ensure_ascii=False))

if __name__=='__main__':main()
