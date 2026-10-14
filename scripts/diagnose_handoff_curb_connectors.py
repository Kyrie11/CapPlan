#!/usr/bin/env python3
"""Audit why strict typed-curb mining rejects real-map PUDO anchors.

This diagnoses representation gaps, not a license to add synthetic connectors.
A sidewalk-snapped PUDO is pedestrian-reachable but DOES NOT prove the vehicle
interface, legal stopping pose or boarding deployment geometry is feasible.
"""
from __future__ import annotations
import argparse, json, math, random, sys
from collections import Counter, defaultdict
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from capplan.data.accessibility_layer import load_accessibility_graph


def rows(path):
    with Path(path).open(encoding='utf-8') as handle:
        for line in handle:
            if line.strip():yield json.loads(line)


def diagnostic(root,out,limit=100,seed=13,min_width=.9,max_slope=.08,max_cross_slope=.04):
    eps=list(rows(root/'episodes.jsonl'))
    # Deterministic sample but never pretend this is the complete split.
    random.Random(seed).shuffle(eps);eps=eps[:limit]
    ids={e['episode_id'] for e in eps}
    anchor_map=defaultdict(list)
    for a in rows(root/'pudo_anchors.jsonl'):
        if a['episode_id'] in ids:anchor_map[a['episode_id']].append(a)
    counts=Counter();by_city=defaultdict(Counter);examples=[];errors=[]
    for ep in eps:
        eid=ep['episode_id'];city=ep.get('map_name','UNKNOWN');counts['episodes_attempted']+=1
        try:graph=load_accessibility_graph(root,eid)
        except (FileNotFoundError,TypeError,ValueError,KeyError) as e:
            errors.append({'episode_id':eid,'error':repr(e)});continue
        counts['episodes_loaded']+=1
        node_by={n.node_id:n for n in graph.nodes}
        incident=defaultdict(list)
        for edge in graph.edges:
            if str(edge.crossing_type).lower() in ('curb','curb_connector'):
                counts['typed_curb_edges_total']+=1
                incident[edge.from_node].append(edge);incident[edge.to_node].append(edge)
        policy=str(graph.metadata.get('pudo_connector_policy','not_declared'))
        counts['graph_policy:'+policy]+=1
        for a in anchor_map[eid]:
            if a.get('kind') not in ('pickup','pickup_dropoff') or a.get('legal_stop') is not True:
                continue
            counts['legal_pickup_anchors']+=1;by_city[city]['legal_pickup_anchors']+=1
            ped=a.get('adjacent_ped_node_id') or a.get('anchor_id')
            n=node_by.get(ped)
            if n is None:outcome='ped_node_missing'
            elif not incident[ped]:outcome='sidewalk_node_no_typed_curb_incident'
            else:
                relevant=incident[ped]
                if not any((not e.obstacle and str(e.obstacle_state).lower()!='blocked'
                   and e.step_free is True and e.width_m is not None and e.width_m>=min_width
                   and e.slope is not None and abs(e.slope)<=max_slope
                   and e.cross_slope is not None and abs(e.cross_slope)<=max_cross_slope)
                   for e in relevant):outcome='typed_curb_edge_fails_contract_or_unknown'
                else:outcome='typed_curb_edge_passes_screen'
            counts[outcome]+=1;by_city[city][outcome]+=1
            if len(examples)<100:
                examples.append({'episode_id':eid,'city':city,'anchor_id':a['anchor_id'],
                                 'pedestrian_node':ped,'pedestrian_node_kind':getattr(n,'kind',None),
                                 'status':outcome,'pudo_connector_policy':policy,
                                 'relevant_typed_curb_edge_sources':[e.source for e in incident.get(ped,[])][:5],
                                 'interface_truth_mode':a.get('truth_mode'),
                                 'physical_site_key':a.get('hybrid_physical_site_key')})
    result={'scope':'schema and connectivity diagnosis; not legal stop/board feasibility audit',
            'sampled_episodes':len(eps),'counts':dict(counts),'by_city':{k:dict(v) for k,v in by_city.items()},
            'errors':errors,'examples':examples,
            'interpretation':{
              'sidewalk_node_no_typed_curb_incident':'PUDO references reachable pedestrian node without a typed curb edge; missing representation does not itself prove physical curb infeasibility',
              'typed_curb_edge_passes_screen':'Typed edge passes graph-level contract, not physical PUDO verification',
              'graph_policy':'Do not create simulated curb connectors just to increase positive witnesses'},
            'next_steps':['Check candidate-to-curb geometry and municipal GIS outside this program',
                          'Add audited connector records only where supported by external evidence',
                          'Keep UNKNOWN rather than converting inferred crossing into measured curb',
                          'Re-run strict-screen and independent motion verification']}
    out.mkdir(parents=True,exist_ok=True)
    (out/'connector_gap.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'counts':dict(counts),'errors':len(errors)},ensure_ascii=False))
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('--dataset_dir',required=True);p.add_argument('--output_dir',required=True)
    p.add_argument('--episode_limit',type=int,default=100);p.add_argument('--seed',type=int,default=13)
    p.add_argument('--min_width_m',type=float,default=.9);p.add_argument('--max_slope',type=float,default=.08)
    p.add_argument('--max_cross_slope',type=float,default=.04)
    a=p.parse_args()
    diagnostic(Path(a.dataset_dir),Path(a.output_dir),a.episode_limit,a.seed,a.min_width_m,a.max_slope,a.max_cross_slope)

if __name__=='__main__':main()
