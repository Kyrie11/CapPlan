#!/usr/bin/env python3
"""Decision report: derive only evidence-limited H1/H2/H3 statements.

Never converts simulated tags, graph connectivity, or CARLA Town control
into an independently verified public PUDO or observed passenger failure.
"""
from __future__ import annotations
import argparse,csv,json,collections
from pathlib import Path

FACTS=('sidewalk_to_curb_continuity','crossing_access','legal_stopping','deployment_region','stopping_pose')

def report(chain_path,graph_path,carla_path,output_dir,site_locations=None):
    chain=json.loads(Path(chain_path).read_text());graph=json.loads(Path(graph_path).read_text());carla=json.loads(Path(carla_path).read_text())
    summary=graph['summary'];statuses=carla['summary'];independent_sites=summary.get('independently_confirmed_legal_stops',0)
    boarding=summary.get('independently_confirmed_boarding',0)
    total=summary.get('sites',0)
    if independent_sites==0 or boarding==0:
        h1='NOT_ESTABLISHED_PHYSICAL_PUDO_AND_BOARDING'
    else:h1='SOME_INDEPENDENT_EVIDENCE_PRESENT_BUT_FAILURE_AND_DENOMINATOR_REQUIRE_AUDIT'
    h2='GRAPH_TIME_SENSITIVITY_ONLY_NO_EXECUTED_PASSENGER_TRAJECTORIES'
    h3='NOT_ESTABLISHED_NO_FAIR_FULL_ACTION_BELIEF_BASELINES'
    paired_valid=statuses.get('RUNS_PRESENT_REQUIRES_INDEPENDENT_MOTION_SAFETY_AND_CURB_AUDIT',0)
    if paired_valid==0:carla_state='NO_VALID_MATCHED_CLEAR_BLOCK_REDIRECT_TRIPLET'
    else:carla_state='CONTROLLED_CARLA_EVIDENCE_PENDING_PUDO_AND_PASSENGER'
    verdict={'H1':h1,'H2':h2,'H3':h3,'carla_state':carla_state,
       'confirmed_real_site_count':independent_sites,'confirmed_boarding_count':boarding,
       'audited_site_anchors':total,'typed_curb_graph_incidents':summary.get('site_typed_curb_incident'),
       'episode_graph_AB_directed_reachable':summary.get('directed_A_to_B_reachable'),
       'status':'RESEARCH_GAP_HYPOTHESIS_NOT_YET_CONFIRMED',
       'next_branch':'VERIFY_REAL_CURB_STOP_POSE_AND_ACCESS_BEFORE_CAUSAL_SERVICE_CLAIMS',
       'scientific_stop_rules':{
        'many_screened_sites_rejected_by_external_stopping_evidence':'Abandon the screened A/B prevalence claim; rebuild from verified operational PUDOs',
        'alternative_PUDO_or_midroute_diversion_recovers_most':'Replace global stranded claim with time-varying handoff service cost and recoverability margin',
        'fixed_or_optimized_verify_then_guide_matches_joint_policy_at_equal_cost':'No joint-decision novelty demonstrated; pursue evidence or verified computational efficiency',
        'full_action_verified_failure_and_strong_baseline_gap':'Then develop recourse/observation-aware joint handoff problem and solver'}}
    out=Path(output_dir);out.mkdir(parents=True,exist_ok=True)
    (out/'research_verdict_v8.json').write_text(json.dumps(verdict,ensure_ascii=False,indent=2)+'\n')
    pose={}
    if site_locations:
        with Path(site_locations).open() as f:
            for l in f:
                if not l.strip():continue
                r=json.loads(l);key=(r.get('city'),r.get('site_key') or r.get('physical_site_key'))
                pose[key]=r.get('pose') or {}
    chosen={};missing=[]
    for s in chain.get('sites',[]):
        key=(s.get('city'),s.get('physical_site_key'))
        if key not in chosen:chosen[key]=s
    with (out/'physical_site_audit_queue_v8.csv').open('w',newline='',encoding='utf-8') as f:
        fields=['city','physical_site_key','example_site_id','fact','value','reviewer_id','source_id','source_kind',
                'checked_at_utc','evidence_uri','site_x','site_y','derived_from_hybrid_label']
        w=csv.DictWriter(f,fields);w.writeheader()
        for (city,key),s in sorted(chosen.items()):
            loc=pose.get((city,key),{})
            for fact in FACTS:
                w.writerow({'city':city,'physical_site_key':key,'example_site_id':s['site_id'],
                            'fact':fact,'value':'unknown','reviewer_id':'','source_id':'','source_kind':'',
                            'checked_at_utc':'','evidence_uri':'','site_x':loc.get('x',''),
                            'site_y':loc.get('y',''),'derived_from_hybrid_label':False})
    verdict['unique_site_keys_to_review']=len(chosen)
    (out/'research_verdict_v8.json').write_text(json.dumps(verdict,ensure_ascii=False,indent=2)+'\n')
    return verdict


def main():
    p=argparse.ArgumentParser();p.add_argument('--evidence',required=True);p.add_argument('--graph_report',required=True)
    p.add_argument('--carla_report',required=True);p.add_argument('--site_locations_jsonl');p.add_argument('--output_dir',required=True)
    a=p.parse_args();v=report(a.evidence,a.graph_report,a.carla_report,a.output_dir,a.site_locations_jsonl)
    print(json.dumps({k:v[k] for k in ('H1','H2','H3','carla_state','unique_site_keys_to_review')},ensure_ascii=False))
if __name__=='__main__':main()
