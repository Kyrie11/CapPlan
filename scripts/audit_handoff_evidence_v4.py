#!/usr/bin/env python3
"""Field-level evidence audit, not a data-generator or verification oracle.

Keeps realistic topology, hybrid numerical labels, and independent physical
claims separate.  An observed source is not automatically a field-verified
vehicle/PUDO interface; provenance transfer is not an in-situ measurement.
"""
from __future__ import annotations
import argparse,json,math,sys
from collections import Counter,defaultdict
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from capplan.handoff.gate_evidence import records,city,site_key,dump_json

FIELDS=('legal_stop','legal_basis','curb_ramp','curb_height_m','sidewalk_width_m',
        'deployment_clearance_m','side','blockage_risk')

def source_kind(p):
    if not isinstance(p,dict):return 'missing'
    k=str(p.get('kind','unknown')).lower()
    method=str(p.get('method',''))
    if method=='same_physical_site_static_evidence_transfer':return 'derived_site_transfer'
    if k in ('observed','measured','field_verified'):return 'source_observed_not_site_audited'
    if k=='derived':return 'derived'
    if k in ('simulated','synthetic'):return 'simulated'
    return 'unknown'

def same_pose(a,b):
    if not isinstance(a,dict) or not isinstance(b,dict):return None
    try:
        return all(abs(float(a[k])-float(b[k]))<1e-7 for k in ('x','y','heading'))
    except (KeyError,TypeError,ValueError):return None

def analyze(pairs,output):
    total=0;seen=set();bycity=defaultdict(Counter);fields=defaultdict(Counter);risks=Counter();examples=[]
    for p in records(pairs):
        total+=1;c=city(p);bycity[c]['pair_rows']+=1
        sk=site_key(p)
        if sk:seen.add((c,tuple(sk)))
        connector=p.get('explicit_curb_connector_evidence') or {}
        pose=p.get('anchor_vehicle_poses') or {}
        for end,suffix in (('A','a'),('B','b')):
            if connector.get(end) is not True:
                risks['missing_typed_curb_connector_endpoints']+=1
                bycity[c]['missing_curb_endpoints']+=1
            interface=p.get('pudo_interface_'+suffix) or {}
            prov=interface.get('field_provenance') or {}
            for key in FIELDS:
                kind=source_kind(prov.get(key));fields[key][kind]+=1
                if kind in ('simulated','missing','unknown'):risks['unverified_'+key+'_endpoints']+=1
            eq=same_pose(pose.get(end+'_stop_pose'),pose.get(end+'_curb_pose'))
            if eq is True:risks['stop_pose_equals_curb_proxy_endpoints']+=1
            if eq is None:risks['stop_pose_comparison_unknown_endpoints']+=1
        if not p.get('a_to_b_vehicle_motion_packet'):
            risks['motion_packet_absent_pairs']+=1
        if len(examples)<12:examples.append({'episode_id':p.get('episode_id'),'city':c,
            'physical_site_pair':sk,'typed_curbs':connector,'motion_checked':bool(p.get('vehicle_reroute_checked'))})
    result={'pairs_examined':total,'independent_known_site_pairs':len(seen),
        'by_city':{k:dict(v) for k,v in bycity.items()},
        'field_source_classification_endpoints':{k:dict(v) for k,v in fields.items()},
        'risks':dict(risks),'sample':examples,
        'semantic_rules':{
          'missing_connector':'absence of edge in graph is UNKNOWN real-world connectivity, not observed infeasible',
          'pose_equality':'equal stop and curb poses could be a geometry proxy; requires independently verified deployment footprint and orientation',
          'site_transfer':'derived site transfer is not independent per-episode ground truth',
          'simulated':'usable for hybrid stress test only; never observed prevalence or empirical PUDO truth',
          'audited':'manual review with traceable source+timestamp+geometry must happen outside this analysis'},
        'physical_validated_cases':0,'incidence_claim_allowed':False}
    dump_json(output,result);return result

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--pairs',required=True);ap.add_argument('--output',required=True)
    x=ap.parse_args();s=analyze(x.pairs,x.output)
    print(json.dumps({'pairs':s['pairs_examined'],'risks':s['risks']},ensure_ascii=False))
