#!/usr/bin/env python3
"""Dual strict/optimistic passenger graph screen for every existing PUDO.

Unlike treating missing typed curb edges as nonexistent physical connections,
this distinguishes: (i) explicit valid connector, (ii) geometrically reachable
but connector UNKNOWN, and (iii) absent even in the optimistic graph. Both
screens share the same filtered adjacency and Dijkstra trees. The optimistic
screen is an *upper bound* on candidates under this graph, not proof of boarding.
"""
from __future__ import annotations
import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from capplan.data.accessibility_layer import load_accessibility_graph
from capplan.handoff.gate_evidence import records, dump_json, site_key
from scripts.audit_handoff_all_alternatives_v8 import filtered_adjacency, evaluate


def screen_pair(pair, anchors, graph, notice_times, speed_mps=.8, ack_s=10.,pause=False):
    adjacency,curb,nodes=filtered_adjacency(graph,pair['screening_contract'])
    cached=(adjacency,curb,nodes,{})
    rows=[]
    for t in notice_times:
        strict=evaluate(pair,anchors,graph,t,speed_mps=speed_mps,ack_s=ack_s,
                        pause=pause,require_curb=True,compiled=cached)
        geom=evaluate(pair,anchors,graph,t,speed_mps=speed_mps,ack_s=ack_s,
                      pause=pause,require_curb=False,compiled=cached)
        node_by_anchor={a.get('anchor_id'):a.get('adjacent_ped_node_id') for a in anchors}
        unknown_curb=[dict(x,connector_evidence='MISSING_OR_NOT_CONTRACT_VALID') for x in geom['alternates']
                      if node_by_anchor.get(x['anchor_id']) not in curb]
        # Strict & optimistic example lists are truncated; exact counts below
        # use the full V8 search result, not these example arrays.
        if strict['graph_eligible_alternates_count']:
            tri='EXPLICIT_GRAPH_CONNECTOR_CANDIDATE'
        elif geom['graph_eligible_alternates_count']:
            tri='ONLY_OPTIMISTIC_UNVERIFIED_CONNECTOR_CANDIDATES'
        else:
            tri='NO_GEOMETRY_CANDIDATE_WITHIN_DECLARED_GRAPH'
        rows.append({
            'episode_id':pair['episode_id'],
            'a_anchor':pair['a_anchor'],'b_anchor':pair['b_anchor'],
            'physical_site_pair':site_key(pair),
            'notice_issued_at_s':t,'notice_actionable_at_s':strict['notice_actionable_at_s'],
            'distance_consumed_at_ack_m':strict['distance_consumed_at_ack_m'],
            'graph_evidence_class':tri,
            'strict_explicit_curb_count':strict['graph_eligible_alternates_count'],
            'optimistic_without_curb_count':geom['graph_eligible_alternates_count'],
            'strict_B_graph_feasible':strict['B_graph_feasible'],
            'optimistic_B_graph_feasible':geom['B_graph_feasible'],
            'strict_third_graph_feasible':strict['non_B_alternate_graph_feasible'],
            'optimistic_third_graph_feasible':geom['non_B_alternate_graph_feasible'],
            'missing_typed_curb_count_from_strict':strict['filter_reasons'].get('no_explicit_valid_typed_curb_edge',0),
            'strict_examples':strict['alternates'][:10],
            'optimistic_unverified_examples':unknown_curb[:10],
            'physical_handoff_verified':False,
            'no_global_stranding_certificate':True,
            'optimistic_bidirectional_pedestrian_edges':True,
        })
    return rows


def run(dataset_dir,pairs,output,notice_times,limit=32,speed_mps=.8,ack_s=10.,pause=False):
    # Selection matches original V8, so side-by-side comparison has the
    # same conditional sample; it is not a population incidence estimate.
    selected=[]
    for p in records(pairs):
        selected.append(p)
        if len(selected)>=limit:break
    ids={p['episode_id'] for p in selected}
    by_ep=defaultdict(list)
    for a in records(Path(dataset_dir)/'pudo_anchors.jsonl'):
        if a.get('episode_id') in ids:by_ep[a['episode_id']].append(a)
    pairs_by_ep=defaultdict(list)
    for p in selected:pairs_by_ep[p['episode_id']].append(p)
    out=Path(output);out.parent.mkdir(parents=True,exist_ok=True)
    counter=Counter();errors=[]
    with out.open('w',encoding='utf-8') as f:
        for ep, ps in pairs_by_ep.items():
            try:
                graph=load_accessibility_graph(dataset_dir,ep)
                for p in ps:
                    try:
                        for row in screen_pair(p,by_ep.get(ep,()),graph,notice_times,speed_mps,ack_s,pause):
                            f.write(json.dumps(row,ensure_ascii=False)+'\n')
                            counter[row['graph_evidence_class']]+=1
                            counter['time_rows']+=1
                            counter['strict_B_graph_feasible']+=int(row['strict_B_graph_feasible'])
                            counter['optimistic_B_graph_feasible']+=int(row['optimistic_B_graph_feasible'])
                            counter['optimistic_third_graph_feasible']+=int(row['optimistic_third_graph_feasible'])
                        counter['processed_pairs']+=1
                    except (KeyError,ValueError,TypeError,OverflowError) as e:
                        errors.append({'episode_id':ep,'a_anchor':p.get('a_anchor'),'error':str(e)})
            except (KeyError,ValueError,TypeError,FileNotFoundError) as e:
                errors.append({'episode_id':ep,'error':str(e)})
    summary={
        'counts':dict(counter),'errors':errors,'attempted_pairs':len(selected),
        'requested_notice_times_s':notice_times,'require_typed_curb_connector_for_strict':True,
        'meaning':'Optimistic model only removes strict typed-curb requirement. It is not physical truth.',
        'inference_boundaries':['unknown curb != inaccessible',
            'graph route != physical boarding', 'missing vehicle pose/window/aid',
            'mined cases cannot estimate natural incident rate'],
    }
    dump_json(out.with_suffix('.summary.json'),summary)
    return summary


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dataset_dir',required=True)
    p.add_argument('--pairs',required=True)
    p.add_argument('--output',required=True)
    p.add_argument('--notice_times_s',default='0,30,60,90')
    p.add_argument('--speed_mps',type=float,default=.8)
    p.add_argument('--ack_s',type=float,default=10.)
    p.add_argument('--limit',type=int,default=32)
    p.add_argument('--pause_during_ack',action='store_true')
    a=p.parse_args();tt=[float(x) for x in a.notice_times_s.split(',')]
    if a.limit<1 or any(x<0 for x in tt):p.error('invalid limit/times')
    print(json.dumps(run(a.dataset_dir,a.pairs,a.output,tt,a.limit,a.speed_mps,a.ack_s,a.pause_during_ack)['counts'],ensure_ascii=False))

if __name__=='__main__':main()
