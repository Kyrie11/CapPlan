"""Independent finite-support diagnostic model of asynchronous pickup commitments.

This is an *exact, generic AND/OR reach-avoid reference for the DECLARED finite
model*, NOT a new CapPlan algorithm, PRISM reimplementation, certified continuous
motion planner, or a model of unconstrained human behavior.  Hidden physical
worlds evolve along exogenous timelines. Only requested observations can
partition belief support. Passenger moves according to the previously
acknowledged direction until the new instruction is acknowledged. Vehicle and
passenger progress concurrently at each tick.

All physical checker inputs in this diagnostic suite are either controlled
synthetic truth or explicitly independently certified. No real city/nuPlan
truth is silently created. The optimizer's objective is first robust completion
in all modeled worlds and then minimum expected action cost; non-winning
states are distinct from UNKNOWN caused by a resource cap.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from collections import defaultdict
from functools import lru_cache
from heapq import heappush, heappop
from typing import Any
import math


@dataclass(frozen=True)
class State:
    tick: int
    passenger: str
    walked: float
    waited: int
    active_instruction: str | None
    pending_instruction: str | None
    acknowledgement_due: int | None
    vehicle: str
    vehicle_target: str | None
    support: tuple[str, ...]
    # evidence tuples: site, observed status, time of observation, guaranteed expiry
    evidence: tuple[tuple[str, bool, int, int], ...] = ()


@dataclass(frozen=True)
class Policy:
    action: str
    expected_cost: float
    branches: tuple[tuple[str, 'Policy | None'], ...] = ()


@dataclass
class Result:
    status: str
    first_safe_actions: tuple[str, ...]
    best_action: str | None
    best_cost: float | None
    explored_states: int
    evaluated_actions: int
    motion_queries: int
    motion_unique_queries: int
    unknown_due_to_cap: bool
    exact_action_set_complete: bool = False
    policy_cost_optimality_certified: bool = False
    policy_prefix: tuple[dict[str, Any], ...] = ()
    model_scope: str = 'DECLARED_FINITE_SUPPORT_DIAGNOSTIC_ONLY'


class Incomplete(Exception):
    pass


class FiniteCommitmentModel:
    """Finite time-discretized joint passenger-vehicle model with belief support.

    Required in scenario:
      pedestrian_graph: node -> [{to, length_m, min_width_m?}, ...]
      vehicle_graph: pose/node -> [neighbor, ...]
      sites: site -> {passenger_node?, interface_ok?, motion_ok?}
      worlds: [{id, probability, open: {site:[booleans per tick]}}]
      passenger: {start, walked_m?, waited_ticks?, active_instruction?}
      vehicle: {start, target?}
      horizon_ticks, max_distance_m, max_wait_ticks, ack_delay_ticks,
      evidence_ttl_ticks, boarding_ticks, required_width_m

    Optional initial_evidence {site: {open, observed_at, valid_until}}.
    All worlds must explicitly cover the entire horizon for each candidate.
    """
    def __init__(self, scenario: dict[str, Any]):
        self.case = scenario
        self.sites = scenario['sites']
        self.pgraph = scenario['pedestrian_graph']
        self.vgraph = scenario['vehicle_graph']
        self.horizon = int(scenario['horizon_ticks'])
        self.max_dist = float(scenario['max_distance_m'])
        self.max_wait = int(scenario['max_wait_ticks'])
        self.width = float(scenario.get('required_width_m', 0))
        self.ack = int(scenario['ack_delay_ticks'])
        self.ttl = int(scenario['evidence_ttl_ticks'])
        self.board_ticks = int(scenario['boarding_ticks'])
        self.require_evidence = bool(scenario.get('require_evidence_for_board', True))
        self.worlds = {w['id']:w for w in scenario['worlds']}
        if not self.sites or not self.worlds or len(self.worlds)!=len(scenario['worlds']):
            raise ValueError('non-empty sites and unique world IDs required')
        if min(self.horizon,self.board_ticks) < 1 or min(self.max_dist,self.max_wait,self.width,self.ack,self.ttl) < 0:
            raise ValueError('invalid budgets, horizon, communication or boarding delay')
        if any(not math.isfinite(x) for x in (self.max_dist,self.width)):
            raise ValueError('nonfinite budget')
        if abs(sum(float(w['probability']) for w in self.worlds.values())-1)>1e-7:
            raise ValueError('world probabilities must sum to 1')
        for w in self.worlds.values():
            if float(w['probability']) <= 0: raise ValueError('worlds require strictly positive probability')
            for site in self.sites:
                trace = w.get('open',{}).get(site)
                if not isinstance(trace,list) or len(trace) < self.horizon+1 or any(type(x) is not bool for x in trace):
                    raise ValueError(f'missing complete availability trace for {w["id"]}:{site}')
        for node, edges in self.pgraph.items():
            for edge in edges:
                if edge['to'] not in self.pgraph or not math.isfinite(float(edge['length_m'])) or float(edge['length_m'])<=0:
                    raise ValueError(f'invalid pedestrian edge {node}: {edge}')
        for node, nxt in self.vgraph.items():
            if any(x not in self.vgraph for x in nxt):raise ValueError('invalid vehicle graph edge')
        p=scenario['passenger'];v=scenario['vehicle'];start=int(scenario.get('start_tick',0))
        if p['start'] not in self.pgraph or v['start'] not in self.vgraph:
            raise ValueError('initial position missing from graph')
        if p.get('active_instruction') not in (None,*self.sites):
            raise ValueError('initial guidance must name a site')
        e=[]
        for site,data in scenario.get('initial_evidence',{}).items():
            if site not in self.sites:raise ValueError('unknown initial evidence site')
            if type(data['open']) is not bool:raise ValueError('evidence status not bool')
            if int(data['observed_at']) > start or int(data['valid_until']) < int(data['observed_at']):
                raise ValueError('future / inconsistent initial evidence')
            # Evidence is not a magic certificate: incompatible worlds cannot be declared observed.
            if any(self.worlds[wi]['open'][site][int(data['observed_at'])] != data['open'] for wi in self.worlds):
                raise ValueError('initial evidence contradicts a world in the belief support')
            e.append((site,data['open'],int(data['observed_at']),int(data['valid_until'])))
        self.initial=State(start,p['start'],float(p.get('walked_m',0)),int(p.get('waited_ticks',0)),
                           p.get('active_instruction'),None,None,v['start'],v.get('target'),
                           tuple(sorted(self.worlds)),tuple(sorted(e)))
        if not (0<=start<self.horizon and 0<=self.initial.walked<=self.max_dist and
                0<=self.initial.waited<=self.max_wait):raise ValueError('invalid initial state')
        self.unknown_interface_sites=tuple(z for z,x in self.sites.items()
                                           if type(x.get('motion_ok')) is not bool
                                           or type(x.get('interface_ok')) is not bool)
        self._memo_route={}
        self._motion_cache={}
        self._motion_queries=0

    def _next_pedestrian(self, src: str, target: str):
        goal=self.sites[target].get('passenger_node',target)
        key=(src,goal,self.width)
        if key not in self._memo_route:
            heap=[(0.,src,None)];best={src:0.};first={src:None}
            while heap:
                d,u,_=heappop(heap)
                if d > best[u]+1e-9:continue
                for e in self.pgraph[u]:
                    if float(e.get('min_width_m',float('inf'))) + 1e-9 < self.width or not bool(e.get('step_free',True)):
                        continue
                    v=e['to'];cost=d+float(e['length_m'])
                    if cost+1e-9<best.get(v,float('inf')):
                        best[v]=cost;first[v]=first[u] if first[u] is not None else (v,float(e['length_m']))
                        heappush(heap,(cost,v,None))
            self._memo_route[key]=first.get(goal) if src!=goal else None
        return self._memo_route[key]

    def _next_vehicle(self,src,target):
        if src==target:return src
        seen={src};queue=[(src,None)]
        for u,f in queue:
            for v in sorted(self.vgraph[u]):
                if v in seen:continue
                seen.add(v);n=f or v
                if v==target:return n
                queue.append((v,n))
        return src  # declared vehicle graph has no route

    def _posterior_mass(self,ids,prior):
        denom=sum(float(self.worlds[x]['probability']) for x in prior)
        return sum(float(self.worlds[x]['probability']) for x in ids)/denom

    def _motion_ok(self,site):
        # Counts a model-checker request; when specified, it is a precomputed
        # simulated abstraction, not a real CARLA / nuPlan continuous proof.
        self._motion_queries+=1
        if site not in self._motion_cache:
            v=self.sites[site].get('motion_ok')
            self._motion_cache[site]=v if type(v) is bool else None
        return self._motion_cache[site]

    def _board_possible(self,s:State,site:str):
        if s.tick+self.board_ticks>self.horizon or s.pending_instruction is not None:
            return False
        if (s.passenger!=self.sites[site].get('passenger_node',site) or s.vehicle!=site
            or (s.active_instruction is not None and s.active_instruction!=site)):
            return False
        if self._motion_ok(site) is not True or self.sites[site].get('interface_ok') is not True:
            return False
        if self.require_evidence and not any(z==site and status and obs<=s.tick and expiry>=s.tick+self.board_ticks
                                             for z,status,obs,expiry in s.evidence):
            return False
        # Even a fresh report cannot substitute for a safe future time window.
        return all(all(self.worlds[w]['open'][site][t]
                       for t in range(s.tick,s.tick+self.board_ticks+1)) for w in s.support)

    def actions(self,s:State):
        if s.tick>=self.horizon:return ()
        actions=['hold']
        if s.pending_instruction is None:
            actions += ['guide:'+z for z in sorted(self.sites) if z!=s.active_instruction]
        actions += ['vehicle:'+z for z in sorted(self.sites)
                    if z in self.vgraph and z!=s.vehicle_target]
        if s.vehicle in self.sites:
            actions.append('verify:'+s.vehicle)
            actions.append('board:'+s.vehicle)
        return tuple(actions)

    def successors(self,s:State,act:str):
        """Returns (conditional branches, local nonnegative cost), or None.

        States in each branch are physically the same observed history, with
        different compatible world supports only after a completed observation.
        """
        if act not in self.actions(s):return None
        if act.startswith('board:'):
            if not self._board_possible(s,act.split(':',1)[1]):
                return None
            return ((('success',None),),0.0)
        p_pending=s.pending_instruction;due=s.acknowledgement_due
        v_target=s.vehicle_target
        verified=None
        if act.startswith('guide:'):
            p_pending=act.partition(':')[2]
            due=s.tick+self.ack
        elif act.startswith('vehicle:'):
            v_target=act.partition(':')[2]
        elif act.startswith('verify:'):
            verified=act.partition(':')[2]
            if verified!=s.vehicle:return None
        active=s.active_instruction
        if p_pending is not None and due is not None and due<=s.tick:
            active=p_pending;p_pending=None;due=None
        loc=s.passenger;walked=s.walked;waited=s.waited
        # If ack_delay==0, new command can affect the current tick.
        # Otherwise, the prior instruction governs until ACK takes effect.
        if active is not None and loc!=self.sites[active].get('passenger_node',active):
            move=self._next_pedestrian(loc,active)
            if move is None:return None
            nxt,dist=move;loc=nxt;walked=round(walked+dist,6)
        else:
            waited+=1
        veh=self._next_vehicle(s.vehicle,v_target) if v_target is not None else s.vehicle
        tick=s.tick+1
        if p_pending is not None and due is not None and due<=tick:
            active=p_pending;p_pending=None;due=None
        if walked>self.max_dist+1e-8 or waited>self.max_wait or tick>self.horizon:
            return None
        local_cost=1.0+0.02*max(0,walked-s.walked)+0.02*int(veh!=s.vehicle)
        if act.startswith('guide:'):local_cost+=0.05
        if verified is None:
            return ((('unobserved',replace(s,tick=tick,passenger=loc,walked=walked,waited=waited,
                                          active_instruction=active,pending_instruction=p_pending,
                                          acknowledgement_due=due,vehicle=veh,vehicle_target=v_target)),),local_cost)
        # The sensor reports after one tick. The observed status is the *new*
        # state of the site; no future event/hidden world ID is revealed.
        partitions=defaultdict(list)
        for w in s.support:
            partitions[self.worlds[w]['open'][verified][tick]].append(w)
        branches=[]
        for value,subset in sorted(partitions.items()):
            ev={x[0]:x for x in s.evidence}
            ev[verified]=(verified,value,tick,tick+self.ttl)
            new=replace(s,tick=tick,passenger=loc,walked=walked,waited=waited,
                        active_instruction=active,pending_instruction=p_pending,
                        acknowledgement_due=due,vehicle=veh,vehicle_target=v_target,
                        support=tuple(sorted(subset)),evidence=tuple(sorted(ev.values())))
            branches.append((f'OBS:{verified}:{value}',new))
        return tuple(branches),local_cost+0.25


class GenericSupportSolver:
    """Domain-agnostic AND/OR finite-support reference, not ICRA implementation.

    The oracle gives a decision or UNKNOWN if hard state/action limits prevent
    definitive rejection. Unbounded (within finite horizon) exploration is
    exact for this DECLARED finite model; action costs are secondary.
    """
    def __init__(self,model:FiniteCommitmentModel,max_states:int=40000):
        self.m=model;self.max_states=max_states
        self.memo={};self.expanded=0;self.actions_tested=0;self.cap_hit=False

    def _solve(self,state:State):
        if state in self.memo:return self.memo[state]
        if self.expanded>=self.max_states:
            self.cap_hit=True;return 'UNKNOWN',None
        self.expanded+=1
        if state.tick>=self.m.horizon:
            self.memo[state]=('LOSE',None);return self.memo[state]
        wins=[];any_unknown=False
        for action in self.m.actions(state):
            self.actions_tested+=1
            out=self.m.successors(state,action)
            if out is None:continue
            branches,local=out
            child_results=[];valid=True;cost=local
            for label,nxt in branches:
                if nxt is None:
                    child_results.append((label,None))
                    continue
                status,policy=self._solve(nxt)
                if status=='UNKNOWN':any_unknown=True;valid=False;break
                if status!='WIN':valid=False;break
                weight=self.m._posterior_mass(nxt.support,state.support)
                cost+=weight*policy.expected_cost
                child_results.append((label,policy))
            if valid:
                wins.append(Policy(action,round(cost,6),tuple(child_results)))
        if wins:
            ans=('WIN',min(wins,key=lambda p:(p.expected_cost,p.action)))
        elif any_unknown:
            ans=('UNKNOWN',None)
        else:
            ans=('LOSE',None)
        self.memo[state]=ans
        return ans

    def solve(self,start:State|None=None):
        state=start or self.m.initial
        status,policy=self._solve(state)
        if status=='LOSE' and self.m.unknown_interface_sites:
            status='UNKNOWN_EVIDENCE'
        if status=='WIN':
            safe=[]
            # We enumerate safe first actions from the finished cached search;
            # if an exploration cap was hit, the list is a partial safe subset.
            for action in self.m.actions(state):
                tr=self.m.successors(state,action)
                if tr is None:continue
                branches,_=tr
                if all(nxt is None or self.memo.get(nxt,('UNKNOWN',None))[0]=='WIN' for _,nxt in branches):
                    safe.append(action)
        else:safe=[]
        prefix=[]
        cur=policy
        for _ in range(12):
            if cur is None:break
            prefix.append({'action':cur.action, 'branch_labels':[lab for lab,_ in cur.branches]})
            cur=next((next_policy for _,next_policy in cur.branches if next_policy is not None),None)
        return Result(status=status,first_safe_actions=tuple(safe),
                      best_action=policy.action if policy else None,
                      best_cost=policy.expected_cost if policy else None,
                      explored_states=self.expanded,evaluated_actions=self.actions_tested,
                      motion_queries=self.m._motion_queries,
                      motion_unique_queries=len(self.m._motion_cache),
                      unknown_due_to_cap=self.cap_hit,
                      exact_action_set_complete=(not self.cap_hit and status in ('WIN','LOSE')),
                      policy_cost_optimality_certified=(status=='WIN' and not self.cap_hit),
                      policy_prefix=tuple(prefix))


def cheapest_heuristic_actions(model:FiniteCommitmentModel):
    """First-step suggestions only: NOT policies or full shielding baselines."""
    targets=sorted(model.sites)
    # A nearest-site baseline must use the FULL width-constrained shortest
    # pedestrian distance, not the first edge length of a multi-hop route.
    def route_distance(site: str) -> float:
        goal=model.sites[site].get('passenger_node',site)
        start=model.initial.passenger
        if start==goal:return 0.0
        queue=[(0.0,start)];best={start:0.0}
        while queue:
            d,u=heappop(queue)
            if d>best[u]+1e-9:continue
            if u==goal:return d
            for e in model.pgraph[u]:
                if (float(e.get('min_width_m',float('inf')))+1e-9 < model.width
                        or not bool(e.get('step_free',True))):
                    continue
                nxt=e['to'];nd=d+float(e['length_m'])
                if nd+1e-9<best.get(nxt,float('inf')):
                    best[nxt]=nd;heappush(queue,(nd,nxt))
        return float('inf')
    distances={z:route_distance(z) for z in targets}
    nearest=min(targets,key=lambda z:(distances[z],z))
    # Vehicle-first allows observing the closest candidate when already there.
    preferred_vehicle=('verify:'+model.initial.vehicle if model.initial.vehicle in model.sites
                       else 'vehicle:'+nearest)
    known_safe=[]
    for z in targets:
        e=model.case.get('initial_evidence',{}).get(z,{})
        if e.get('open') is True and e.get('valid_until',-1)>=model.initial.tick+model.board_ticks:
            known_safe.append(z)
    anchor=min(known_safe,key=lambda z:(distances[z],z)) if known_safe else nearest
    return {'nearest_static':'guide:'+nearest,
            'vehicle_first':preferred_vehicle,
            'fixed_evidence_anchor':'guide:'+anchor}


def verify_policy_in_all_worlds(model:FiniteCommitmentModel, policy:Policy|None):
    """Replay *one shared policy* across each declared possible world.

    Observation branches are selected only from available observation labels,
    never by hidden world ID before observation. This tests witness consistency
    but does not constitute an independent checker of CARLA/nuPlan physics.
    """
    if policy is None:return None
    results={}
    for wid in model.initial.support:
        state=model.initial;p=policy;acts=[]
        for step in range(model.horizon+2):
            if p is None:raise AssertionError('no policy continuation before terminal')
            trans=model.successors(state,p.action)
            if trans is None:raise AssertionError('policy attempted disabled action')
            branches,_=trans;matching=[]
            for label,nxt in branches:
                if nxt is None or wid in nxt.support:
                    matching.append((label,nxt))
            if len(matching)!=1:raise AssertionError('policy has ambiguous observation')
            label,nxt=matching[0]
            policy_branches=dict(p.branches)
            if label not in policy_branches:raise AssertionError('missing contingent branch')
            acts.append({'tick':state.tick,'action':p.action,'observed_branch':label})
            if nxt is None:
                results[wid]={'success':True,'actions':acts,'walked_m':state.walked,
                             'waited_ticks':state.waited,'finish_tick':state.tick+model.board_ticks}
                break
            state=nxt;p=policy_branches[label]
        else:raise AssertionError('policy did not terminate within horizon')
    return results
