#!/usr/bin/env python3
"""City-balanced, physical-site-pair-level review queue. No sampled positive labels."""
from __future__ import annotations
import argparse,csv,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from capplan.handoff.gate_evidence import records,city,site_key,pair_key,fingerprint,evidence_risks,dump_json

COLUMNS=['review_id','city','episode_id','scenario_token','a_anchor','b_anchor',
         'site_a','site_b','distance_excess_m','risk_flags','review_decision',
         'curb_connector_checked','legal_stop_checked','vehicle_trajectory_checked',
         'boarding_interface_checked','passenger_route_checked','external_source_uri',
         'reviewer','reviewed_at','notes']


def select(pairs,output_dir,per_city=10):
    if per_city<=0:raise ValueError('per_city must be positive')
    dest=Path(output_dir);dest.mkdir(parents=True,exist_ok=True)
    # Deterministic best candidate for each *known* physical pair. Rows with
    # unknown keys are held separately and never counted as distinct sites.
    best={};unknown=0;total=0;source_cities=set()
    for p in records(pairs):
        total+=1;c=city(p);source_cities.add(c);k=site_key(p)
        if k is None:unknown+=1;continue
        risks=evidence_risks(p)
        # Evidence richness beats margin. Never rank by synthetic 'success'.
        score=(-len(risks),min(500,float(p.get('distance_excess_m',0))),fingerprint(p))
        key=(c,k)
        if key not in best or score>best[key][0]:best[key]=(score,p)
    by_city={c:[] for c in sorted(source_cities)}
    for (_, _),(_,p) in best.items():by_city[city(p)].append(p)
    out=[];site_usage=set()
    for c in sorted(by_city):
        chosen=0
        for p in sorted(by_city[c],key=lambda p:(len(evidence_risks(p)),
                      -min(500,float(p.get('distance_excess_m',0))),fingerprint(p))):
            sk=site_key(p)
            if any(s in site_usage for s in sk):continue
            out.append(p);site_usage.update(sk);chosen+=1
            if chosen>=per_city:break
    csvpath=dest/'site_review_queue.csv'
    # Never overwrite human-entered results. Reruns write proposals instead.
    if csvpath.exists():csvpath=dest/'site_review_queue_REGENERATED_UNREVIEWED.csv'
    with csvpath.open('w',encoding='utf-8',newline='') as f:
        wr=csv.DictWriter(f,fieldnames=COLUMNS);wr.writeheader()
        for p in out:
            sk=site_key(p)
            wr.writerow({'review_id':fingerprint(p),'city':city(p),'episode_id':p['episode_id'],
                        'scenario_token':p.get('scenario_token'),'a_anchor':p['a_anchor'],
                        'b_anchor':p['b_anchor'],'site_a':sk[0],'site_b':sk[1],
                        'distance_excess_m':p.get('distance_excess_m'),
                        'risk_flags':';'.join(evidence_risks(p)),
                        'review_decision':'PENDING',
                        **{k:'UNKNOWN' for k in ('curb_connector_checked','legal_stop_checked',
                           'vehicle_trajectory_checked','boarding_interface_checked',
                           'passenger_route_checked')}})
    file=dest/'selected_review_pairs.jsonl'
    file.write_text(''.join(json.dumps(p,ensure_ascii=False)+'\n' for p in out),encoding='utf-8')
    expected_cities=('boston','pittsburgh','singapore','vegas')
    counts={c:sum(city(p)==c for p in out) for c in sorted(source_cities|set(expected_cities))}
    manifest={'candidate_pairs_total':total,'unknown_identity_pairs_held':unknown,
              'known_independent_pair_candidates':len(best),'review_queue_size':len(out),
              'by_city':counts,'cities_without_selected_candidates':[c for c,n in counts.items() if n==0],
              'expected_cities_missing_from_input':[c for c in expected_cities if c not in source_cities],
              'min_site_pair_independence':'no physical site key shared among selected pairs',
              'selection':'deterministic stratified high-evidence convenience sample, NOT prevalence sampling',
              'positive_cases_confirmed':0,'review_file':str(csvpath),
              'no_external_validation_performed':True}
    dump_json(dest/'gate1_selection.json',manifest)
    return manifest

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--pairs',required=True)
    parser.add_argument('--output_dir',required=True);parser.add_argument('--per_city',type=int,default=10)
    args=parser.parse_args()
    print(json.dumps(select(args.pairs,args.output_dir,args.per_city),ensure_ascii=False))
