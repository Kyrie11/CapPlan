#!/usr/bin/env python3
"""Create EMPTY physical/temporal audit forms from V10 unverified candidates.

Deduplicate by actual episode+anchor and physical-site key. Null fields are
intentional: this tool never invents legality, boarding, observation timestamps,
passenger mobility, or vehicle stop windows. Temporal repeats and site-pair
combinations cannot be counted as independent physical observations.
"""
from __future__ import annotations
import argparse, json
from collections import Counter,defaultdict
from pathlib import Path


def prepare(rows):
    by_anchor={};physical_keys=set();pairs=set();episodes=set();mentions=0
    for group in rows:
        ep=str(group['episode_id']);pair=str(group['physical_site_pair'])
        episodes.add(ep);pairs.add((ep,pair))
        for cand in group.get('candidate_review_queue',[]):
            mentions+=1
            anchor=str(cand['anchor_id']);key=(ep,anchor)
            physical=str(cand.get('physical_site_key') or 'UNKNOWN')
            if physical!='UNKNOWN':physical_keys.add(physical)
            if key not in by_anchor:
                by_anchor[key]={
                    'option_id':ep+'::'+anchor,'episode_id':ep,'site_key':physical,
                    'review_source_pair_keys':[],
                    'input_evidence_class':'UNVERIFIED_PHYSICAL_AUDIT_QUEUE',
                    'passenger_arrival_window_s':None,
                    'passenger_wait_remaining_s':None,
                    'boarding_duration_s':None,
                    'vehicle_stop_windows_s':None,
                    'evidence_validity_windows_s':None,
                    'attestations':{name:{'independent_source_ref':None,'review_status':'UNKNOWN'}
                                    for name in ('directed_pedestrian_path','legal_stop',
                                    'vehicle_trajectory_stop_pose','deployment_region',
                                    'dynamic_observation')},
                    'research_notice':'No measurement or motion check was created by this template.'}
            if pair not in by_anchor[key]['review_source_pair_keys']:
                by_anchor[key]['review_source_pair_keys'].append(pair)
    summary={'episode_count':len(episodes),'selected_pair_count':len(pairs),
             'repeated_candidate_mentions':mentions,
             'unique_episode_anchor_count':len(by_anchor),
             'unique_known_physical_site_keys':len(physical_keys),
             'real_temporal_packets_verified':0,
             'interpretation':'Deduplicate by physical site before estimating statistical evidence; template is NOT a real-world handoff measurement.'}
    return list(by_anchor.values()),summary


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--queue',required=True)
    parser.add_argument('--output',required=True)
    a=parser.parse_args()
    rows=[json.loads(x) for x in Path(a.queue).read_text(encoding='utf-8').splitlines() if x.strip()]
    packets,summary=prepare(rows)
    out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in packets),encoding='utf-8')
    out.with_suffix('.summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False))
if __name__=='__main__':main()
