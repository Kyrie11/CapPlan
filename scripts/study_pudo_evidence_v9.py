#!/usr/bin/env python3
"""Science V9 research gate. Reports what was observed; never fabricates PUDO facts.

Uses source leads (official geospatial proximity), optional manually submitted review
metadata and CARLA paired runs. A source record is not a verified physical interface.
"""
from __future__ import annotations
import argparse,csv,json,sys
from collections import Counter,defaultdict
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.pudo_evidence_v9 import read_jsonl,write_json
from scripts.audit_handoff_physical_v5 import audit,read_rows

FACTS=('sidewalk_to_curb_continuity','crossing_access','legal_stopping','deployment_region','stopping_pose')

def analyze(sites_path,leads_path,output,review_path=None,carla_summary_path=None):
 sites=list(read_jsonl(sites_path));leads=list(read_jsonl(leads_path))
 distinct={(s['city'],s['physical_site_key']) for s in sites}
 source_sites={(r['city'],r['site_key']) for r in leads if r.get('matched_feature_count',0)>0}
 total_by_city=Counter(s['city'] for s in sites)
 lead_city=Counter(city for city,key in source_sites)
 classified=Counter(r.get('source_kind','unknown') for r in leads if r.get('matched_feature_count',0))
 manual={'supplied':False,'eligible_positive_site_claims':0,'independently_confirmed_sites':0}
 if review_path:
  review=audit(read_rows(review_path));manual={'supplied':True,
   'eligible_positive_site_claims':review['metadata_complete_positive_sites'],
   'independently_confirmed_sites':0,
   'warning':'V5 audit does NOT examine photo/legality claim semantics; complete fields remain claims pending human review',
   'quarantined_claims':review['counts'].get('quarantined',0)}
 carla={'status':'NOT_SUBMITTED','valid_controlled_vehicle_success':False}
 if carla_summary_path:
  pairs=json.loads(Path(carla_summary_path).read_text()).get('trials',[])
  statuses=Counter(t.get('status','UNKNOWN') for t in pairs)
  clear_stops=sum(t.get('runs',{}).get('clear_A',{}).get('stop_with_dwell') is True for t in pairs)
  carla={'status':'CONTROLLED_TOWN_STUDY_NOT_REAL_NUPLAN_PUDO',
   'trials':len(pairs),'control_stops':clear_stops,'statuses':dict(statuses),
   'valid_controlled_vehicle_success':clear_stops>0,
   'not_physical_truth':'TownXX goal stop cannot certify an actual nuPlan city curb boarding pose'}
 checks={
  'H1':'PHYSICAL_EXECUTABLE_PUDO_NOT_ESTABLISHED' if not manual['independently_confirmed_sites'] else 'FURTHER_INDEPENDENT_VERIFICATION_REQUIRED',
  'H2':'ONLY_HYBRID_GRAPH_PROGRESS_COUNTERFACTUAL; need actual state and communication chronology',
  'H3':'NOT_ESTABLISHED; need optimized verify-then-guide and equal-action belief/shield baselines',
  'research_branch': 'REAL_PUDO_EVIDENCE_RECONSTRUCTION_AND_BLIND_NEGATIVE_CONTROLS',
  'cannot_infer_natural_prevalence':True,
  'success_conditions':{
   'H1':'independent same-site pedestrian+curb+legality+vehicle pose/dwell with negative controls',
   'H2':'same physically reviewed sites, matched vehicle time, observed or validated passenger progress and notification latency',
   'H3':'both H1 and H2 plausible plus same-information, same-action optimized strong baselines; measure false commitments, coverage and effort'}}
 report={'version':'science_v9','site_count':len(distinct),'sites_by_city':dict(total_by_city),
  'sites_with_external_spatial_leads':len(source_sites),
  'sites_with_leads_by_city':dict(lead_city),'spatial_lead_kinds':dict(classified),
  'independent_review':manual,'carla':carla,'hypotheses':checks,
  'evidence_ladder':{
   'L0':'hybrid nuPlan+GIS+simulated fields; already available',
   'L1':'official GIS candidate proximity; not validated curb connector',
   'L2':'independent audited physical/legal PUDO; unavailable unless externally substantiated',
   'L3':'matched vehicle stop pose plus time window; no current nuPlan city proof',
   'L4':'passenger functional capability and actual progress linked to timing; unavailable',
   'L5':'fair full-action decision/policy comparison and independent prevalence sampling; unavailable'}}
 write_json(output,report)
 return report

def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--sites',required=True);p.add_argument('--leads',required=True);p.add_argument('--output',required=True)
 p.add_argument('--review_claims');p.add_argument('--carla_paired_summary')
 a=p.parse_args();r=analyze(a.sites,a.leads,a.output,a.review_claims,a.carla_paired_summary)
 print(json.dumps({'sites':r['site_count'],'spatial_lead_sites':r['sites_with_external_spatial_leads'],
  'hypotheses':r['hypotheses']},indent=2))
if __name__=='__main__':main()
