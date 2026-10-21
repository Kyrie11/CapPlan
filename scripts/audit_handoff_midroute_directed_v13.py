#!/usr/bin/env python3
"""Directed, graph-only mid-edge B-redirection falsification (read-only).

Previous mining used symmetric edges to generate O->A routes. Revalidate every
stored edge in the actual directed graph. A directional failure or insufficient
budget is NEVER proof of physical stranding: an unencoded U-turn, third PUDO,
vehicle reposition or safe aid may still exist. The result is a necessary
screening observation, not a passenger boarding certificate.
"""
from __future__ import annotations
import argparse,heapq,json,math,sys
from collections import defaultdict,Counter
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from capplan.data.accessibility_layer import load_accessibility_graph
from capplan.handoff.gate_evidence import records,dump_json
from scripts.mine_handoff_recourse_pairs import _valid_edge


def allowed_graph(graph,contract):
    config=SimpleNamespace(require_step_free=bool(contract.get('require_step_free')),
      min_width_m=float(contract['min_width_m']),max_slope=float(contract['max_slope']),
      max_cross_slope=float(contract['max_cross_slope']),min_confidence=float(contract.get('min_confidence',0)))
    by_id={};incoming=defaultdict(list);edge_pairs={}
    for e in graph.edges:
        if _valid_edge(e,config):
            by_id.setdefault(e.edge_id,[]).append(e)
            incoming[e.to_node].append((e.from_node,float(e.length_m)))
            edge_pairs[(e.from_node,e.to_node)]=min(float(e.length_m),edge_pairs.get((e.from_node,e.to_node),float('inf')))
    return by_id,incoming,edge_pairs


def distances_to_b(incoming,b):
    d={b:0.0};pq=[(0.0,b)]
    while pq:
        cost,u=heapq.heappop(pq)
        if cost>d[u]+1e-8:continue
        for v,length in incoming.get(u,[]):
            nv=cost+length
            if nv+1e-8<d.get(v,float('inf')):
                d[v]=nv;heapq.heappush(pq,(nv,v))
    return d


def analyze_pair(pair,graph,notice_s,ack_s=3.,speed_mps=1.,pause_during_ack=False,cache=None):
    if any(not math.isfinite(x) or x<0 for x in (notice_s,ack_s,speed_mps)) or speed_mps==0:
        raise ValueError('invalid notice/ack/speed')
    path=pair['paths']['origin_to_a'];nodes=path['node_ids'];edges=path['edges'];contract=pair['screening_contract']
    if len(edges)+1!=len(nodes) or len(nodes)<2:raise ValueError('invalid stored path')
    if cache is None:cache=allowed_graph(graph,contract)
    by_id,incoming,pairs=cache
    lengths=[];bad=[]
    for i,item in enumerate(edges):
        u,v=nodes[i:i+2];eid=item['edge_id']
        oriented=[e for e in by_id.get(eid,[]) if e.from_node==u and e.to_node==v]
        if not oriented:bad.append({'edge_id':eid,'u':u,'v':v})
        else:
            length=min(e.length_m for e in oriented)
            if abs(length-float(item['length_m']))>1e-3:
                bad.append({'edge_id':eid,'error':'stored_length_directional_mismatch'})
            lengths.append(float(length))
    summary={'episode_id':pair.get('episode_id'),'a_anchor':pair.get('a_anchor'),'b_anchor':pair.get('b_anchor'),
       'notice_issued_s':notice_s,'actionable_s':notice_s+ack_s,
       'scope':'DIRECTED_GRAPH_B_ONLY_NOT_GLOBAL_RECOVERY_OR_PHYSICAL_HANDOFF',
       'third_pudo_and_vehicle_actions_unchecked':True,'unsafe_real_world_commitment_authorized':False}
    if bad:
        return {**summary,'status':'UNKNOWN_MINED_PATH_NOT_DIRECTIONALLY_VALIDATED',
                'invalid_or_reverse_stored_edges':bad[:10],
                'note':'Original O->A path mined with symmetric edges. Cannot treat it as directional passenger history.'}
    length=sum(lengths)
    stored=float(pair['distances_m']['origin_to_a'])
    if abs(length-stored)>1e-3:raise ValueError('stored directed path length inconsistent')
    moved=notice_s*speed_mps if pause_during_ack else (notice_s+ack_s)*speed_mps
    progress=min(length,max(0.,moved))
    b=pair['pedestrian_graph_nodes']['B']
    to_b=distances_to_b(incoming,b)
    if progress>=length-1e-8:
        remaining=to_b.get(nodes[-1],float('inf'));point='AT_A';reverse_used=False
    else:
        cur=0.;remaining=float('inf');point='UNRESOLVED';reverse_used=False
        for k,ln in enumerate(lengths):
            if progress<=cur+ln+1e-8:
                u,v=nodes[k:k+2];into=max(0,progress-cur)
                # A passenger on a directed edge can continue to v. Reversal
                # is allowed only if the reverse edge is explicitly present.
                forward=(ln-into)+to_b.get(v,float('inf'))
                backward=float('inf')
                rev_len=pairs.get((v,u))
                if rev_len is not None:
                    backward=into/ln*rev_len+to_b.get(u,float('inf')) if ln>0 else to_b.get(u,float('inf'))
                reverse_used=backward<forward
                remaining=min(forward,backward)
                if into<=1e-8:
                    # At an actual graph vertex, all outgoing options are
                    # available; do not falsely force the old itinerary.
                    remaining=min(remaining,to_b.get(u,float('inf')))
                point=f'{u}->{v}:fraction={into/ln:.3f}' if ln>0 else 'ZERO_LENGTH_EDGE'
                break
            cur+=ln
    used=float(contract.get('already_consumed_m',0))+progress
    budget=float(contract['distance_budget_m'])
    feasible=math.isfinite(remaining) and used+remaining<=budget+1e-8
    return {**summary,'status':'DIRECTED_B_GRAPH_BUDGET_FEASIBLE' if feasible else
                               'B_NOT_REACHABLE_WITHIN_DECLARED_DIRECTED_GRAPH_BUDGET',
            'progress_m':round(progress,4),'location_on_path':point,
            'remaining_m_to_B':round(remaining,4) if math.isfinite(remaining) else None,
            'consumed_m_at_actionable_notice':round(used,4),
            'distance_budget_m':budget,'computed_total_m':round(used+remaining,4) if math.isfinite(remaining) else None,
            'explicit_reverse_edge_used':reverse_used,
            'is_a_true_global_no_recourse_certificate':False}


def run(dataset_dir,pairs_path,output,times,*,ack_s=3.,speed_mps=1.,pause=False,max_pairs=32):
    root=Path(dataset_dir);out=Path(output);out.parent.mkdir(parents=True,exist_ok=True)
    cache={};rows=[];counts=Counter();failures=[]
    with out.open('w',encoding='utf8') as file:
        for i,pair in enumerate(records(Path(pairs_path))):
            if i>=max_pairs:break
            try:
                ep=pair['episode_id']
                if ep not in cache:
                    graph=load_accessibility_graph(root,ep)
                    built=allowed_graph(graph,pair['screening_contract'])
                    cache[ep]=(graph,built)
                graph,built=cache[ep]
                for t in times:
                    r=analyze_pair(pair,graph,t,ack_s=ack_s,speed_mps=speed_mps,pause_during_ack=pause,cache=built)
                    file.write(json.dumps(r,ensure_ascii=False)+'\n');counts[r['status']]+=1
            except (ValueError,KeyError,TypeError,FileNotFoundError) as e:
                failures.append({'episode_id':pair.get('episode_id'),'reason':type(e).__name__+': '+str(e)})
    summary={'case_evaluations':sum(counts.values()),'pair_limit':max_pairs,'statuses':dict(counts),
       'errors':failures[:20],'graph_only':True,'third_options_tested':False,
       'positive_does_not_verify_real_handoff':True,'negative_does_not_prove_no_global_recourse':True}
    dump_json(out.with_suffix('.summary.json'),summary)
    return summary


def main():
    p=argparse.ArgumentParser();p.add_argument('--dataset_dir',required=True);p.add_argument('--pairs',required=True)
    p.add_argument('--output',required=True);p.add_argument('--times_s',default='0,15,30,60,90')
    p.add_argument('--ack_s',type=float,default=3);p.add_argument('--speed_mps',type=float,default=0.8)
    p.add_argument('--max_pairs',type=int,default=32);p.add_argument('--pause_during_ack',action='store_true')
    a=p.parse_args();times=[float(x) for x in a.times_s.split(',')]
    if not times or a.max_pairs<1: p.error('bad times or pair count')
    print(json.dumps(run(a.dataset_dir,a.pairs,a.output,times,ack_s=a.ack_s,speed_mps=a.speed_mps,
                         pause=a.pause_during_ack,max_pairs=a.max_pairs),ensure_ascii=False))
if __name__=='__main__':main()
