#!/usr/bin/env python3
"""Audit evidence_chain + graph *without manufacturing curb or vehicle truth*.

Checks external artifact hashes, directed pedestrian connectivity and incident
curb-typed edges. It is a falsification tool, not a real-world PUDO certifier.
"""
from __future__ import annotations
import argparse, collections, hashlib, heapq, io, json, math, re, zipfile
from pathlib import Path


def shortest(adj, start, goal):
    if start == goal: return 0.0
    frontier=[(0.,start)]; best={start:0.}
    while frontier:
        d,u=heapq.heappop(frontier)
        if d>best[u]+1e-9:continue
        if u==goal:return d
        for v,w in adj.get(u,()):
            nd=d+w
            if nd<best.get(v,float('inf')):
                best[v]=nd;heapq.heappush(frontier,(nd,v))
    return None


def parse_node(physical_key):
    found=re.search(r'ped:\d+:\d+',str(physical_key))
    return found.group(0) if found else None


def per_graph(raw_nodes,raw_edges,sites):
    ids={json.loads(r)['node_id'] for r in raw_nodes.splitlines() if r.strip()}
    directed=collections.defaultdict(list); reverse=0; total=0; edgeset=set()
    curb=collections.defaultdict(int); provenance=collections.Counter(); biggrade=0
    edgelist=[]
    for line in raw_edges.splitlines():
        if not line.strip():continue
        e=json.loads(line);u=e.get('from_node');v=e.get('to_node');length=e.get('length_m')
        if not isinstance(length,(float,int)) or not math.isfinite(length) or length<0:continue
        total+=1;edgeset.add((u,v));edgelist.append((u,v))
        directed[u].append((v,float(length)))
        if str(e.get('crossing_type','')).lower() in ('curb','curb_connector'):
            curb[u]+=1;curb[v]+=1
        slope=e.get('slope')
        if isinstance(slope,(int,float)) and abs(slope)>.12:biggrade+=1
        prov=(e.get('metadata') or {}).get('field_provenance') or {}
        for field in ('slope','width_m','cross_slope','step_free'):
            p=prov.get(field) or {}
            provenance[field+':'+str(p.get('kind','MISSING'))]+=1
    reverse=sum((v,u) in edgeset for u,v in edgelist)
    result={}
    for s in sites:
        nid=parse_node(s.get('physical_site_key'))
        result[s['site_id']]={'pedestrian_node':nid,'node_exists':nid in ids,'typed_curb_incident_count':curb[nid],
            'reality':'GRAPH_EVIDENCE_ONLY; physical pedestrian-to-vehicle transfer not audited'}
    if len(sites)==2:
        one,two=sites
        na=parse_node(one.get('physical_site_key'));nb=parse_node(two.get('physical_site_key'))
        if na in ids and nb in ids:
            result['_AB']={'directed_A_to_B_m':shortest(directed,na,nb),'directed_B_to_A_m':shortest(directed,nb,na)}
    return result,{'nodes':len(ids),'valid_edges':total,'edge_reverse_arc_fraction':reverse/max(1,total),
        'edge_large_abs_grade_over_0_12_fraction':biggrade/max(1,total),'attribute_provenance':dict(provenance)}


def audit(chain,graph_zip,output):
    evidence=json.loads(Path(chain).read_text());sites=evidence.get('sites',[])
    grouped=collections.defaultdict(list)
    for s in sites:grouped[s['episode_id']].append(s)
    agg=collections.Counter();ep_results=[];field_prov=collections.Counter()
    with zipfile.ZipFile(graph_zip) as z:
        names=set(z.namelist());manifest=json.loads(z.read('MANIFEST.json')) if 'MANIFEST.json' in names else {}
        hash_map=manifest.get('hashes',{})
        if not hash_map:raise ValueError('Graph archive lacks authenticated MANIFEST hashes')
        for ep,sub in sorted(grouped.items()):
            prefix='accessibility_graphs/'+ep
            ns=prefix+'.nodes.jsonl';es=prefix+'.edges.jsonl'
            if ns not in names or es not in names:
                agg['missing_episode_graph']+=1;continue
            nbytes=z.read(ns);ebytes=z.read(es)
            if any(hashlib.sha256(data).hexdigest()!=hash_map.get(name) for name,data in [(ns,nbytes),(es,ebytes)]):
                raise ValueError('Graph MANIFEST hash mismatch for '+ep)
            sr,gr=per_graph(nbytes,ebytes,sub)
            for site in sub:
                status=sr[site['site_id']]
                agg['sites']+=1;agg['site_node_exists']+=int(status['node_exists'])
                agg['site_typed_curb_incident']+=int(status['typed_curb_incident_count']>0)
                agg['independently_confirmed_legal_stops']+=int(site.get('real_legal_pudo_verified') is True)
                agg['independently_confirmed_boarding']+=int(site.get('real_boarding_verified') is True)
                agg['site_missing_lane_id']+=int(not site.get('has_explicit_lane_id'))
                agg['site_pose_coincident']+=int(site.get('curb_to_stop_pose_gap_m')==0)
            for k,v in gr['attribute_provenance'].items():field_prov[k]+=v
            ab=sr.get('_AB',{})
            if ab:
                agg['paired_graph_routes_computed']+=1
                agg['directed_A_to_B_reachable']+=int(ab.get('directed_A_to_B_m') is not None)
                agg['directed_B_to_A_reachable']+=int(ab.get('directed_B_to_A_m') is not None)
            ep_results.append({'episode_id':ep,'city':sub[0].get('city'),'sites':{k:v for k,v in sr.items() if k!='_AB'},'AB_routes':ab,'graph':{k:v for k,v in gr.items() if k!='attribute_provenance'}})
    res={'source_status':'REAL_GIS_GRAPH_WITH_HYBRID_ATTRIBUTES_NOT_SITE_CERTIFICATION',
         'summary':dict(agg),'field_provenance_counts':dict(field_prov),'episodes':ep_results,
         'limitations':['No independent street-level legal stop proof','No vehicle pose/door/ramp clearance verification',
         'Directed graph access is a path-model check, not actual mobility capability',
         'Simulated widths and slope derivatives do not certify passenger completion',
         'These episode pairs are risk-screened, not an unbiased prevalence denominator']}
    Path(output).parent.mkdir(parents=True,exist_ok=True)
    Path(output).write_text(json.dumps(res,ensure_ascii=False,indent=2)+'\n')
    return res


def main():
    p=argparse.ArgumentParser();p.add_argument('--evidence',required=True);p.add_argument('--graphs_zip',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();r=audit(a.evidence,a.graphs_zip,a.output);print(json.dumps(r['summary']))
if __name__=='__main__':main()
