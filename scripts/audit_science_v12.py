#!/usr/bin/env python3
"""Science V12: denominator/evidence-bottleneck audit for mined all-PUDO V11 rows.

This is NOT a physical infeasibility or population-incidence estimator.
It reports the dependency and missingness structure that is otherwise hidden by
512 repeated episode-time observations. It also inventories historical QA
reports separately, never equating bootstrap QA to hybrid QA.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
from statistics import mean


def read_jsonl(path):
    with Path(path).open(encoding='utf8') as f:
        for line in f:
            if line.strip():yield json.loads(line)


def audit(rows, reports_dir=None):
    rows=list(rows)
    if not rows:raise ValueError('Empty V11 alternatives input')
    by_episode=defaultdict(list);by_pair=defaultdict(list);by_site=defaultdict(list)
    reasons=Counter(); opt_sizes=[]; strict_sizes=[]; cities=Counter()
    for row in rows:
        episode=str(row['episode_id'])
        sites=row.get('physical_site_pair') or []
        if not isinstance(sites,list) or len(sites)!=2:
            raise ValueError('Physical site pair missing or malformed')
        pair=(episode, tuple(sorted(map(str,sites))))
        by_episode[episode].append(row);by_pair[pair].append(row)
        for s in sites:by_site[str(s)].append(row)
        cities[str(sites[0]).split('|')[0]]+=1
        sr=row['strict'];op=row['optimistic']
        strict_sizes.append(int(sr['graph_candidate_count']))
        opt_sizes.append(int(op['graph_candidate_count']))
        reasons.update(sr.get('filter_reasons',{}))
        if int(sr['graph_candidate_count'])!=len(sr['anchors']):
            raise ValueError('Strict candidate count inconsistent with exported anchors')
        if int(op['graph_candidate_count'])!=len(op['anchors']):
            raise ValueError('Optimistic candidate count inconsistent with exported anchors')
    all_optimistic=sum(v>0 for v in opt_sizes);all_strict=sum(v>0 for v in strict_sizes)
    ep_details=[]
    for ep,rr in sorted(by_episode.items()):
        pairs={(str(r['a_anchor']),str(r['b_anchor'])) for r in rr}
        sp={tuple(sorted(map(str,r['physical_site_pair']))) for r in rr}
        ep_details.append({'episode_id':ep,'city':rr[0]['physical_site_pair'][0].split('|')[0],
            'time_rows':len(rr),'mined_ab_pairs':len(pairs),'physical_site_pairs':len(sp),
            'strict_any_rate':sum(x['strict']['graph_candidate_count']>0 for x in rr)/len(rr),
            'optimistic_any_rate':sum(x['optimistic']['graph_candidate_count']>0 for x in rr)/len(rr)})
    # Do not report pseudo-binomial CI treating correlated mined rows as iid.
    counts={
        'number_time_rows':len(rows), 'independent_nuplan_episodes':len(by_episode),
        'mined_ab_pair_identities':len({(x['episode_id'],x['a_anchor'],x['b_anchor']) for x in rows}),
        'episode_and_physical_site_pair_clusters':len(by_pair),
        'unique_physical_site_keys':len(by_site),
        'graph_strict_any_count':all_strict, 'graph_optimistic_any_count':all_optimistic,
        'strict_curb_candidate_absence_count':len(rows)-all_strict,
        'strict_zero_with_optimistic_positive':sum(a==0 and b>0 for a,b in zip(strict_sizes,opt_sizes)),
        'max_episode_fraction_of_time_rows':max(len(v) for v in by_episode.values())/len(rows),
        'mean_optimistic_options_per_row':round(mean(opt_sizes),3),
        'distinct_notice_times':sorted({r['notice_issued_at_s'] for r in rows}),
    }
    reports=[]
    if reports_dir:
        root=Path(reports_dir)
        for path in sorted(root.glob('build/*/dataset_quality.*.json')):
            try:
                j=json.loads(path.read_text(encoding='utf8'))
            except (ValueError,OSError):continue
            reports.append({'path':str(path.relative_to(root)),
                'episode_count':j.get('counts',{}).get('episodes'),
                'paper_mode':j.get('manifest',{}).get('paper_mode'),
                'source_policy':j.get('manifest',{}).get('source_policy'),
                'publication_status':j.get('publication_readiness',{}).get('status'),
                'publication_ready':j.get('publication_readiness',{}).get('ready_for_main_results'),
                'paper_eligible_total':j.get('pudo_evidence_readiness',{}).get('paper_eligible_total'),
                'skeletons':j.get('counts',{}).get('skeleton_labels')})
    return {'schema':'capplan_science_v12_evidence_audit', 'counts':counts,
       'episodes':ep_details,'cities_time_rows':dict(cities),
       'strict_filter_reason_totals':dict(reasons),
       'reported_qa_snapshot_inventory_not_full_dataset_census':reports,
       'claim_gates':{
          'true_physical_handoff_infeasibility':'NOT_ESTABLISHED',
          'site_physical_legal_stop_verification':'NOT_ESTABLISHED',
          'natural_incidence':'NOT_ESTABLISHED_MINED_SELECTION',
          'strict_missing_connector_is_physical_blockage':False,
          'graph_screening_mismatch':'OBSERVED_IN_FINITE_GIS_SUBSTRATE',
          'observational_unit_for_field_statistics':'INDEPENDENT_SITE_AND_EPISODE_NOT_TIME_ROW',
          'strict_candidate_failure_means':'GRAPH_PROOF_ABSENT_PHYSICAL_TRUTH_UNKNOWN'},
       'interpretation':'No valid confidence interval for population prevalence: sample is mined and episode-clustered. Independent external motion/interface audits are required.'}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--all_existing',required=True)
    p.add_argument('--reports_dir')
    p.add_argument('--output',required=True)
    a=p.parse_args()
    s=audit(read_jsonl(a.all_existing),a.reports_dir)
    out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(s,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps(s['counts'],ensure_ascii=False))

if __name__=='__main__':main()
