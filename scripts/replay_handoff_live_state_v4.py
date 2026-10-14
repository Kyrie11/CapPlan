#!/usr/bin/env python3
"""Read-only sequential state/observation replay with no automatic passenger command.

Event stream (JSONL): {timestamp_s, type, ...}, types: move, wait,
observe, issue, acknowledge, motion_attestation, physical_review, finish.
Movement must have a traceable source and nonnegative measured distance.
Unknown evidence is never promoted to real-world safe-to-commit status.
"""
from __future__ import annotations
import argparse,json,math,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from capplan.handoff.gate_evidence import records,dump_json


def replay(profile,events,output,required_observations=('pudo_available','curb_connector','boarding_interface')):
    if not isinstance(profile,dict) or not profile.get('consent_scope') or not profile.get('profile_id'):
        raise ValueError('profile requires consent_scope and profile_id')
    mob=profile['mobility'];wait=profile['wait']
    distance_budget=float(mob['max_access_distance_m']);wait_budget=float(wait['max_wait_exposure_s'])
    used=0.;waited=0.;clock=float('-inf');issued={};acks=set();evidence={};events_out=[]
    for seq,e in enumerate(records(events)):
        t=float(e['timestamp_s'])
        if not math.isfinite(t) or t<clock:raise ValueError('non-monotone or non-finite event clock')
        clock=t;kind=e.get('type')
        if kind=='move':
            d=float(e.get('distance_m',-1))
            if not math.isfinite(d) or d<0 or not e.get('source'):
                raise ValueError('movement requires nonnegative measured distance and source')
            used+=d
        elif kind=='wait':
            v=float(e.get('duration_s',-1))
            if not math.isfinite(v) or v<0:raise ValueError('negative/nonfinite waiting interval')
            waited+=v
        elif kind=='observe':
            field=e.get('field');end=float(e.get('valid_until_s',-1))
            if not field or not e.get('source') or not math.isfinite(end) or end<t:
                raise ValueError('observation requires source and non-expired validity')
            evidence[field]={'value':e.get('value'),'valid_until_s':end,
                             'source':e['source'],'evidence_class':e.get('evidence_class','unknown')}
        elif kind=='issue':
            if not e.get('instruction_id') or e['instruction_id'] in issued:
                raise ValueError('duplicate/missing instruction id')
            issued[e['instruction_id']]=t
        elif kind=='acknowledge':
            if e.get('instruction_id') not in issued or t<issued[e['instruction_id']]:
                raise ValueError('acknowledgement without prior instruction')
            acks.add(e['instruction_id'])
        elif kind in ('motion_attestation','physical_review','finish'):
            # Require external validators. Bare self-claims are not evidence.
            pass
        else:raise ValueError('unknown event type '+str(kind))
        remaining=distance_budget-used;wait_remaining=wait_budget-waited
        current={k:v for k,v in evidence.items() if v['valid_until_s']>=t}
        missing=[field for field in required_observations
                 if field not in current or current[field]['value'] is not True or
                 current[field]['evidence_class'] not in ('independent_site_audit','validated_live_sensor')]
        reasons=[]
        if remaining<0:reasons.append('DISTANCE_BUDGET_EXCEEDED')
        if wait_remaining<0:reasons.append('WAIT_BUDGET_EXCEEDED')
        if missing:reasons.append('MISSING_OR_STALE_INDEPENDENT_EVIDENCE')
        # Even if nothing is missing, a live motion + interaction-safe checker
        # and causal reachability planner are still needed; this is a status
        # monitor, not a command/shield.
        events_out.append({'seq':seq,'at_s':t,'event_type':kind,'profile_id':profile['profile_id'],
           'used_distance_m':used,'remaining_distance_m':remaining,
           'waited_s':waited,'remaining_wait_s':wait_remaining,'unverified_fields':missing,
           'acknowledged_instructions':len(acks),
           'screening_status':'HOLD_RESOURCE_VIOLATION' if remaining<0 or wait_remaining<0 else
              'HOLD_NEEDS_EXTERNAL_VERIFICATION' if reasons else 'AWAIT_CAUSAL_MOTION_AND_INTERFACE_CHECK',
           'can_issue_guidance_from_this_replay':False,
           'reason_codes':reasons})
    dest=Path(output);dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in events_out),encoding='utf-8')
    summary={'events':len(events_out),'latest':events_out[-1] if events_out else None,
       'population_calibration':False,'vehicle_closure':False,
       'warning':'Read-only replay; never issue real passenger directions or presume real-world safety.'}
    dump_json(dest.with_suffix('.summary.json'),summary);return summary

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--profile',required=True)
    p.add_argument('--events',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();pr=json.loads(Path(a.profile).read_text(encoding='utf-8'))
    print(json.dumps(replay(pr,a.events,a.output),ensure_ascii=False))
