#!/usr/bin/env python3
"""Convert V9 *optimistic* graph witnesses into physical-evidence audit requests.

Do NOT produce executable world traces, an artificial curb connector, or a
positive real-world handoff label. Candidate counts are conditional on mined
pairs and cannot estimate natural incident prevalence. Deduplicate by physical
site-pair; snapshots at 0/30/60/90 are correlated within site.
"""
from __future__ import annotations
import argparse,json
from collections import Counter,defaultdict
from pathlib import Path


def export_packets(rows,max_pairs=64,max_candidates=5):
    by_pair=defaultdict(list)
    for r in rows:
        key=(str(r.get('episode_id')),str(r.get('physical_site_pair')))
        by_pair[key].append(r)
    packets=[];c=Counter()
    for (episode,pair),members in sorted(by_pair.items())[:max_pairs]:
        first=min(members,key=lambda r:float(r.get('notice_issued_at_s',0)))
        candidates={}
        for row in members:
            for raw in row.get('optimistic_unverified_examples',[]):
                if raw.get('anchor_id'):
                    item={k:raw.get(k) for k in ('anchor_id','physical_site_key',
                        'remaining_graph_m','total_passenger_distance_m','is_declared_B')}
                    candidates.setdefault(item['anchor_id'],item)
        sorted_items=sorted(candidates.values(),key=lambda x:(x.get('total_passenger_distance_m') or 1e30,x['anchor_id']))
        while len(sorted_items)>max_candidates:sorted_items.pop()
        pending=[]
        for cand in sorted_items:
            pending.append({**cand,
               'pedestrian_access_point':None,'curb_interaction_region':None,
               'vehicle_stop_pose':None,'real_typed_curb_connector':None,
               'vehicle_continuous_motion_trajectory_ref':None,
               'service_dwell_interval':None,'interface_deployment_clearance':None,
               'legal_stop_observation':None,'observation_timestamp_and_validity':None,
               'user_ack_and_actual_position':None,
               'evidence_for_evaluation':'UNVERIFIED_REQUIRED',
               'audit_sources':[],
               'verification_state':'UNKNOWN_PHYSICAL_HANDOFF'})
        if not pending:c['no_exportable_candidate_examples']+=1
        c['site_pairs']+=1;c['pending_candidates']+=len(pending)
        packets.append({
            'episode_id':episode,'physical_site_pair':pair,
            'candidate_A':first.get('a_anchor'),'candidate_B':first.get('b_anchor'),
            'evaluated_notice_times_s':sorted(set(float(r.get('notice_issued_at_s',0)) for r in members)),
            'strict_explicit_graph_connector_counts':[int(r.get('strict_explicit_curb_count',0)) for r in members],
            'optimistic_graph_candidate_counts':[int(r.get('optimistic_without_curb_count',0)) for r in members],
            'candidate_review_queue':pending,
            'requires_further_evidence':[
                'independent real pedestrian->curb connector and direction',
                'stop legality + vehicle pose + deployment envelope',
                'real vehicle motion check and synchronized boarding dwell',
                'timestamped curb observation, notice/ack timing, true passenger progress',
                'third PUDO and assistance actions after route change'],
            'real_service_viability':'UNKNOWN',
            'source_selection':'MINED_RECOURSE_PAIR_CONDITIONAL_SAMPLE',
            'not_service_incidence':True,
            'do_not_pass_to_finite_solver_before_world_and_motion_evidence':True})
    return packets,{'pairs':c['site_pairs'],'candidate_examples':c['pending_candidates'],
          'pairs_without_examples':c['no_exportable_candidate_examples'],
          'real_viability_verified':0,'evidence_status':'AUDIT_QUEUE_NOT_TRUE_LABELS'}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--v9_rows',required=True)
    p.add_argument('--output',required=True)
    p.add_argument('--max_pairs',type=int,default=64)
    p.add_argument('--max_candidates',type=int,default=5)
    a=p.parse_args()
    if a.max_pairs<1 or a.max_candidates<1:p.error('positive limits required')
    rows=[json.loads(x) for x in Path(a.v9_rows).read_text().splitlines() if x.strip()]
    packets,summary=export_packets(rows,a.max_pairs,a.max_candidates)
    f=Path(a.output);f.parent.mkdir(parents=True,exist_ok=True)
    f.write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in packets),encoding='utf-8')
    f.with_suffix('.summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(summary,ensure_ascii=False))
if __name__=='__main__':main()
