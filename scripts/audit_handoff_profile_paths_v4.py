#!/usr/bin/env python3
"""Functional contract -> pedestrian graph/curb interface cross-check.

Uses actual benchmark graph for each canonical profile, with field provenance
preserved.  Not a passenger-complete or empirical accessibility assertion:
vehicle equipment, legal permission, live wait/ride and human confirmation
are explicitly unresolved.
"""
from __future__ import annotations
import argparse,json,math,sys
from collections import Counter,defaultdict
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from capplan.data.accessibility_layer import load_accessibility_graph
from capplan.data.capability_contracts import profile_to_contract
from capplan.handoff.gate_evidence import records,dump_json
from scripts.mine_handoff_recourse_pairs import _valid_edge,_shortest_tree


def evaluate(p,profile,graph):
    # Compile the FULL program first; the audit still evaluates only its
    # declared pedestrian and subset of curb interface dimensions.
    profile_to_contract(profile)
    m=profile['mobility'];i=profile['interface'];u=profile['uncertainty']
    cond=SimpleNamespace(require_step_free=bool(m.get('step_free_required')),
        min_width_m=float(m['min_clear_width_m']),max_slope=float(m['max_slope']),
        max_cross_slope=float(m['max_cross_slope']),min_confidence=float(u['min_map_confidence']))
    adj=defaultdict(list)
    allowed=set(m.get('allowed_surfaces') or [])
    for e in graph.edges:
        if _valid_edge(e,cond) and (not allowed or e.surface in allowed):
            adj[e.from_node].append((e.to_node,e));adj[e.to_node].append((e.from_node,e))
    dists,_=_shortest_tree(adj,p['pedestrian_graph_nodes']['origin'])
    budget=float(m['max_access_distance_m']);consumed=float(p['screening_contract'].get('already_consumed_m',0))
    opts={}
    for end,letter in [('A','a'),('B','b')]:
        node=p['pedestrian_graph_nodes'][end];distance=dists.get(node,math.inf)
        interface=p.get('pudo_interface_'+letter) or {};fails=[];unknown=[]
        if not math.isfinite(distance):fails.append('ACCESS_ROUTE_NOT_FOUND_UNDER_HYBRID_GRAPH')
        elif distance+consumed>budget+1e-9:fails.append('ACCESS_DISTANCE_BUDGET')
        clear=interface.get('deployment_clearance_m')
        if clear is None:unknown.append('DEPLOYMENT_CLEARANCE_MISSING')
        elif float(clear)<float(i.get('min_deployment_clearance_m') or 0):fails.append('DEPLOYMENT_CLEARANCE_IN_HYBRID')
        sidewalk=interface.get('sidewalk_width_m')
        if sidewalk is None:unknown.append('SIDEWALK_WIDTH_MISSING')
        elif float(sidewalk)<float(m['min_clear_width_m']):fails.append('CURB_SIDEWALK_WIDTH_IN_HYBRID')
        pref=i.get('preferred_door_side','either');side=interface.get('side')
        if pref not in ('either','both',None):
            if side is None:unknown.append('CURB_SIDE_UNKNOWN')
            elif side not in ('both',pref):fails.append('PREFERRED_DOOR_SIDE_IN_HYBRID')
        if m.get('curb_ramp_required'):
            unknown.append('RAMP_PHYSICAL_CONNECTOR_UNVERIFIED')
        if i.get('boarding_any_of'):
            unknown.append('VEHICLE_BOARDING_EQUIPMENT_UNVERIFIED')
        if not (p.get('explicit_curb_connector_evidence') or {}).get(end):
            unknown.append('PHYSICAL_CURB_CONNECTOR_UNVERIFIED')
        unknown.extend(['REAL_STOP_PERMISSION_UNVERIFIED','LIVE_VEHICLE_POSE_UNVERIFIED',
                        'WAIT_AND_DWELL_WINDOW_UNVERIFIED','RIDE_AND_HANDOFF_UNVERIFIED'])
        opts[end]={'hybrid_graph_route_distance_m':distance if math.isfinite(distance) else None,
            'hybrid_hard_fail':bool(fails),'hybrid_hard_fail_reasons':fails,
            'unresolved_service_axes':unknown,
            'hybrid_screening_pass':not fails,
            'real_world_handoff_verified':False}
    return {'episode_id':p['episode_id'],'profile_id':profile['profile_id'],
       'archetype':profile.get('archetype'),'A':opts['A'],'B':opts['B'],
       'preference_flip_possible_in_hybrid_screen':opts['A']['hybrid_screening_pass']!=opts['B']['hybrid_screening_pass'],
       'interpretation':'Subset functional contract, hybrid graph/interface labels only; no real-world capability pass'}


def run(pairs,profiles,dataset_dir,output,max_pairs=32,max_profiles=40):
    chosen=[]
    for p in records(pairs):
        if len(chosen)>=max_pairs:break
        chosen.append(p)
    pr=[]
    for p in records(profiles):
        if len(pr)>=max_profiles:break
        if p.get('evaluation_suite')!='canonical_counterfactual' or p.get('population_representative'):
            raise ValueError('canonical stress profiles only; empirical evaluation must stay separate')
        pr.append(p)
    graphs={};rows=[];counts=Counter();errors=[]
    for p in chosen:
        try:
            ep=p['episode_id']
            if ep not in graphs:graphs[ep]=load_accessibility_graph(dataset_dir,ep)
            for prof in pr:
                r=evaluate(p,prof,graphs[ep]);rows.append(r)
                counts['profile_pair_evaluations']+=1
                counts['hybrid_A_pass']+=int(r['A']['hybrid_screening_pass'])
                counts['hybrid_B_pass']+=int(r['B']['hybrid_screening_pass'])
                counts['hybrid_A_B_flip']+=int(r['preference_flip_possible_in_hybrid_screen'])
        except (ValueError,KeyError,TypeError,FileNotFoundError) as e:
            errors.append({'episode_id':p.get('episode_id'),'error':str(e)})
    out=Path(output);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
    result={'counts':dict(counts),'errors':errors[:20],
       'all_passes_source':'hybrid benchmark; not real world',
       'limits':'static pedestrian+partial curb subset only, no real motion/observation or empirical prevalence'}
    dump_json(out.with_suffix('.summary.json'),result);return result

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--pairs',required=True);ap.add_argument('--profiles',required=True)
    ap.add_argument('--dataset_dir',required=True);ap.add_argument('--output',required=True)
    ap.add_argument('--max_pairs',type=int,default=32);ap.add_argument('--max_profiles',type=int,default=40)
    a=ap.parse_args();print(json.dumps(run(a.pairs,a.profiles,a.dataset_dir,a.output,a.max_pairs,a.max_profiles),ensure_ascii=False))
