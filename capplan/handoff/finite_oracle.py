"""Small exact belief-support AND/OR oracle for controlled two-anchor pickups.

The world support is fixed and finite.  Neither world identity nor future
availability is observable to the policy.  A verification partitions worlds
ONLY by an observation available at its completion time.  An attempted handoff
partitions worlds by its actual outcome.  Past passenger distance and wait
always remain in the state, including on failure.  Search is exhaustive over
this *declared* action set, not over a whole city or arbitrary aid services.

This module produces conditional model evidence, never nuPlan vehicle safety
proof or independent real-world handoff confirmations.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any
import math


@dataclass(frozen=True)
class State:
    location: str
    distance: float
    clock: float
    support: tuple[str, ...]
    checked: bool = False
    waited_s: float = 0.0


@dataclass(frozen=True)
class Decision:
    action: str
    expected_cost: float
    branches: tuple[tuple[str, Any], ...] = ()


class FiniteHandoffOracle:
    def __init__(self, overlay: dict, speed_mps: float = 1.0, boarding_s: float = 10.0,
                 waiting_cost_mps: float = 0.04, verification_cost_m: float = 4.0):
        if speed_mps <= 0 or boarding_s < 0 or waiting_cost_mps < 0:
            raise ValueError('invalid model parameters')
        self.overlay = overlay
        cs = overlay['candidate_handoffs']
        if len(cs) != 2 or cs[0]['anchor_id'] == cs[1]['anchor_id']:
            raise ValueError('oracle requires exactly two distinct anchors')
        self.a, self.b = (c['anchor_id'] for c in cs)
        self.distance = {'O': {self.a: float(cs[0]['direct_passenger_distance_m']),
                               self.b: float(cs[1]['direct_passenger_distance_m'])},
                         self.a: {self.b: float(cs[0]['from_a_to_b_distance_m'])}}
        self.budget = float(overlay['capability_budget']['distance_budget_m'])
        self.wait_budget = overlay['capability_budget'].get('wait_budget_s')
        self.speed = speed_mps
        self.boarding = boarding_s
        self.wait_cost = waiting_cost_mps
        self.verify_cost = verification_cost_m
        self.ack = float(overlay['observation_model']['instruction_acknowledgement_delay_s'])
        self.delay = float(overlay['observation_model']['availability_observation_delay_s'])
        self.worlds = {w['world_id']: w for w in overlay['world_support']}
        if len(self.worlds) != len(overlay['world_support']) or not self.worlds:
            raise ValueError('world identifiers must be unique')
        for wid, w in self.worlds.items():
            for anchor in (self.a, self.b):
                trace = w['availability_trace'].get(anchor)
                if not trace or any(float(trace[i][0]) > float(trace[i+1][0])
                                    for i in range(len(trace)-1)):
                    raise ValueError(f'missing or out-of-order availability trace {wid}:{anchor}')
        self.initial = State('O', float(overlay['initial_execution_state']['walked_or_rolled_m']),
                             float(overlay['initial_execution_state']['timestamp_s']),
                             tuple(sorted(self.worlds)),False,
                             float(overlay['initial_execution_state'].get('waited_s',0.0)))
        if self.initial.distance > self.budget:
            raise ValueError('initial burden exceeds declared contract')
        self.total_prob = sum(float(w['probability']) for w in self.worlds.values())
        if abs(self.total_prob-1) > 1e-6:
            raise ValueError('world mass must be one')

    def status(self, world: str, anchor: str, t: float) -> bool:
        trace = self.worlds[world]['availability_trace'][anchor]
        val = None
        for at, available in trace:
            if float(at) <= t + 1e-9:
                val = bool(available)
            else:
                break
        # No information prior to the first declared trace instant.
        if val is None:
            raise ValueError('availability trace does not cover query time')
        return val

    def available_for_boarding(self, world: str, anchor: str, begin: float) -> bool:
        # Check BOTH initial availability and all changes through handoff end.
        finish = begin + self.boarding
        times = [begin, finish]
        times += [float(t) for t, _ in self.worlds[world]['availability_trace'][anchor]
                  if begin < float(t) <= finish]
        return all(self.status(world, anchor, t) for t in times)

    def arrival(self, state: State, target: str):
        leg = self.distance.get(state.location, {}).get(target)
        if leg is None:
            return None
        burden = state.distance + leg
        arrived = state.clock + self.ack + leg/self.speed
        if burden > self.budget + 1e-9:
            return None
        if self.wait_budget is not None and state.waited_s + self.ack > float(self.wait_budget)+1e-9:
            return None
        return burden, arrived, leg

    def conditional_mass(self, subset: tuple[str, ...], support: tuple[str, ...]) -> float:
        numerator = sum(float(self.worlds[w]['probability']) for w in subset)
        denom = sum(float(self.worlds[w]['probability']) for w in support)
        return numerator / denom if denom > 0 else 0.0

    def verify_time(self, state: State) -> float:
        """Use a PREDECLARED request schedule; never inspect hidden event traces.

        The action can be scheduled for a known observation slot, or requested
        immediately if no slot is declared. The observation itself is delayed
        by the configured latency. Future state-transition times are hidden.
        """
        scheduled = float(self.overlay['observation_model'].get(
            'scheduled_observation_request_s', state.clock))
        if not math.isfinite(scheduled) or scheduled < 0:
            raise ValueError('invalid observation request schedule')
        return max(state.clock, scheduled) + self.delay

    def solve(self, start_state: State | None = None):
        explored = 0

        @lru_cache(None)
        def dp(state: State):
            nonlocal explored
            explored += 1
            proposals = []
            for target in (self.a, self.b):
                step = self.arrival(state, target)
                if step is None:
                    continue
                burden, arrived, leg = step
                successes = tuple(w for w in state.support if self.available_for_boarding(w, target, arrived))
                failures = tuple(w for w in state.support if w not in successes)
                branch_cost = 0.0
                branches = [('confirmed', {'worlds': successes})] if successes else []
                if failures:
                    nxt = State(target, burden, arrived + self.boarding, failures, state.checked,
                                state.waited_s+self.ack)
                    continuation = dp(nxt)
                    if continuation is None:
                        continue
                    branch_cost = self.conditional_mass(failures, state.support) * continuation.expected_cost
                    branches.append(('handoff_failed', {'worlds': failures, 'next': continuation}))
                proposals.append(Decision('guide:'+target, leg + self.ack*self.wait_cost + branch_cost,
                                          tuple(branches)))
            if not state.checked and state.location == 'O':
                observed_at = self.verify_time(state)
                waited = observed_at-state.clock
                if self.wait_budget is None or state.waited_s+waited <= float(self.wait_budget) + 1e-9:
                    partitions: dict[bool,list[str]] = {}
                    for w in state.support:
                        partitions.setdefault(self.status(w, self.a, observed_at), []).append(w)
                    verification_cost = self.verify_cost + waited*self.wait_cost
                    branches = []
                    for observation, group in sorted(partitions.items()):
                        group = tuple(group)
                        continuation = dp(State(state.location, state.distance, observed_at, group, True,
                                                state.waited_s+waited))
                        if continuation is None:
                            break
                        verification_cost += self.conditional_mass(group, state.support)*continuation.expected_cost
                        branches.append((f'observed_A_{observation}', {'worlds': group, 'next': continuation}))
                    else:
                        proposals.append(Decision('verify_before_guidance', verification_cost, tuple(branches)))
            if not proposals:
                return None
            return min(proposals, key=lambda d: (d.expected_cost, d.action))

        policy = dp(start_state or self.initial)
        return policy, explored

    def guidance_has_causal_recourse(self, state: State, target: str) -> bool:
        """Exact finite-support action shield; success and failure are observable.

        Returns False if ANY possible world can leave no feasible continuation
        after this irreversible passenger movement.  No belief reset occurs.
        """
        step=self.arrival(state,target)
        if step is None:
            return False
        burden,arrival,_=step
        failed=tuple(w for w in state.support if not self.available_for_boarding(w,target,arrival))
        if not failed:
            return True
        successor=State(target,burden,arrival+self.boarding,failed,state.checked,
                        state.waited_s+self.ack)
        return self.solve(successor)[0] is not None

    def first_commitment_diagnostics(self, rollout: dict, world: str) -> dict:
        """Model-derived FCR/PCRL for a recorded baseline action sequence."""
        actions=rollout.get('actions',[])
        guides=[a for a in actions if a['action']=='guide']
        if not guides:
            return {'commitment_issued':False,'false_commit':None,'recourse_lost':None}
        checked=next((a for a in actions if a['action']=='verify'),None)
        if checked is not None:
            checked_at=float(checked['time_s'])
            observed=self.status(world,self.a,checked_at)
            support=tuple(w for w in self.initial.support
                          if self.status(w,self.a,checked_at)==observed)
            state=State('O',self.initial.distance,checked_at,support,True,
                        self.initial.waited_s+checked_at-self.initial.clock)
        else:
            state=self.initial
        first=guides[0]
        target=first['to_anchor']
        action_robust=self.guidance_has_causal_recourse(state,target)
        if rollout.get('violated_actual_budget'):
            lost=True
        elif not first.get('handoff_candidate_confirmed'):
            move=self.arrival(state,target)
            if move is None:
                lost=True
            else:
                burden,arrived,_=move
                failed=tuple(w for w in state.support
                             if not self.available_for_boarding(w,target,arrived))
                after=State(target,burden,arrived+self.boarding,failed,state.checked,
                            state.waited_s+self.ack)
                lost=self.solve(after)[0] is None
        else:
            lost=False
        initial_recoverable=self.solve()[0] is not None
        return {'commitment_issued':True,'false_commit':not action_robust,
                'recourse_lost':bool(initial_recoverable and lost)}

    def simulate(self, policy: Decision | None, world: str) -> dict:
        """Execute a NON-clairvoyant fixed policy in one declared world."""
        if world not in self.worlds:
            raise KeyError(world)
        state = self.initial
        steps = []
        while policy is not None:
            if policy.action == 'verify_before_guidance':
                observed_at = self.verify_time(state)
                if state.location != 'O':
                    raise RuntimeError('verification requires still-at-origin')
                observed = self.status(world, self.a, observed_at)
                steps.append({'action':'verify','time_s':observed_at, 'observed_a':observed})
                label = f'observed_A_{observed}'
                matching = [details for key,details in policy.branches if key == label]
                if not matching or world not in matching[0]['worlds']:
                    raise RuntimeError('oracle policy observation mismatch')
                state = State(state.location, state.distance, observed_at, tuple(matching[0]['worlds']), True,
                              state.waited_s+observed_at-state.clock)
                policy = matching[0]['next']
                continue
            target = policy.action.partition(':')[2]
            movement = self.arrival(state, target)
            if movement is None:
                raise RuntimeError('oracle issued infeasible commitment')
            burden, arrived, leg = movement
            confirmed = self.available_for_boarding(world, target, arrived)
            steps.append({'action':'guide','from_anchor':state.location,'to_anchor':target,
                          'distance_m':leg,'cumulative_distance_m':burden,
                          'time_s':arrived,'handoff_candidate_confirmed':confirmed})
            if confirmed:
                return {'candidate_service_success':True,'executed_passenger_distance_m':burden,
                        'passenger_waiting_time_s':max(0, arrived-self.initial.clock-(burden-self.initial.distance)/self.speed),
                        'passenger_redirections':int(len([x for x in steps if x['action']=='guide'])>1),
                        'actions':steps}
            failures = [details for key,details in policy.branches if key=='handoff_failed']
            if not failures or world not in failures[0]['worlds']:
                break
            state = State(target, burden, arrived+self.boarding, tuple(failures[0]['worlds']), state.checked,
                          state.waited_s+self.ack)
            policy = failures[0]['next']
        return {'candidate_service_success':False,'executed_passenger_distance_m':state.distance,
                'actions':steps}


def _estimate_handoff(o: FiniteHandoffOracle, world: str, target: str,
                      *, use_ledger: bool = True, verify: bool = False):
    """Reproducible simple policy baselines, not full POMDP shielding."""
    state = o.initial
    actions = []
    if verify:
        at = o.verify_time(state)
        actions.append({'action':'verify','time_s':at})
        waited=state.waited_s+at-state.clock
        if o.wait_budget is not None and waited>float(o.wait_budget)+1e-9:
            return {'candidate_service_success':False,'violated_actual_budget':False,
                    'executed_passenger_distance_m':state.distance,'actions':actions,
                    'infeasible_wait_budget':True}
        state = State('O',state.distance,at,state.support,True,waited)
    if target == 'adaptive':
        target = o.a if o.status(world, o.a, state.clock) else o.b
    attempts = [target] + ([o.b] if target == o.a else [])
    for t in attempts:
        leg = o.distance.get(state.location, {}).get(t)
        if leg is None:
            break
        after = state.distance+leg
        reset_model_accepts = leg<=o.budget+1e-9
        actual_model_accepts = after<=o.budget+1e-9
        if not (reset_model_accepts if not use_ledger else actual_model_accepts):
            actions.append({'action':'refuse_guidance','target':t,'reason':'budget'})
            break
        at = state.clock + o.ack + leg/o.speed
        okay = o.available_for_boarding(world,t,at)
        actions.append({'action':'guide','to_anchor':t,'distance_m':leg,
                        'cumulative_distance_m':after,'handoff_candidate_confirmed':okay})
        state = State(t,after,at+o.boarding,state.support,state.checked,state.waited_s+o.ack)
        if okay:
            return {'candidate_service_success':actual_model_accepts,
                    'violated_actual_budget':not actual_model_accepts,'executed_passenger_distance_m':after,
                    'actions':actions}
    return {'candidate_service_success':False,'violated_actual_budget':state.distance>o.budget+1e-9,
            'executed_passenger_distance_m':state.distance,'actions':actions}


def evaluate_baselines(oracle: FiniteHandoffOracle) -> dict[str,dict]:
    """Five policy categories. The generic shield is a simple robust-anchor proxy.

    Do not misrepresent this proxy as a published resource-constrained POMDP
    implementation; an actual strong shielding baseline remains future work.
    """
    policies, explored = oracle.solve()
    out = {}
    for name in ('exact_finite_support','reset_and_replan','actual_ledger_myopic',
                 'verify_then_guide','robust_fixed_anchor','generic_resource_shield_proxy'):
        rollouts = {}
        for world in oracle.worlds:
            if name=='exact_finite_support':
                result = oracle.simulate(policies,world)
            elif name=='reset_and_replan':
                result = _estimate_handoff(oracle,world,oracle.a,use_ledger=False)
            elif name=='actual_ledger_myopic':
                result = _estimate_handoff(oracle,world,oracle.a,use_ledger=True)
            elif name=='verify_then_guide':
                result = _estimate_handoff(oracle,world,'adaptive',verify=True)
            else:
                # B is the robust anchor in this declared two-anchor construction.
                result = _estimate_handoff(oracle,world,oracle.b)
            result['world_probability']=float(oracle.worlds[world]['probability'])
            diagnostics=oracle.first_commitment_diagnostics(result,world)
            result.update(diagnostics)
            rollouts[world]=result
        out[name] = {
            'rollouts': rollouts,
            'weighted_candidate_success_rate': sum(v['world_probability']*int(v['candidate_service_success'])
                                                    for v in rollouts.values()),
            'weighted_burden_m': sum(v['world_probability']*v['executed_passenger_distance_m']
                                     for v in rollouts.values()),
            'world_support_guaranteed': all(v['candidate_service_success'] for v in rollouts.values()),
            'budget_breach_worlds':sum(bool(v.get('violated_actual_budget')) for v in rollouts.values()),
            'weighted_false_commit_rate': (
                sum(v['world_probability']*int(v['false_commit']) for v in rollouts.values()
                    if v['false_commit'] is not None)
                / sum(v['world_probability'] for v in rollouts.values()
                      if v['false_commit'] is not None)
                if any(v['false_commit'] is not None for v in rollouts.values()) else None),
            'weighted_post_commitment_recourse_loss_rate': (
                sum(v['world_probability']*int(v['recourse_lost']) for v in rollouts.values()
                    if v['recourse_lost'] is not None)
                / sum(v['world_probability'] for v in rollouts.values()
                      if v['recourse_lost'] is not None)
                if any(v['recourse_lost'] is not None for v in rollouts.values()) else None),
        }
    out['exact_finite_support']['expanded_belief_states']=explored
    out['exact_finite_support']['policy']=_decision_to_json(policies)
    return out


def _decision_to_json(node):
    if node is None:
        return None
    return {'action':node.action,'expected_cost':node.expected_cost,
            'branches':[{'observation':label,'worlds':list(details.get('worlds',())),
                         'next':_decision_to_json(details['next']) if 'next' in details else None}
                        for label,details in node.branches]}
