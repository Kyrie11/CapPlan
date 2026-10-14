#!/usr/bin/env python3
"""Quarantine external motion claims unless bound to requested run and checked log.

This is a provenance/consistency validator, NOT a simulator and NOT a proof of
physical safety. Independent review of checker and vehicle-interface semantics
is still required. No packet is created when no actual motion result exists.
"""
from __future__ import annotations
import argparse,hashlib,json,math,sys
from collections import Counter
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from capplan.handoff.gate_evidence import records,dump_json
from capplan.handoff.evidence import packet_valid,packet_covers_event


def key(d):
    return (str(d['episode_id']),str(d['from_anchor']),str(d['to_anchor']))


def audit(requests,claims,output_dir,*,log_root=None):
    out=Path(output_dir);out.mkdir(parents=True,exist_ok=True)
    req={}
    for row in records(requests):
        k=key(row)
        if k in req:raise ValueError('duplicate motion request '+str(k))
        req[k]=row
    results=[];accepted=[];seen=set();cnt=Counter()
    if claims:
        submitted=list(records(claims))
        duplicate_counts=Counter()
        for c in submitted:
            try:duplicate_counts[key(c)]+=1
            except (KeyError,TypeError,ValueError):pass
        for claim in submitted:
            cnt['submitted']+=1
            reasons=[]
            try:k=key(claim)
            except (KeyError,ValueError,TypeError):
                cnt['invalid_identity']+=1;continue
            if duplicate_counts[k]>1:reasons.append('DUPLICATE_CLAIM_KEY')
            seen.add(k)
            request=req.get(k)
            if request is None:reasons.append('NOT_IN_REQUEST_SET')
            if not packet_valid(claim):reasons.append('INVALID_PACKET_OR_CHECKS')
            if request is not None:
                try:
                    event=float(request['traffic_event_s'])
                    if not packet_covers_event(claim,event):reasons.append('REQUEST_EVENT_OUTSIDE_CHECKED_INTERVAL')
                except (ValueError,TypeError,KeyError):reasons.append('INVALID_REQUEST_TIME')
                if claim.get('scenario_token')!=request.get('scenario_token'):
                    reasons.append('SCENARIO_TOKEN_MISMATCH')
                if claim.get('map_name')!=request.get('map_name'):
                    reasons.append('MAP_MISMATCH')
            for name in ('planner_hash','trajectory_id','simulator_log','simulator_log_sha256',
                         'checker_version','time_reference'):
                if not isinstance(claim.get(name),str) or not claim[name].strip():
                    reasons.append('MISSING_'+name.upper())
            if claim.get('time_reference')!='scene_relative_seconds':
                reasons.append('UNSUPPORTED_TIME_REFERENCE')
            log_path=claim.get('simulator_log')
            if log_path:
                path=Path(log_path)
                if log_root:
                    root=Path(log_root).resolve()
                    path=(root/path).resolve() if not path.is_absolute() else path.resolve()
                    if not path.is_relative_to(root):reasons.append('LOG_OUTSIDE_DECLARED_ROOT')
                if not path.is_file():reasons.append('SIMULATOR_LOG_MISSING')
                else:
                    digest=hashlib.sha256(path.read_bytes()).hexdigest()
                    if digest!=claim.get('simulator_log_sha256'):reasons.append('SIMULATOR_LOG_HASH_MISMATCH')
            if not reasons:
                # A checked, bound external *attestation*, still not independent
                # full handoff or natural-scene validation.
                accepted.append(claim)
                cnt['log_bound_attestations']+=1
            else:cnt['quarantined']+=1
            results.append({'key':k,'status':'LOG_BOUND_CHECKER_ATTESTATION' if not reasons else 'QUARANTINE',
                            'reasons':reasons})
    verified_file=out/'log_bound_motion_attestations.jsonl'
    verified_file.write_text(''.join(json.dumps(p,ensure_ascii=False)+'\n' for p in accepted),encoding='utf-8')
    total={'requests':len(req),'counts':dict(cnt),'unanswered_requests':len(set(req)-seen),
           'log_bound_attestation_count':len(accepted),
           'independent_real_handoff_confirmations':0,
           'warning':'A checker flag and log hash are only attestation integrity; require external review of motion, collision, curb pose and time reference before any physical claims',
           'attestation_file':str(verified_file),'results':results[:500]}
    dump_json(out/'gate2_motion_audit.json',total)
    return total

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--requests',required=True)
    p.add_argument('--claims',help='external real method-specific motion checker outputs; omit until available')
    p.add_argument('--output_dir',required=True);p.add_argument('--log_root')
    a=p.parse_args();d=audit(a.requests,a.claims,a.output_dir,log_root=a.log_root)
    print(json.dumps({k:d[k] for k in ('requests','counts','unanswered_requests')},ensure_ascii=False))
