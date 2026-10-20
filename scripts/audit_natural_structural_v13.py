#!/usr/bin/env python3
"""Natural, screening-blind V12 sample -> V13 local coupled-feasibility census.

Separate the precommitted sample from mined cases; uses only dataset-declared
fields and optional graph-interface evidence, never labels physical success.
Outputs every chosen episode including zero candidate/unknown cases.
"""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
from collections import Counter,defaultdict
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from capplan.handoff.structural_gap_v13 import episode_signature,provenance_kind


def read_jsonl(p):
    with open(p,encoding='utf8') as f:
        for line in f:
            if line.strip():yield json.loads(line)


def run(dataset,sample,output,graph_limit=0,**kwargs):
    dataset=Path(dataset); selected=list(read_jsonl(sample)); ids={str(x['episode_id']) for x in selected}
    if len(ids)!=len(selected):raise ValueError('Duplicate episodes in preregistered sample')
    by=defaultdict(list)
    for a in read_jsonl(dataset/'pudo_anchors.jsonl'):
        ep=str(a.get('episode_id',''))
        if ep in ids and a.get('kind') in ('pickup','pickup_dropoff'):
            by[ep].append(a)
    rows=[];provenance=defaultdict(Counter);summary=Counter(); city_counts=defaultdict(Counter)
    for x in selected:
        ep=str(x['episode_id']); city=str(x.get('city','unknown'));anchors=by[ep]
        s=episode_signature(anchors,**kwargs)
        row={'episode_id':ep,'city':city,'original_sampling_design':x.get('sampling_design'),**s}
        summary['sampled_episodes']+=1;summary['pickup_anchor_records']+=len(anchors)
        city_counts[city]['episodes']+=1;city_counts[city]['anchors']+=len(anchors)
        for k in ('independent_existential_gap','declared_local_joint_exists'):
            summary[k]+=int(bool(s[k]));city_counts[city][k]+=int(bool(s[k]))
        for a in anchors:
            for field in ('sidewalk_width_m','curb_ramp','curb_height_m','legal_stop','blockage_risk','deployment_clearance_m','stop_pose'):
                provenance[field][provenance_kind(a,field)]+=1
        rows.append(row)
    # Graph probes are a *separate quality screen*; no UNKNOWN->FAIL promotion.
    # Explicit typed curb edge presence is not identical to curb physical validity.
    graph_probes=[]
    if graph_limit:
        from capplan.data.accessibility_layer import load_accessibility_graph
        # Round-robin city selection avoids evaluating Boston alone when the
        # preregistered cohort is stored in city-grouped order.
        by_city=defaultdict(list)
        for r in rows:by_city[r['city']].append(r)
        balanced=[]
        while len(balanced)<graph_limit and any(by_city.values()):
            for city in sorted(by_city):
                if by_city[city] and len(balanced)<graph_limit:
                    balanced.append(by_city[city].pop(0))
        for row in balanced:
            entry={'episode_id':row['episode_id'],'city':row['city']}
            try:
                graph=load_accessibility_graph(dataset,row['episode_id'])
                curb_nodes=set(); cross=Counter()
                for e in graph.edges:
                    typ=str(e.crossing_type or 'missing').lower();cross[typ]+=1
                    if typ=='curb':curb_nodes.update((e.from_node,e.to_node))
                anchors=by[row['episode_id']]
                entry.update({'graph_loaded':True,'nodes':len(graph.nodes),'edges':len(graph.edges),
                  'typed_curb_edges':cross.get('curb',0),
                  'pickup_anchors_with_any_typed_curb_endpoint':sum(a.get('adjacent_ped_node_id') in curb_nodes for a in anchors),
                  'pickup_anchors_checked':len(anchors),
                  'physical_connection_certified':False})
            except (FileNotFoundError,ValueError,KeyError,TypeError,RuntimeError) as e:
                entry.update({'graph_loaded':False,'error':str(e)[:400]})
            graph_probes.append(entry)
    out=Path(output);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in rows),encoding='utf8')
    result={'sampling_unit':'preregistered retained nuPlan-hybrid EPISODE, not a natural passenger trip',
            'precommitted_unselected_negative_cases_included':True,
            'counts':dict(summary),'cities':{c:dict(v) for c,v in city_counts.items()},
            'field_provenance_raw':{k:dict(v) for k,v in provenance.items()},
            'graph_probes':graph_probes,'assumed_screening_contract':kwargs,
            'severe_limitations':['local anchor constraints only, not walking paths or stop dynamics',
              'simulated/derived evidence must not be called street ground truth',
              'no independently certified motion/interface acceptance','sample restricted to retained hybrid cohort']}
    out.with_suffix('.summary.json').write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n')
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dataset',required=True);p.add_argument('--sample',required=True)
    p.add_argument('--output',required=True);p.add_argument('--graph_limit',type=int,default=12)
    p.add_argument('--min_width',type=float,default=1.2);p.add_argument('--min_clearance',type=float,default=1.2)
    p.add_argument('--max_curb',type=float,default=.05);p.add_argument('--max_blockage',type=float,default=.85)
    a=p.parse_args()
    if a.graph_limit<0 or a.min_width<0 or a.min_clearance<0 or a.max_curb<0 or not 0<a.max_blockage<1:p.error('invalid threshold')
    result=run(a.dataset,a.sample,a.output,a.graph_limit,min_width=a.min_width,
        min_clearance=a.min_clearance,max_curb=a.max_curb,max_blockage=a.max_blockage)
    print(json.dumps(result['counts'],ensure_ascii=False))
if __name__=='__main__':main()
