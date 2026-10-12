#!/usr/bin/env python3
"""Produce denominator-preserving motivation funnel / human audit packets.

No negative result is disguised as a completed vehicle-motion check.
"""
from __future__ import annotations
import argparse
from collections import Counter,defaultdict
from pathlib import Path
import csv,json,math


def load(path):
    if not path.exists():return []
    return [json.loads(t) for t in path.read_text(encoding='utf-8').splitlines() if t.strip()]


def summarize(pairs_path, audit_path, out_path, review_top=30):
    pairs=load(Path(pairs_path))
    audit=json.loads(Path(audit_path).read_text(encoding='utf-8'))
    counts=Counter(audit.get('counts',{}))
    counts['screened_A_B_pairs']=len(pairs)
    counts['vehicle_verified_pairs']=sum(bool(p.get('two_candidate_distance_and_vehicle_witness')) for p in pairs)
    counts['not_vehicle_verified_pairs']=len(pairs)-counts['vehicle_verified_pairs']
    unique_episodes=len({x['episode_id'] for x in pairs})
    unique_sites=len({str(p.get('physical_site_pair')) for p in pairs if p.get('physical_site_pair')})
    by_city=defaultdict(Counter)
    interface_truth_modes=Counter()
    pedestrian_edge_sources=Counter()
    synthetic_or_unknown_evidence_pairs=0
    for p in pairs:
        a=p.get('pudo_interface_a',{})
        b=p.get('pudo_interface_b',{})
        mode_pair=(str(a.get('truth_mode','unknown')),str(b.get('truth_mode','unknown')))
        interface_truth_modes[' / '.join(mode_pair)]+=1
        for path in p.get('paths',{}).values():
            for edge in path.get('edges',[]):
                pedestrian_edge_sources[str(edge.get('source') or 'unknown')]+=1
        evidence=json.dumps([a,b,p.get('paths',{})],default=str).lower()
        synthetic_or_unknown_evidence_pairs+=int(
            any(token in evidence for token in ('synthetic','simulat','unknown','proxy')))
        c=p.get('map_name') or 'unknown' 
        by_city[c]['screened_pairs']+=1
        by_city[c]['vehicle_verified_pairs']+=bool(p.get('two_candidate_distance_and_vehicle_witness'))
    report={
        'claim_status':('vehicle_checked_witnesses_present' if counts['vehicle_verified_pairs'] else
                        'geometric_necessary_conditions_only'),
        'interpretation':'conditioned on selected graph episodes/profiles, NOT natural event frequency; finite two-anchor witness NOT global no-recourse',
        'denominators':dict(counts), 'screened_unique_episodes':unique_episodes,
        'known_distinct_site_pairs':unique_sites, 'by_city':{k:dict(v) for k,v in by_city.items()},
        'construction_parameters':audit.get('parameters',{}),'errors':audit.get('errors',[]),
        'pudo_interface_truth_mode_pair_counts':dict(interface_truth_modes),
        'path_edge_source_occurrences':dict(pedestrian_edge_sources),
        'pairs_with_any_synthetic_simulated_or_unknown_evidence_marker':synthetic_or_unknown_evidence_pairs,
        'source_count_semantics':'counts of path edge occurrences, not audited independent physical sites',
        'unresolved':['missing independent vehicle motion packets', 'physical site and curb-interface field audit',
                      'static/dynamic evidence provenance', 'human review of map and stopping legality',
                      'actual boarding and confirmation evidence', 'full action-space recourse analysis']
    }
    out=Path(out_path);out.mkdir(parents=True,exist_ok=True)
    (out/'funnel_report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    # Audit high-contrast cases, not a prevalence-estimation sample.
    eligible=sorted(pairs,key=lambda r:(not r.get('two_candidate_distance_and_vehicle_witness',False),
                                        -r.get('distance_excess_m',0),r['episode_id']))[:review_top]
    # Human review is expensive primary evidence. Rebuilding a funnel must not
    # silently erase independently entered decisions or audit notes.
    review_file=out/'human_review_queue.csv'
    previous={}
    if review_file.exists():
        with review_file.open(encoding='utf-8',newline='') as old:
            for record in csv.DictReader(old):
                key=(record.get('episode_id'),record.get('a_anchor'),record.get('b_anchor'))
                previous[key]=record
    with review_file.open('w',encoding='utf-8',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=['episode_id','map_name','scenario_token','a_anchor','b_anchor',
                     'physical_site_pair','distance_excess_m','vehicle_motion_verified','static_geometry_checked',
                     'curb_legality_checked','vehicle_stop_window_checked','interface_deployment_checked',
                     'passenger_route_checked','source_provenance_checked','expert_reviewer','decision','notes'])
        writer.writeheader()
        for r in eligible:
            current={**{k:r.get(k) for k in ('episode_id','map_name','scenario_token','a_anchor','b_anchor','physical_site_pair','distance_excess_m')},
                    'vehicle_motion_verified':bool(r.get('two_candidate_distance_and_vehicle_witness')),
                    'decision':'PENDING_REVIEW'}
            key=(r.get('episode_id'),r.get('a_anchor'),r.get('b_anchor'))
            historical=previous.get(key, {})
            for name in writer.fieldnames:
                if name not in ('episode_id','a_anchor','b_anchor','map_name',
                                'scenario_token','physical_site_pair','distance_excess_m',
                                'vehicle_motion_verified') and historical.get(name):
                    current[name]=historical[name]
            writer.writerow(current)
    print(json.dumps({'geometric_pairs':len(pairs),'vehicle_verified':counts['vehicle_verified_pairs'],
                      'human_review_queue':len(eligible)},ensure_ascii=False))
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--pairs',required=True);p.add_argument('--mining_audit',required=True)
    p.add_argument('--output_dir',required=True);p.add_argument('--review_top',type=int,default=30)
    args=p.parse_args();summarize(args.pairs,args.mining_audit,args.output_dir,args.review_top)
