#!/usr/bin/env python3
"""V11 complete enumeration of existing nuPlan/GIS PUDO anchors for mined pairs.

Fixes the V8/V9 30-alternate and V10 audit-packet top-5 truncation for science
falsification. Each row lists ALL graph-feasible anchors in the declared
existing dataset for each requested notice time and capability profile.
This is NOT a completeness claim about all physically possible curbs, assistance
strategies, continuous poses, or information-gathering contingent policies.
"""
from __future__ import annotations

import argparse
from collections import defaultdict, Counter
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capplan.data.accessibility_layer import load_accessibility_graph
from capplan.handoff.gate_evidence import records
from scripts.audit_handoff_all_alternatives_v8 import evaluate, filtered_adjacency


def run(dataset_dir, pairs_path, output, limit=128, times=(0.,30.,60.,90.),
        speed_mps=.8, ack_s=10., pause=False):
    if limit<=0: raise ValueError('limit must be positive')
    selected=[]
    for p in records(pairs_path):
        selected.append(p)
        if len(selected)>=limit: break
    eps={p['episode_id'] for p in selected}
    all_anchors=defaultdict(list)
    for a in records(Path(dataset_dir)/'pudo_anchors.jsonl'):
        if a.get('episode_id') in eps:all_anchors[a['episode_id']].append(a)
    pair_groups=defaultdict(list)
    for p in selected:pair_groups[p['episode_id']].append(p)
    out=Path(output);out.parent.mkdir(parents=True,exist_ok=True)
    stats=Counter();errors=[]
    with out.open('w', encoding='utf8') as handle:
        for episode, episode_pairs in pair_groups.items():
            try:
                graph=load_accessibility_graph(dataset_dir,episode)
            except (KeyError,ValueError,TypeError,FileNotFoundError) as exc:
                errors.append({'episode_id':episode,'error':str(exc)});continue
            for pair in episode_pairs:
                try:
                    compiled=(*filtered_adjacency(graph,pair['screening_contract']),{})
                    for t in times:
                        mode={}
                        for requires,tag in ((True,'strict'),(False,'optimistic')):
                            r=evaluate(pair,all_anchors[episode],graph,t,
                                speed_mps=speed_mps,ack_s=ack_s,pause=pause,
                                require_curb=requires,compiled=compiled,
                                max_returned_alternates=None)
                            assert r['eligible_anchor_list_complete']
                            assert len(r['alternates'])==r['graph_eligible_alternates_count']
                            mode[tag]={'graph_candidate_count':len(r['alternates']),
                                       'anchors':r['alternates'],
                                       'filter_reasons':r['filter_reasons'],
                                       'legal_unblocked_candidates_checked':r['legal_unblocked_candidates_checked']}
                        row={'episode_id':episode,'a_anchor':pair['a_anchor'],
                            'b_anchor':pair['b_anchor'],
                            'physical_site_pair':pair.get('physical_site_pair'),
                            'notice_issued_at_s':t,
                            'dataset_pudo_anchor_inventory_count':len(all_anchors[episode]),
                            'strict':mode['strict'],'optimistic':mode['optimistic'],
                            'all_existing_eligible_dataset_anchor_entries_enumerated':True,
                            'all_physical_or_contingent_recovery_actions_enumerated':False,
                            'strict_connector_absence_is_unknown_physics_not_failure':True,
                            'selection':'MINED_RECOURSE_PAIRS_NOT_NATURAL_INCIDENCE'}
                        handle.write(json.dumps(row,ensure_ascii=False)+'\n')
                        stats['time_rows']+=1
                        stats['strict_any']+=bool(mode['strict']['anchors'])
                        stats['optimistic_any']+=bool(mode['optimistic']['anchors'])
                    stats['processed_pairs']+=1
                except (KeyError,ValueError,TypeError,OverflowError,AssertionError) as exc:
                    errors.append({'episode_id':episode,'a_anchor':pair.get('a_anchor'),'error':str(exc)})
    summary={'counts':dict(stats),'attempted_pairs':len(selected),'errors':errors,
             'full_existing_dataset_candidate_inventory':True,
             'cannot_certify_global_stranding':True,
             'cannot_estimate_natural_incidence':True}
    out.with_suffix('.summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    return summary


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dataset_dir',required=True)
    p.add_argument('--pairs',required=True)
    p.add_argument('--output',required=True)
    p.add_argument('--limit',type=int,default=128)
    p.add_argument('--notice_times_s',default='0,30,60,90')
    p.add_argument('--speed_mps',type=float,default=.8)
    p.add_argument('--ack_s',type=float,default=10.)
    p.add_argument('--pause_during_ack',action='store_true')
    a=p.parse_args()
    if a.limit<1:p.error('limit must be positive')
    times=tuple(float(x) for x in a.notice_times_s.split(','))
    if any(t<0 for t in times):p.error('nonnegative times only')
    print(json.dumps(run(a.dataset_dir,a.pairs,a.output,a.limit,times,
                         a.speed_mps,a.ack_s,a.pause_during_ack),ensure_ascii=False))


if __name__=='__main__':main()
