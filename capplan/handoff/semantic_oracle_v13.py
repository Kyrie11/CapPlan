"""Independent integer-grid reference for the V12 one-observation policy class.

Research code, *not* an implementation of a published POMDP shield.
Only accepts integer-second windows. A single site AND single service-start time
must be shared by worlds with the same observable history. Waiting, motion,
capability and observation delays are ASSERTED INPUT WINDOWS, not generated here.
"""
from __future__ import annotations
from collections import defaultdict
from dataclasses import dataclass
import math

NEEDED=('passenger_wait_remaining_s','boarding_duration_s',
 'vehicle_stop_windows_s','evidence_validity_windows_s','interface_windows_s',
 'world_handoff_open_windows_s','passenger_arrival_quantifier')


def _integer(x):
    return type(x) in (int,float) and math.isfinite(x) and int(x)==x


def _windows(x,field):
    if x is None:return None
    if not isinstance(x,list):raise ValueError(f'{field}: expected list')
    rows=[]
    for p in x:
        if not isinstance(p,(list,tuple)) or len(p)!=2 or not all(_integer(t) for t in p):
            raise ValueError(f'{field}: expected integer endpoints')
        a,b=map(int,p)
        if a<0 or b<a:raise ValueError(f'{field}: invalid interval')
        rows.append((a,b))
    return rows


def _site_start(site,world,post):
    """(arrival, wait, duration, required windows), or None for unknown."""
    key='post_observation_passenger_arrival_window_s' if post else 'passenger_arrival_window_s'
    if site.get('contract_feasible') is False:return False
    if site.get('contract_feasible') is not True:return None
    if any(site.get(k) is None for k in (*NEEDED,key)):return None
    a=site[key]
    if not isinstance(a,(list,tuple)) or len(a)!=2 or not all(_integer(t) for t in a):
        raise ValueError(f'{key}: integer arrival bounds required')
    a,b=map(int,a)
    if a<0 or a>b:raise ValueError(f'{key}: invalid bounds')
    wait=site['passenger_wait_remaining_s'];d=site['boarding_duration_s']
    if not _integer(wait) or not _integer(d) or wait<0 or d<=0:
        raise ValueError('invalid duration or wait budget')
    q=site['passenger_arrival_quantifier']
    if q not in ('all','exists'):return None
    world_windows=site['world_handoff_open_windows_s']
    if not isinstance(world_windows,dict) or world_windows.get(world) is None:return None
    ww=[_windows(site.get(field),field) for field in
        ('vehicle_stop_windows_s','evidence_validity_windows_s','interface_windows_s')]
    ww.append(_windows(world_windows[world],f'world[{world}]'))
    if any(w is None for w in ww):return None
    return a,b,int(wait),int(d),q,ww


def _admissible(t,values):
    if values is False:return False
    a,b,wait,d,q,windows=values
    if q=='all':
        if not (b<=t<=a+wait):return False
    elif not (a<=t<=b+wait):return False
    return all(any(lo<=t and t+d<=hi for lo,hi in spans) for spans in windows)


def _bound(values):
    if values is False or values is None:return 0
    a,b,wait,d,q,_=values
    return a+wait if q=='all' else b+wait


def shared_witness(sites,worlds,*,post=False,reveal=None,max_seconds=12000):
    """One action must satisfy every compatible hidden world.

    Returns (witness or None, unknown boolean). Enumeration intentionally does
    not share the V12 interval algorithm.
    """
    unknown=False
    for site in sorted(sites,key=lambda z:str(z['option_id'])):
        vals=[_site_start(site,w,post) for w in worlds]
        if any(v is None for v in vals):unknown=True;continue
        if any(v is False for v in vals):continue
        if post:
            if reveal is None:raise ValueError('reveal time required')
            # V12 restricted policy: everybody is still in a holding state at
            # reveal. In particular it does not justify a pre-reveal movement.
            if any(v[0]<reveal for v in vals):continue
        upper=min(_bound(v) for v in vals)
        if upper>max_seconds:raise ValueError('integer-grid horizon exceeded; use smaller audit subset')
        for t in range(max(0,int(reveal) if reveal is not None else 0),int(upper)+1):
            if all(_admissible(t,v) for v in vals):
                return {'option_id':site['option_id'],'service_start_s':t},unknown
    return None,unknown


def evaluate(case,max_seconds=12000):
    worlds=case['world_support'];sites=case['options']
    if not worlds or len(worlds)!=len(set(worlds)) or not sites or len(sites)!=len({s['option_id'] for s in sites}):
        raise ValueError('unique nonempty worlds/options required')
    fixed,missing=shared_witness(sites,worlds,max_seconds=max_seconds)
    one_per_world={}
    for world in worlds:
        witness,_=shared_witness(sites,[world],max_seconds=max_seconds)
        one_per_world[world]=witness
    conditional={};unknown=missing;obs=case.get('one_observation')
    if fixed:
        status='ROBUST_FIXED_IN_DECLARED_MODEL'
    elif obs is None:
        status='UNKNOWN_MISSING_INPUT' if missing else 'NO_POLICY_IN_RESTRICTED_CLASS'
    else:
        t=obs['available_at_s'];groups=defaultdict(list)
        if not _integer(t) or t<0:raise ValueError('observation.available_at_s must be integer and >=0')
        if set(obs['outcomes_by_world'])!=set(worlds):raise ValueError('observation outcomes must cover worlds')
        for world in worlds:
            groups[obs['outcomes_by_world'][world]].append(world)
        for observed,group in sorted(groups.items()):
            w,missing_branch=shared_witness(sites,group,post=True,reveal=int(t),max_seconds=max_seconds)
            conditional[observed]={'worlds':group,'witness':w,'missing_inputs':missing_branch}
            unknown=unknown or missing_branch
        if all(b['witness'] is not None for b in conditional.values()):
            status='ONE_OBSERVATION_CAUSAL_POLICY_IN_DECLARED_MODEL'
        else:
            status='UNKNOWN_MISSING_INPUT' if unknown else 'NO_POLICY_IN_RESTRICTED_CLASS'
    # Clairvoyant oracle may use the hidden world identity before it is ever
    # observed. It is an upper bound, NEVER an implementable policy.
    clairvoyant=all(x is not None for x in one_per_world.values())
    return {'case_id':case.get('case_id'),'status':status,'fixed_witness':fixed,
        'one_observation_witnesses':conditional,'clairvoyant_possible':clairvoyant,
        'clairvoyant_per_world':one_per_world,
        'nonanticipativity_gap':bool(clairvoyant and status=='NO_POLICY_IN_RESTRICTED_CLASS'),
        'unknown_data_encountered':unknown,
        'scope':'FINITE_INTEGER_GRID_ONE_OBSERVATION_POLICY_CLASS',
        'not_published_shield':True,'not_real_world_handoff':True}
