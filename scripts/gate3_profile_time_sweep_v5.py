#!/usr/bin/env python3
"""Counterfactual time/speed/ack/profile stress test of optimistic graph recourse.

IMPORTANT: This reuses ORIGINAL screening graph. Profile distance is applied,
but categorical mobility, empirical speed, vehicle windows and PUDO interface
are NOT validated; output is a graph-level upper-bound screening exercise.
"""
from __future__ import annotations
import argparse,copy,json,math,sys
from collections import Counter
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from capplan.data.accessibility_layer import load_accessibility_graph
from capplan.handoff.gate_evidence import records
from scripts.audit_handoff_midroute import evaluate_pair, _graph_distances

def evaluate_grid(pair,graph,profiles,times,speeds,ack_values):
    # Original-screening graph shared across the entire time/profile grid.
    # Recomputing Dijkstra for every notice/ack/contract could be prohibitively slow.
    to_b=_graph_distances(graph,pair["screening_contract"],pair["pedestrian_graph_nodes"]["B"])
    for profile in profiles:
        budget=float(profile['mobility']['max_access_distance_m'])
        if not math.isfinite(budget) or budget<0:raise ValueError('invalid profile access budget')
        # Graph was screened under the ORIGINAL contract. Applying a stricter
        # capability profile can only make this optimistic reachability bound weaker.
        p=copy.deepcopy(pair);p['screening_contract']['distance_budget_m']=budget
        for speed in speeds:
            for ack in ack_values:
                for notice in times:
                    r=evaluate_pair(p,graph,notice,speed,ack,precomputed_to_b=to_b)
                    yield {'episode_id':p['episode_id'],'a_anchor':p['a_anchor'],'b_anchor':p['b_anchor'],
                       'profile_id':profile['profile_id'],'budget_m':budget,'notice_s':notice,'speed_mps':speed,
                       'ack_s':ack,'graph_possible':r['can_redirect_to_B_in_graph'],
                       'total_graph_distance_m':r['best_possible_total_distance_to_B_m'],
                       'progress_m':r['progress_m'], 'research_scope':'OPTIMISTIC_SYMMETRIC_GRAPH_DISTANCE_ONLY'}

def run(pairs,profiles,dataset_dir,output,max_pairs=24,max_profiles=12,times=(0,15,30,60,90),speeds=(.6,1.0,1.3),acks=(0,3,10)):
    profiles=list(records(profiles))[:max_profiles]
    if not profiles:raise ValueError('no profiles')
    selected=[];seen=set()
    for p in records(pairs):
        key=(p.get('episode_id'),p.get('a_anchor'),p.get('b_anchor'))
        if key not in seen:selected.append(p);seen.add(key)
        if len(selected)>=max_pairs:break
    if not selected:raise ValueError('no pairs')
    rows=[];errors=[];graph_cache={}
    for p in selected:
        try:
            ep=p['episode_id']
            if ep not in graph_cache:graph_cache[ep]=load_accessibility_graph(dataset_dir,ep)
            rows.extend(evaluate_grid(p,graph_cache[ep],profiles,times,speeds,acks))
        except (KeyError,ValueError,TypeError,FileNotFoundError) as e:errors.append({'episode_id':p.get('episode_id'),'error':str(e)})
    out=Path(output);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows))
    grouped={};from collections import defaultdict
    groups=defaultdict(list)
    for r in rows:groups[(r['profile_id'],r['speed_mps'],r['ack_s'],r['notice_s'])].append(r)
    agg=[{'profile_id':k[0],'speed_mps':k[1],'ack_s':k[2],'notice_s':k[3],
         'graph_possible_count':sum(r['graph_possible'] for r in v),'count':len(v)} for k,v in sorted(groups.items())]
    summary={'pairs_selected':len(selected),'pairs_succeeded':len(selected)-len(errors),'profiles':len(profiles),
        'grid_rows':len(rows),'errors':errors,'group_summary':agg,
        'caution':'Optimistic graph-only distance upper-bound. Routes originally screened for a different mobility contract; no independent pedestrian speed or physical handoff.'}
    out.with_suffix('.summary.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False)+'\n')
    return summary

def main():
    a=argparse.ArgumentParser();a.add_argument('--pairs',required=True);a.add_argument('--profiles',required=True)
    a.add_argument('--dataset_dir',required=True);a.add_argument('--output',required=True)
    a.add_argument('--max_pairs',type=int,default=24);a.add_argument('--max_profiles',type=int,default=12)
    a.add_argument('--notice_times_s',default='0,15,30,60,90');a.add_argument('--speeds_mps',default='0.6,1.0,1.3');a.add_argument('--acks_s',default='0,3,10')
    x=a.parse_args();parse=lambda s:[float(v) for v in s.split(',')]
    times,speeds,acks=map(parse,(x.notice_times_s,x.speeds_mps,x.acks_s))
    if any((not math.isfinite(t) or t<0) for t in times+acks) or any((not math.isfinite(s) or s<=0) for s in speeds):a.error('invalid grid')
    d=run(x.pairs,x.profiles,x.dataset_dir,x.output,x.max_pairs,x.max_profiles,times,speeds,acks)
    print(json.dumps({k:d[k] for k in ('pairs_selected','profiles','grid_rows','errors')},ensure_ascii=False))
if __name__=='__main__':main()
