#!/usr/bin/env python3
"""Evidence-bound H1 field-audit gate (never silently turns unknown into FAIL).

Input: V9/V10 dual graph rows, V10 physical audit queue, and OPTIONAL independently
completed site review records. The default output is UNKNOWN for physical
handoffs. This is a sample CONDITIONAL ON MINED PAIRS, not an incidence study.

Optional review JSONL schema (one record per anchor_id and physical_site_key):
  {"anchor_id":"...", "physical_site_key":"...", "checks": {
    "pedestrian_route":{"result":"PASS","source":"review-id","checked_at_utc":"..."},
    ...all other REQUIRED_CHECKS...
  }}
Result FAIL/PASS requires external independently verified evidence for that
particular site/time/vehicle (nonempty source and checked_at_utc). The review
record is an audit assertion; it is not automatically proven by this tool.
"""
from __future__ import annotations

import argparse
from collections import defaultdict, Counter
import json
from pathlib import Path

REQUIRED_CHECKS = (
    'passenger_route_and_remaining_budget',
    'directed_curb_connector',
    'legal_stop_and_pose',
    'continuous_vehicle_motion',
    'vehicle_interface_and_deployment',
    'whole_boarding_dwell_window',
    'timestamped_evidence_validity',
    'notice_ack_and_actual_progress',
)


def canonical_pair(row):
    val = row.get('physical_site_pair')
    # The original V9 export may encode physical sites as a list or repr string.
    if isinstance(val, list):
        return tuple(sorted(str(x) for x in val))
    return (str(val),)


def review_status(review):
    if review is None:
        return 'UNKNOWN'
    checks = review.get('checks', {})
    observed = []
    for key in REQUIRED_CHECKS:
        entry = checks.get(key)
        if (not isinstance(entry, dict) or entry.get('result') not in ('PASS', 'FAIL')
                or not isinstance(entry.get('source'), str) or not entry['source'].strip()
                or not isinstance(entry.get('checked_at_utc'), str) or not entry['checked_at_utc'].strip()):
            observed.append(None)
        else:
            observed.append(entry['result'])
    if 'FAIL' in observed: return 'AUDITED_OPTION_FAIL'
    if all(v == 'PASS' for v in observed): return 'AUDITED_OPTION_PASS_CONDITIONAL'
    return 'UNKNOWN'


def audit_graph(rows, queue=None, reviews=None):
    queue = queue or []
    reviews = reviews or []
    index = defaultdict(list)
    for record in reviews:
        if record.get('anchor_id') and record.get('physical_site_key'):
            index[(record['anchor_id'], record['physical_site_key'])].append(record)
    groups = defaultdict(list)
    for row in rows:
        key = (str(row['episode_id']), canonical_pair(row))
        groups[key].append(row)
    queue_by_ep = defaultdict(list)
    for packet in queue: queue_by_ep[str(packet.get('episode_id'))].append(packet)
    result=[]
    for (episode, site_pair), members in sorted(groups.items()):
        graph_strict = any(int(r.get('strict_explicit_curb_count', 0)) > 0 for r in members)
        graph_optimistic = any(int(r.get('optimistic_without_curb_count', 0)) > 0 for r in members)
        sampled_anchor_count = sum(len(r.get('optimistic_unverified_examples', [])) for r in members)
        review_results=[]
        for packet in queue_by_ep.get(episode, []):
            if canonical_pair(packet) != site_pair and str(packet.get('physical_site_pair')) != str(members[0].get('physical_site_pair')):
                continue
            for cand in packet.get('candidate_review_queue', []):
                key=(cand.get('anchor_id'),cand.get('physical_site_key'))
                matched=index.get(key, [])
                if len(matched)>1:
                    # Contradictory or duplicated independent audits cannot be resolved by guessing.
                    s='UNKNOWN_DUPLICATE_REVIEW'
                else:s=review_status(matched[0] if matched else None)
                review_results.append({'anchor_id':key[0], 'physical_site_key':key[1], 'status':s})
        result.append({
            'episode_id':episode, 'physical_site_pair':site_pair,
            'number_notice_times':len({r.get('notice_issued_at_s') for r in members}),
            'strict_graph_some_candidate':graph_strict,
            'optimistic_graph_some_candidate':graph_optimistic,
            'optimistic_only':graph_optimistic and not graph_strict,
            'exported_candidates_may_be_truncated':True,
            'sampled_example_entries_across_times':sampled_anchor_count,
            'reviewed_candidates':review_results,
            'some_audited_option_pass':any(x['status']=='AUDITED_OPTION_PASS_CONDITIONAL' for x in review_results),
            'global_no_recovery_proved':False,  # no exhaustive physical/motion policy oracle in V10
            'real_handoff_claim': 'AUDITED_OPTION_PASS_CONDITIONAL' if any(
                x['status']=='AUDITED_OPTION_PASS_CONDITIONAL' for x in review_results) else 'UNKNOWN',
        })
    counts=Counter()
    for row in result:
        counts['mined_site_pairs']+=1
        counts['site_pairs_with_strict_graph_candidate']+=bool(row['strict_graph_some_candidate'])
        counts['site_pairs_with_optimistic_graph_candidate']+=bool(row['optimistic_graph_some_candidate'])
        counts['site_pairs_optimistic_only']+=bool(row['optimistic_only'])
        counts['site_pairs_with_conditional_audited_pass']+=bool(row['some_audited_option_pass'])
        counts['reviewed_candidate_entries']+=len(row['reviewed_candidates'])
        counts['candidate_entries_unknown']+=sum(x['status'].startswith('UNKNOWN') for x in row['reviewed_candidates'])
    return result, {
        'counts': dict(counts),
        'number_timeslices_total':len(rows),
        'conditional_sample_only': True,
        'not_field_incidence_or_population_failure_rate': True,
        'verified_global_stranding_count': 0,
        'missing_review_is_unknown_not_blocked': True,
        'review_validation': 'external review assertions require independent inspection',
    }


def read_jsonl(path):
    if not path:return []
    with Path(path).open(encoding='utf8') as file:
        return [json.loads(line) for line in file if line.strip()]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--alternatives', required=True)
    p.add_argument('--audit_queue')
    p.add_argument('--physical_reviews')
    p.add_argument('--output', required=True)
    a=p.parse_args()
    rows, summary=audit_graph(read_jsonl(a.alternatives),read_jsonl(a.audit_queue),read_jsonl(a.physical_reviews))
    out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in rows),encoding='utf8')
    out.with_suffix('.summary.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False)+'\n',encoding='utf8')
    print(json.dumps(summary,ensure_ascii=False))

if __name__=='__main__': main()
