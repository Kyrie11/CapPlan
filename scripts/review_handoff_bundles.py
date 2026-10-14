#!/usr/bin/env python3
"""Reproduce the seven-step motivation evidence funnel from *result bundles*.

Works without a local nuPlan/GIS DB. The original funnel denominator and
assumptions are retained. This is an evidence AUDIT, not a replacement for
trajectory checking or independent PUDO observation.
"""
from __future__ import annotations
import argparse, csv, hashlib, json, math, zipfile
from collections import Counter, defaultdict
from pathlib import Path


def _json_from(source, name):
    if zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as f:
            return json.loads(f.read(name))
    return json.loads((source/name).read_text(encoding='utf-8'))


def _lines(source,name):
    if zipfile.is_zipfile(source):
        with zipfile.ZipFile(source) as f:
            if name not in f.namelist(): return
            with f.open(name) as handle:
                for line in handle:
                    if line.strip(): yield json.loads(line)
    else:
        p=source/name
        if p.exists():
            with p.open(encoding='utf-8') as handle:
                for line in handle:
                    if line.strip(): yield json.loads(line)


def _stable_key(p):
    sites=p.get('physical_site_pair')
    if isinstance(sites, list) and len(sites)==2 and all(sites):
        return tuple(sorted(map(str, sites)))
    return ('UNKNOWN:'+str(p.get('episode_id')), str(p.get('a_anchor'))+':'+str(p.get('b_anchor')))


def _priority(p):
    # Favor semantically stronger evidence before a visually large margin.
    connector=p.get('explicit_curb_connector_evidence') or {}
    return (int(bool(p.get('two_candidate_distance_and_vehicle_witness'))),
            int(bool(connector.get('A')) and bool(connector.get('B'))),
            int(bool(p.get('interface_completeness_claim'))),
            min(float(p.get('distance_excess_m',0)),1000),
            -int(hashlib.sha256(str(p.get('episode_id')).encode()).hexdigest()[:8],16))


def analyze(strict,relaxed,output,max_per_city=12,max_per_site=1):
    out=Path(output);out.mkdir(parents=True,exist_ok=True)
    reports={}
    selected_by_city=defaultdict(list)
    # Preserve one best episode from each distinct physical-site pair.
    best_by_site={};seen_site_pairs=Counter();joint=Counter();by_city=defaultdict(Counter)
    for label,source in [('strict',Path(strict)),('relaxed',Path(relaxed))]:
        a=_json_from(source,'mining/mining_audit.json')
        f=_json_from(source,'audit/funnel_report.json')
        pol=_json_from(source,'policies/policy_summary.json')
        gate=(_json_from(source,'research_gate.json') if label=='strict' else None)
        try: bundle_manifest=_json_from(source,'BUNDLE_MANIFEST.json')
        except (FileNotFoundError, KeyError): bundle_manifest=None
        reports[label]={'bundle_manifest':bundle_manifest, 'sampled_episodes':a.get('selected_episodes'),
                        'counts':a.get('counts',{}), 'audited_counts':f.get('denominators',{}),
                        'policy_summary':pol.get('methods',{}), 'research_gate':gate,
                        'errors':a.get('errors',[])}
        if label=='relaxed':
            for p in _lines(source,'mining/recourse_pairs.jsonl'):
                city=p.get('map_name') or 'UNKNOWN'
                k=_stable_key(p)
                seen_site_pairs[k]+=1
                c=p.get('explicit_curb_connector_evidence') or {}
                typed=bool(c.get('A')) and bool(c.get('B'))
                motion=bool(p.get('two_candidate_distance_and_vehicle_witness'))
                joint[(city,'pair')]+=1; joint[(city,'typed_both')]+=int(typed)
                joint[(city,'motion')]+=int(motion)
                joint[(city,'independent_interface')]+=int('simulat' not in
                     json.dumps([p.get('pudo_interface_a'),p.get('pudo_interface_b')]).lower())
                by_city[city]['pairs']+=1
                if k not in best_by_site or _priority(p)>_priority(best_by_site[k]):
                    best_by_site[k]=p
    for p in best_by_site.values():
        selected_by_city[p.get('map_name') or 'UNKNOWN'].append(p)
    review=[]
    for city in sorted(selected_by_city):
        sorted_candidates=sorted(selected_by_city[city],key=_priority,reverse=True)
        per_single_site=Counter()
        for p in sorted_candidates:
            sites=_stable_key(p)
            if any(per_single_site[x]>=max_per_site for x in sites):continue
            for x in sites:per_single_site[x]+=1
            review.append(p)
            if len([x for x in review if x.get('map_name')==city])>=max_per_city:break
    with (out/'diverse_motion_and_review_queue.csv').open('w',encoding='utf-8',newline='') as handle:
        cols=['city','episode_id','scenario_token','from_anchor','to_anchor','site_a','site_b','distance_excess_m',
              'typed_curb_a','typed_curb_b','interface_complete_fields','method_specific_motion_verified',
              'evidence_category','review_decision','curb_connector_checked','legal_stop_checked',
              'vehicle_trajectory_checked','boarding_interface_checked','external_source_uri','reviewer','notes']
        writer=csv.DictWriter(handle,fieldnames=cols);writer.writeheader()
        for p in review:
            sites=p.get('physical_site_pair') or [None,None]
            curb=p.get('explicit_curb_connector_evidence') or {}
            writer.writerow({'city':p.get('map_name'),'episode_id':p['episode_id'],
              'scenario_token':p.get('scenario_token'),'from_anchor':p['a_anchor'],'to_anchor':p['b_anchor'],
              'site_a':sites[0],'site_b':sites[1], 'distance_excess_m':round(p.get('distance_excess_m',0),2),
              'typed_curb_a':curb.get('A',False),'typed_curb_b':curb.get('B',False),
              'interface_complete_fields':p.get('interface_completeness_claim'),
              'method_specific_motion_verified':p.get('two_candidate_distance_and_vehicle_witness'),
              'evidence_category':'DISTANCE_ONLY_NOT_OBSERVED_EVENT', 'review_decision':'PENDING',
              'curb_connector_checked':'UNKNOWN','legal_stop_checked':'UNKNOWN',
              'vehicle_trajectory_checked':'UNKNOWN','boarding_interface_checked':'UNKNOWN'})
    s=reports['strict']['counts']; r=reports['relaxed']['counts']; m=reports['relaxed']['policy_summary']
    # A full path to the claim can only become valid with external evidence.
    stages=[
      ('1_geometric_counterexample',r.get('two_anchor_distance_asymmetry',0)>0,
       r.get('two_anchor_distance_asymmetry',0),'necessary distance asymmetry; not event prevalence'),
      ('2_typed_curb_and_interface',s.get('episodes_with_candidates',0)>0,
       s.get('episodes_with_candidates',0),'strict screening currently cannot establish qualified A/B pairs'),
      ('3_method_specific_motion',r.get('motion_time_window_verified',0)>0,
       r.get('motion_time_window_verified',0),'no method-specific verified A-to-B trajectories'),
      ('4_independent_human_review',False,0,'no completed independently reviewed sites supplied'),
      ('5_exact_finite_oracle',m.get('exact_finite_support',{}).get('n',0)>0,
       m.get('exact_finite_support',{}).get('n',0),'two-anchor model only; not full mobility recourse'),
      ('6_strong_baseline_distinction',False,0,'generic shield is proxy; fixed B has guaranteed success by construction'),
      ('7_final_scientific_evidence',False,0,'requires integrated motion, audited curb and causal handoff verification')]
    sampled_pairs=sum(seen_site_pairs.values())
    truncated=bool((reports['relaxed'].get('bundle_manifest') or {}).get('files',{}).get('mining/recourse_pairs.jsonl',{}).get('truncated'))
    result={'status':'HOLD_EXTERNAL_EVIDENCE_AND_STRONG_BASELINE',
            'inputs':{'strict':str(strict),'relaxed':str(relaxed)},'reports':reports,
            'stages':[{'stage':t,'screen_pass':ok,'n':count,'interpretation':desc} for t,ok,count,desc in stages],
            'distinct_physical_site_pairs_in_bundled_examples_only':len(best_by_site),
            'bundled_example_rows':sampled_pairs,
            'pairs_file_truncated':truncated,
            'max_reuse_of_same_pair':max(seen_site_pairs.values(),default=0),
            'per_city':{k:dict(v) for k,v in by_city.items()},
            'review_queue_size':len(review),
            'method_identifiability_warning':'B always available in the original two-world model, so fixed B succeeds in all worlds; 100% oracle vs 100% fixed B is forced by construction.',
            'population_prevalence_estimation_allowed':False,
            'sample_site_distribution_not_representative':truncated,
            'next':'Run strict connector audit on original dataset, then independent motion/PUDO verification, then two-sided falsification control.'}
    (out/'evidence_funnel.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    txt=['# Handoff evidence gate (bundled results only)','',
         '**Current disposition: HOLD.** No vehicle-verified or independent PUDO-confirmed motivating case.',
         '', '| Stage | Result | Count | Boundary |','|---|---|---:|---|']
    for t,ok,count,desc in stages:
        txt.append(f'| {t} | {"screen passed" if ok else "not established"} | {count:,} | {desc} |')
    txt += ['',f'Physical site pairs **only among {sampled_pairs:,} bundled examples**: {len(best_by_site):,}; '
            f'max repeated uses of one pair: {max(seen_site_pairs.values(),default=0):,}.',
            f'Site-diverse review targets: {len(review)} (not independent observed positive events).',
            '', '## Critical falsification',
            'The original controlled worlds make anchor B permanently usable and directly reachable. '
            'Thus a fixed-B policy is guaranteed to succeed, making an apparent 100% exact-oracle result '
            'a property of the scenario constructor, not evidence that a new handoff planner is necessary.',
            '', '## Required next data',
            'Typed curb topology checked against actual GIS; independent PUDO access/interface review; '
            'nuPlan method-specific motion packets at sidecar event times; non-clairvoyant two-sided '
            'dynamic events; empirical profiles only after task-specific consented acquisition.']
    (out/'evidence_funnel.md').write_text('\n'.join(txt)+'\n',encoding='utf-8')
    return result


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--strict',required=True);p.add_argument('--relaxed',required=True)
    p.add_argument('--output_dir',required=True)
    p.add_argument('--max_per_city',type=int,default=12)
    p.add_argument('--max_per_site',type=int,default=1)
    a=p.parse_args()
    d=analyze(a.strict,a.relaxed,a.output_dir,a.max_per_city,a.max_per_site)
    print(json.dumps({'status':d['status'],'queue':d['review_queue_size'],
                     'site_pairs_in_bundled_examples_only':d['distinct_physical_site_pairs_in_bundled_examples_only']},ensure_ascii=False))

if __name__=='__main__':main()
