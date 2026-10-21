"""Exact interval calculations for a deliberately RESTRICTED pickup policy class.

This is NOT a POMDP shield or a real-vehicle feasibility certificate.

Supported policy class:
  (a) choose one site and one service-start time without observing the world;
  or (b) complete ONE explicitly scheduled observation before any new passenger
      instruction, then choose one site and service-start time per *observed*
      outcome. Worlds with the same observed outcome must share the same choice.

Each site supplies windows for passenger arrival, passenger remaining wait,
continuous stationary vehicle availability, time-valid evidence, interface
availability and per-world curb/stop availability. A service start must leave
room for the ENTIRE boarding duration. Passenger profiles and execution
histories must already have been compiled to the supplied arrival windows and
resource predicates. These asserted inputs may be simulated, derived or
independently audited; this routine does not promote them into reality.

This is an AND-over-worlds / OR-over-actions calculation for the restricted
policy class. NO_POLICY_FOUND does not imply global POMDP/physical infeasibility.
"""
from __future__ import annotations
from dataclasses import dataclass
from collections import defaultdict
import math
from typing import Any

EPS=1e-9
Interval=tuple[float,float]


def intervals(value:Any,*,field:str)->tuple[Interval,...]:
    if not isinstance(value,list):
        raise ValueError(f'{field} must be a list of [start,end] intervals')
    out=[]
    for item in value:
        if not isinstance(item,(list,tuple)) or len(item)!=2 or any(type(x) not in (int,float) or not math.isfinite(x) for x in item):
            raise ValueError(f'bad {field} interval: {item}')
        a,b=map(float,item)
        if a>b:raise ValueError(f'{field} has reversed interval')
        out.append((a,b))
    return merge(out)


def merge(spans)->tuple[Interval,...]:
    ordered=sorted(spans)
    result=[]
    for a,b in ordered:
        if result and a<=result[-1][1]+EPS:
            result[-1]=(result[-1][0],max(b,result[-1][1]))
        else:result.append((a,b))
    return tuple(result)


def intersect(a:tuple[Interval,...],b:tuple[Interval,...])->tuple[Interval,...]:
    i=j=0;out=[]
    while i<len(a) and j<len(b):
        lo=max(a[i][0],b[j][0]);hi=min(a[i][1],b[j][1])
        if lo<=hi+EPS:out.append((lo,hi))
        if a[i][1]<b[j][1]-EPS:i+=1
        elif b[j][1]<a[i][1]-EPS:j+=1
        else:i+=1;j+=1
    return merge(out)


def shift_ends_for_full_duration(spans:tuple[Interval,...],duration:float)->tuple[Interval,...]:
    """Convert intervals during which a resource is continuously available
    into legal START times for an uninterrupted duration-sized service.
    """
    return merge((a,b-duration) for a,b in spans if b-a+EPS>=duration)


def _num(x,name,*,positive=False):
    if type(x) not in (int,float) or not math.isfinite(x) or x<0 or (positive and x<=0):
        raise ValueError(f'{name} must be finite and {"positive" if positive else "nonnegative"}')
    return float(x)


def one_world_windows(site:dict,world:str,*,post_observation=False)->tuple[Interval,...]|None:
    """Returns None for UNKNOWN (not for an empty feasible set)."""
    arr_key='post_observation_passenger_arrival_window_s' if post_observation else 'passenger_arrival_window_s'
    required=('passenger_wait_remaining_s','boarding_duration_s','vehicle_stop_windows_s',
              'evidence_validity_windows_s','interface_windows_s','world_handoff_open_windows_s')
    if any(site.get(x) is None for x in (*required,arr_key)):return None
    world_map=site['world_handoff_open_windows_s']
    if not isinstance(world_map,dict) or world not in world_map or world_map[world] is None:return None
    arrival=site[arr_key]
    if not isinstance(arrival,(list,tuple)) or len(arrival)!=2:return None
    arrival=intervals([arrival],field=arr_key)
    wait=_num(site['passenger_wait_remaining_s'],'passenger_wait_remaining_s')
    duration=_num(site['boarding_duration_s'],'boarding_duration_s',positive=True)
    if site.get('contract_feasible') is False:return ()
    if site.get('contract_feasible') is not True:return None
    # EXACT meaning of arrival interval is essential. 'exists' models an
    # intentional choice of arrival time; 'all' guarantees a SINGLE fixed
    # service start for every possible actual arrival, without assuming the
    # passenger can be instantaneously relocated or is already present.
    quantifier=site.get('passenger_arrival_quantifier')
    if quantifier not in ('all','exists'):return None
    if quantifier=='all':
        p=merge((b,a+wait) for a,b in arrival if b<=a+wait+EPS)
    else:
        p=merge((a,b+wait) for a,b in arrival)
    conditions=('vehicle_stop_windows_s','evidence_validity_windows_s','interface_windows_s')
    for field in conditions:
        avail=intervals(site[field],field=field)
        p=intersect(p,shift_ends_for_full_duration(avail,duration))
    avail=intervals(world_map[world],field=f'world_handoff_open_windows_s[{world}]')
    p=intersect(p,shift_ends_for_full_duration(avail,duration))
    return p


def _common_windows(site,worlds,*,post_observation=False):
    common=((-float('inf'),float('inf')),)
    unknown=False
    for world in worlds:
        item=one_world_windows(site,world,post_observation=post_observation)
        if item is None:
            unknown=True
            continue
        common=intersect(common,item)
    return None if unknown else common


def _pick(sites,worlds,*,post=False):
    unknown=[];candidates=[]
    for site in sites:
        win=_common_windows(site,worlds,post_observation=post)
        if win is None:unknown.append(site['option_id'])
        elif win:candidates.append({'option_id':site['option_id'],
                                     'service_start_windows_s':[[a,b] for a,b in win]})
    return sorted(candidates,key=lambda x:(x['service_start_windows_s'][0][0],x['option_id'])),unknown


def analyze(case:dict)->dict:
    worlds=case.get('world_support')
    sites=case.get('options')
    if not isinstance(worlds,list) or not worlds or len(worlds)!=len(set(worlds)) or any(not isinstance(x,str) or not x for x in worlds):
        raise ValueError('world_support must contain unique nonempty world IDs')
    if not isinstance(sites,list) or not sites or len(sites)!=len({s['option_id'] for s in sites}):
        raise ValueError('options must contain unique option_id values')
    candidates,unknown=_pick(sites,worlds)
    evidence={'physical_evidence_verified':False,
              'scope':'DECLARED_FINITE_WORLDS_AND_ASSERTED_TIME_WINDOWS_ONLY',
              'guarantees_real_boarding':False,
              'does_not_model_general_contingent_POMDP':True}
    result={'case_id':case.get('case_id'),'world_count':len(worlds),'option_count':len(sites),
            'fixed_candidates':candidates,'missing_input_options':unknown,
            'observation_branches':[],'evidence':evidence}
    # A fixed choice is a full support-world guarantee in the declared restricted model.
    if candidates:
        result['status']='ROBUST_FIXED_IN_DECLARED_MODEL'
        result['witness']=candidates[0]
        return result
    obs=case.get('one_observation')
    if obs is None:
        result['status']='UNKNOWN_MISSING_INPUT' if unknown else 'NO_POLICY_IN_RESTRICTED_CLASS'
        return result
    reveal_time=_num(obs.get('available_at_s'),'one_observation.available_at_s')
    outcomes=obs.get('outcomes_by_world')
    if not isinstance(outcomes,dict) or set(outcomes)!=set(worlds) or any(not isinstance(v,str) or not v for v in outcomes.values()):
        raise ValueError('every world must have one declared observable outcome')
    groups=defaultdict(list)
    for w in worlds:groups[outcomes[w]].append(w)
    all_winning=True;incomplete=False
    for outcome,group in sorted(groups.items()):
        options,unk=_pick(sites,group,post=True)
        valid=[]
        for o in options:
            # This policy class asks the passenger to remain in their declared
            # holding state before observation. If a post-observation arrival
            # bound begins BEFORE the observation is available, the supplied
            # timing witness is invalid for this restricted policy class.
            site=next(z for z in sites if z['option_id']==o['option_id'])
            arrival=site['post_observation_passenger_arrival_window_s']
            if float(arrival[0])+EPS<reveal_time:
                continue
            admissible=intersect(tuple(tuple(z) for z in o['service_start_windows_s']),
                                 ((reveal_time,float('inf')),))
            if admissible:
                valid.append({'option_id':o['option_id'],
                              'service_start_windows_s':[[a,b] for a,b in admissible]})
        if not valid:all_winning=False
        if unk:incomplete=True
        result['observation_branches'].append({'outcome':outcome,'worlds':group,
                                               'feasible_options':valid,'unknown_options':unk})
    if all_winning:
        result['status']='ONE_OBSERVATION_CAUSAL_POLICY_IN_DECLARED_MODEL'
        result['witness']={'observe_at_s':reveal_time,
                           'branches':{b['outcome']:b['feasible_options'][0] for b in result['observation_branches']}}
    elif unknown or incomplete:
        result['status']='UNKNOWN_MISSING_INPUT'
    else:
        result['status']='NO_POLICY_IN_RESTRICTED_CLASS'
    return result
