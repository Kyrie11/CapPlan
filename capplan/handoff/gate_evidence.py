"""Conservative research-evidence accounting for pickup handoff.

Never turn a synthetic counterfactual, a submitted claim or missing evidence
into measured site truth, motion success or real-world incidence.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
from pathlib import Path
import csv
import json
import math

CITY = {'us-ma-boston':'boston','us-pa-pittsburgh-hazelwood':'pittsburgh',
        'us-nv-las-vegas-strip':'vegas','sg-one-north':'singapore'}


def records(path):
    with Path(path).open(encoding='utf-8') as f:
        for lineno, line in enumerate(f, 1):
            if line.strip():
                try:
                    row=json.loads(line)
                    if not isinstance(row,dict):raise ValueError('expected object')
                    yield row
                except (ValueError, TypeError) as e:
                    raise ValueError(f'{path}:{lineno}: {e}') from e


def dump_json(path, obj):
    dest=Path(path);dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(json.dumps(obj,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8')


def site_key(p):
    sites=p.get('physical_site_pair')
    if isinstance(sites,(tuple,list)) and len(sites)==2 and all(isinstance(s,str) and s.strip() for s in sites):
        return tuple(sorted(sites))
    # Missing site identifier is NOT evidence of independence.
    return None


def pair_key(p):
    return (str(p['episode_id']),str(p['a_anchor']),str(p['b_anchor']))


def city(p):
    name=str(p.get('map_name') or '')
    return CITY.get(name,name or 'UNKNOWN')


def fingerprint(p):
    key=pair_key(p)
    return sha256(json.dumps(key,ensure_ascii=False).encode('utf-8')).hexdigest()[:20]


def evidence_risks(pair):
    flags=[]
    if not site_key(pair):flags.append('SITE_IDENTITY_MISSING')
    conn=pair.get('explicit_curb_connector_evidence') or {}
    if not conn.get('A') or not conn.get('B'):flags.append('TYPED_CURB_CONNECTOR_NOT_BOTH')
    if pair.get('interface_completeness_claim') is not True:flags.append('INTERFACE_FIELDS_NOT_COMPLETE')
    if pair.get('two_candidate_distance_and_vehicle_witness') is not True:flags.append('NO_ATTESTED_MOTION')
    if any(term in json.dumps([pair.get('pudo_interface_a'),pair.get('pudo_interface_b'),
                               pair.get('paths')],ensure_ascii=False).lower()
           for term in ('simulat','synthetic','unknown','proxy')):
        flags.append('SIMULATED_OR_UNKNOWN_STATIC_EVIDENCE')
    return flags


def inspect_overlays(overlays, *, sample_limit=20):
    c=Counter();groups=defaultdict(Counter);violations=[];sites=defaultdict(set)
    for i,row in enumerate(records(overlays)):
        c['overlays']+=1
        fam=(row.get('provenance') or {}).get('counterfactual_intervention_family','original')
        src=(row.get('provenance') or {}).get('source_pair') or {}
        m=city(src);groups[fam]['overlays']+=1;groups[fam]['city:'+m]+=1
        sk=site_key(src)
        if sk:sites[fam].add(sk)
        cb=row.get('capability_budget') or {}
        if cb.get('wait_budget_s') is None:groups[fam]['wait_budget_unspecified']+=1
        if cb.get('profile_id') is None:groups[fam]['profile_not_bound']+=1
        if not row.get('motivation_witness',{}).get('vehicle_a_to_b_verified_for_event_time',False):
            groups[fam]['sidecar_motion_unverified']+=1
        mw=row.get('motivation_witness') or {}
        if mw.get('counterfactual_event_time_s') is not None and mw.get('counterfactual_a_arrival_s') is not None:
            event=float(mw['counterfactual_event_time_s']);arrival=float(mw['counterfactual_a_arrival_s'])
            if event<=arrival:groups[fam]['event_before_or_at_a_arrival']+=1
        om=row.get('observation_model') or {}
        try:
            if float(om.get('scheduled_observation_request_s',0))+float(om.get('availability_observation_delay_s',0))<=float(mw.get('counterfactual_event_time_s',float('inf'))):
                groups[fam]['observation_precedes_or_at_event']+=1
        except (ValueError,TypeError,OverflowError):pass
        try:
            from capplan.handoff.evidence import packet_covers_event
            packet=(row.get('candidate_handoffs') or [{}])[0].get('vehicle_motion_evidence')
            ev=mw.get('counterfactual_event_time_s')
            if ev is not None and bool(mw.get('vehicle_a_to_b_verified_for_event_time'))!=packet_covers_event(packet,ev):
                groups[fam]['MOTION_WITNESS_CONTRADICTION']+=1
        except (IndexError,KeyError,ValueError,TypeError):
            groups[fam]['MOTION_WITNESS_UNCHECKABLE']+=1
        if (row.get('provenance') or {}).get('privileged_schedule_upper_bound') or 'privileged' in str(om.get('schedule_provenance','')).lower():
            groups[fam]['privileged_sensing']+=1
        if row.get('phase')!='pickup':groups[fam]['non_pickup']+=1
        worlds=row.get('world_support') or []
        try:
            if not worlds or abs(sum(float(w['probability']) for w in worlds)-1)>1e-6:
                raise ValueError('world probabilities invalid')
            a,b=[h['anchor_id'] for h in row['candidate_handoffs']]
            for w in worlds:
                for name in [a,b]:
                    trace=w['availability_trace'][name]
                    if not trace or trace[0][0] > row['initial_execution_state']['timestamp_s']:
                        raise ValueError('availability trace does not cover initial time')
                    if any(float(trace[j][0])>=float(trace[j+1][0]) for j in range(len(trace)-1)):
                        raise ValueError('non-strict/duplicate trace timestamps')
            budget=float(cb['distance_budget_m'])
            consumed=float(row['initial_execution_state'].get('walked_or_rolled_m',0))
            if not all(math.isfinite(v) for v in (budget,consumed)) or budget<=0 or consumed<0:
                raise ValueError('invalid distance ledger')
            req=float(om.get('scheduled_observation_request_s',0))
            delay=float(om['availability_observation_delay_s'])
            if req<0 or delay<0:raise ValueError('negative observation time')
        except (KeyError,ValueError,TypeError,IndexError,OverflowError) as e:
            c['invalid_overlay']+=1
            if len(violations)<sample_limit:violations.append({'overlay_id':row.get('overlay_id'),'error':str(e)})
    return {'counts':dict(c),'by_event_family':{k:{**dict(v),'distinct_known_site_pairs':len(sites[k])}
                                             for k,v in groups.items()},
            'invalid_examples':violations,
            'scope':'controlled sidecars; sample frequencies are not real-world event incidence'}


def inspect_pairs(path):
    c=Counter();by_city=defaultdict(Counter);sites=defaultdict(set);samples=[]
    for p in records(path):
        c['pairs']+=1;m=city(p);by_city[m]['pairs']+=1
        site=site_key(p)
        if site:sites[m].add(site)
        flags=evidence_risks(p)
        for f in flags:c['risk:'+f]+=1
        if p.get('two_candidate_distance_and_vehicle_witness') is True:c['declared_motion_witnesses']+=1
        if len(samples)<12:samples.append({'pair_id':fingerprint(p),'city':m,'risk_flags':flags})
    audit_path=Path(path).parent/'mining_audit.json'
    audit_declared=None
    if audit_path.is_file():
        try:
            audit_declared=int(json.loads(audit_path.read_text(encoding='utf-8'))
                               .get('counts',{}).get('distance_recourse_candidates',0))
        except (ValueError,TypeError,KeyError):audit_declared=None
    coverage=('MATCHED_COMPLETE_PAIRS' if audit_declared==c['pairs']
              else 'TRUNCATED_EXAMPLE_BUNDLE' if audit_declared is not None and audit_declared>c['pairs']
              else 'UNVERIFIED_COMPLETENESS')
    return {'counts':dict(c),'by_city':{k:{**dict(v),'distinct_known_site_pairs':len(sites[k])}
                                     for k,v in by_city.items()},'examples':samples,
            'mining_total_declared_candidates':audit_declared,
            'pair_file_coverage':coverage,
            'allowed_frequency_claims':False,
            'denominator':'only rows actually present in this JSONL; NOT all mining hits, independent sites or observed events'}


def inspect_profiles(path):
    counts=Counter();first=[]
    for p in records(path):
        counts['profiles']+=1
        suite=p.get('evaluation_suite','UNDECLARED');counts['suite:'+str(suite)]+=1
        if p.get('population_representative') is True:counts['claimed_representative']+=1
        if len(first)<5:first.append({'profile_id':p.get('profile_id'), 'archetype':p.get('archetype')})
    return {'counts':dict(counts),'examples':first,'warning':'A profile file alone does not establish use in the policy overlays'}


def inspect_review(path):
    counts=Counter();approved_sites=set();examples=[]
    with Path(path).open(encoding='utf-8',newline='') as f:
        for r in csv.DictReader(f):
            counts['review_rows']+=1
            # No single PASS string is enough to assert independent audited site evidence.
            fields=('curb_connector_checked','legal_stop_checked','vehicle_trajectory_checked',
                    'boarding_interface_checked')
            if str(r.get('review_decision') or r.get('decision') or '').upper()=='PASS':
                counts['declared_pass']+=1
                if (all(str(r.get(k,'')).upper()=='PASS' for k in fields) and
                    (r.get('external_source_uri') or r.get('source_reference')) and
                    (r.get('reviewer') or r.get('expert_reviewer'))):
                    a=r.get('site_a');b=r.get('site_b')
                    if a and b:approved_sites.add(tuple(sorted([a,b])))
                    else:counts['pass_without_site_keys']+=1
                else:counts['pass_without_required_evidence']+=1
    counts['strictly_completed_unique_sites']=len(approved_sites)
    return dict(counts)
