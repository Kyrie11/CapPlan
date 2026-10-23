"""Create a non-destructive, source-aware independent site-verification queue.

Missing fields are explicit UNKNOWN; this function never turns hybrid simulated
curb dimensions or a CARLA surrogate stop into actual measured evidence.
"""
from __future__ import annotations
from collections import Counter
from pathlib import Path
import json

REQUIRED = (
    'pedestrian_link_direction_attested', 'legal_stop_basis', 'vehicle_stop_pose_m',
    'curb_contact_point_m', 'deployment_polygon_m', 'passenger_speed_interval_mps',
    'passenger_wait_tolerance_s', 'acknowledgement_delay_interval_s',
    'full_vehicle_dwell_windows_s', 'boarding_duration_interval_s',
    'evidence_observation_timestamp_s', 'evidence_expiration_timestamp_s',
    'safe_continuation_protocol',
)


def make_review_queue(packets: Path, output: Path, *, max_sites: int = 60) -> dict:
    if max_sites<=0:raise ValueError('max_sites must be positive')
    by_pair={};counts=Counter()
    with Path(packets).open(encoding='utf-8') as src:
        for line in src:
            if not line.strip():continue
            p=json.loads(line)
            site_keys=p.get('physical_site_pair') or [None,None]
            # pair ID uses physical locations if BOTH are present, else
            # combines episode and anchor to avoid accidental site collision.
            if len(site_keys)==2 and all(site_keys):
                key=('physical',)+tuple(sorted(str(v) for v in site_keys))
            else:
                key=('episode',str(p.get('episode_id')),
                     str(p.get('a_anchor')),str(p.get('b_anchor')))
            info=p.get('action_history') or {}
            witness=p.get('graph_witness') or {}
            flags=[]
            if not witness.get('complete_B_suffix_available'):
                flags.append('MISSING_DIRECTED_B_SUFFIX')
            if not witness.get('arc_orientation_semantics_independently_verified'):
                flags.append('ARC_DIRECTION_NOT_INDEPENDENTLY_ATTESTED')
            for anchor in ('pudo_hybrid_A','pudo_hybrid_B'):
                a=p.get(anchor) or {}
                if not a.get('real_physical_validated'):
                    flags.append(anchor+':NOT_REAL_PHYSICAL_ATTESTATION')
            record=by_pair.setdefault(key,{
                'city':p.get('city'),'site_pair_id':list(key),
                'representative_episode':p.get('episode_id'),
                'example_notice_s':info.get('notice_issued_s'),
                'record_count':0,'flags':set(),
                'source_witness':{'kind':'ENCODED_GIS_GRAPH',
                                  'independent_arc_verification':False},
                'fields_to_independently_audit':{f:{'status':'UNKNOWN','value':None,
                                                   'evidence_source':None,
                                                   'reviewer':None,'review_time':None}
                                                   for f in REQUIRED},
                'physical_handoff_verified':False})
            record['record_count']+=1
            record['flags'].update(flags)
            counts['packets_seen']+=1
    # Balance the geographic audit and avoid top heavy one-city queue.
    pool=sorted(by_pair.values(),key=lambda r:(r['city'] or '',-r['record_count'],str(r['site_pair_id'])))
    chosen=[];city_counts=Counter()
    while pool and len(chosen)<max_sites:
        next_idx=min(range(len(pool)),key=lambda i:(city_counts[pool[i]['city']],
                   -pool[i]['record_count'],pool[i]['city'] or '',str(pool[i]['site_pair_id'])))
        rec=pool.pop(next_idx);rec['flags']=sorted(rec['flags'])
        chosen.append(rec);city_counts[rec['city']]+=1
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    with output.open('w',encoding='utf-8') as f:
        for x in chosen:f.write(json.dumps(x,ensure_ascii=False)+'\n')
    return {'source_packets':counts['packets_seen'],'distinct_site_pairs_candidate':len(by_pair),
            'review_queue_rows':len(chosen),'per_city':dict(city_counts),
            'attested_real_handoffs_created':0,
            'output':str(output),'scope':'INDEPENDENT_ATTESTATION_WORKLIST_NOT_REAL_WORLD_PROOF'}
