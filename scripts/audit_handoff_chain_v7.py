#!/usr/bin/env python3
"""Evidence-chain audit on selected REAL nuPlan episodes + HYBRID GIS anchors.

This script does not derive physical curb truth from simulated labels or from
historical ego poses. It is intended for a selected, site-disjoint review slice.
"""
from __future__ import annotations
import argparse, collections, json, math
from pathlib import Path


def records(path):
    with Path(path).open(encoding='utf-8') as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def provenance_kind(anchor, field):
    d=(anchor.get('field_provenance') or {}).get(field) or {}
    return d.get('kind','unknown') if isinstance(d,dict) else 'unknown'


def distance(a,b):
    if not isinstance(a,dict) or not isinstance(b,dict):return math.inf
    try:return math.hypot(float(a['x'])-float(b['x']),float(a['y'])-float(b['y']))
    except (ValueError,TypeError,KeyError):return math.inf


def audit(sites_path, anchors_path, logs_path, calibration_path, output_path, physical_claims=None):
    sites=list(records(sites_path)); desired={(s['episode_id'],s['site_key']) for s in sites}
    best={};count_anchors=0
    sites_by_key={(s['episode_id'],s['site_key']):s for s in sites}
    # Do not load a >400 MB anchors file into memory. Tie to episode AND physical site.
    for anchor in records(anchors_path):
        count_anchors+=1
        k=(anchor.get('episode_id'),anchor.get('hybrid_physical_site_key'))
        if k not in desired:continue
        # Compare declared passenger/vehicle site proxy to anchor pose, not mere name.
        site=sites_by_key[k]
        gap=distance(site.get('pose'),anchor.get('stop_pose'))
        if k not in best or gap<best[k][0]:best[k]=(gap,anchor)
    logs=json.loads(Path(logs_path).read_text());cal=json.loads(Path(calibration_path).read_text())
    log_by_ep={e['episode_id']:e for e in logs.get('episodes',[])}
    reviewed={}; review_total=0
    if physical_claims:
        for row in records(physical_claims):
            review_total+=1
            # Claims are subject to independent review; user declarations alone are not proof.
            key=(row.get('city'),row.get('physical_site_key'),row.get('fact'))
            if row.get('value') is True and row.get('source_id') and row.get('evidence_uri') and row.get('reviewer_id') and not row.get('derived_from_hybrid_label'):
                reviewed[key]=row
    rows=[];counter=collections.Counter();by_city=collections.defaultdict(collections.Counter)
    for site in sites:
        city=site['city'];k=(site['episode_id'],site['site_key']);item=best.get(k)
        row={'site_id':site['site_id'],'episode_id':site['episode_id'],'city':city,
             'physical_site_key':site['site_key'],'hybrid_anchor_matched':item is not None,
             'site_pose_source':'UNVERIFIED_GIS_POSE_PROXY'}
        ep_log=log_by_ep.get(site['episode_id'],{})
        row['log_bound_exactly']=ep_log.get('status')=='EXACT_LIDAR_TOKEN_BOUND'
        near=next((v.get('nearby',{}).get('nearest') for v in ep_log.get('site_results',[]) if v.get('site_id')==site['site_id']),None)
        row['historical_ego_within_query_window']=near is not None
        if item:
            gap,a=item
            row['site_to_anchor_pose_gap_m']=round(gap,5) if math.isfinite(gap) else None
            row['curb_to_stop_pose_gap_m']=round(distance(a.get('curb_pose'),a.get('stop_pose')),5)
            row['has_explicit_lane_id']=a.get('lane_id') is not None
            row['hybrid_legal_label']=a.get('legal_stop')
            row['hybrid_interface_fields']={field:{'value':a.get(field),'provenance':provenance_kind(a,field)} for field in
                ('legal_stop','curb_ramp','curb_height_m','sidewalk_width_m','deployment_clearance_m','blockage_risk')}
            for field,v in row['hybrid_interface_fields'].items():
                counter[f'{field}_{v["provenance"]}']+=1
            if (row['curb_to_stop_pose_gap_m'] or 0)<.01:counter['curb_stop_coincident']+=1
            if not row['has_explicit_lane_id']:counter['no_lane_id']+=1
        facts=['sidewalk_to_curb_continuity','crossing_access','legal_stopping','deployment_region','stopping_pose']
        declared=[f for f in facts if (city,site['site_key'],f) in reviewed]
        row['submitted_external_positive_fact_count']=len(declared)
        row['external_review_status']='DECLARED_METADATA_REQUIRES_INDEPENDENT_INSPECTION' if len(declared)==len(facts) else 'MISSING_INDEPENDENT_PHYSICAL_FACTS'
        row['real_legal_pudo_verified']=False
        row['real_boarding_verified']=False
        rows.append(row)
        counter['sites']+=1;by_city[city]['sites']+=1
        if item:counter['anchor_matched']+=1;by_city[city]['anchor_matched']+=1
        if row['log_bound_exactly']:counter['exact_log_sites']+=1
        if near is not None:counter['historical_nearby_sites']+=1
    goal_candidates=cal.get('calibrated_goal_zone_candidates',[])
    stopped=sum(1 for c in goal_candidates if c.get('stopped_near_goal') is True)
    output={'summary':{'site_rows':len(rows),'total_anchor_rows_scanned':count_anchors,
                       'field_counts':dict(counter),'cities':{k:dict(v) for k,v in by_city.items()},
                       'carla_goal_zone_candidates':len(goal_candidates),'carla_stopped_candidates':stopped,
                       'review_claim_rows_submitted':review_total,'independently_confirmed_pudo_count':0,
                       'H1':'UNVERIFIED_PHYSICAL_INTERFACE','H2':'GRAPH_CONDITIONAL_ONLY',
                       'H3':'NOT_SHOWN_NECESSARY','next_experiment':'CARLA_SERVICE_STOP_NEGATIVE_CONTROL_AND_INDEPENDENT_CURB_AUDIT',
                       'cannot_estimate_natural_failure_prevalence':True},'sites':rows}
    out=Path(output_path);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(output['summary'],ensure_ascii=False))
    return output

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--sites',required=True);p.add_argument('--anchors',required=True)
    p.add_argument('--logs',required=True);p.add_argument('--calibration',required=True)
    p.add_argument('--physical_claims');p.add_argument('--output',required=True)
    a=p.parse_args();audit(a.sites,a.anchors,a.logs,a.calibration,a.output,a.physical_claims)
