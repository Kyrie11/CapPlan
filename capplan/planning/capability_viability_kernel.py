"""Proof-carrying capability viability kernel for passenger-complete TSBS.

V5 replaces V4's independently relaxed Capability Continuation Envelope with a
backward set of *concrete structurally executable suffix witnesses*.  A forward
TSBS label may be pruned only when one of two exact conditions holds relative
to the frozen candidate-transition graph and depth bound:

1. no structurally executable suffix reaches the accepting destination; or
2. every enumerated structurally executable suffix fails when replayed with the
   label's actual typed ledger under the same capability/resource semantics as
   forward TSBS.

The kernel is proof-carrying: structural dead ends propagate the concrete
transition-test witness that blocks all suffixes, rather than emitting V4's
generic ``continuation_reachability`` pseudo-resource.  Typed dead ends use the
real violation produced by exact suffix replay.  If suffix enumeration exceeds
``max_paths_per_state`` the state is marked overflow and typed pruning is
*disabled* there, preserving soundness.

This module deliberately does not introduce a learned hard-feasibility source.
Learning remains an ordering aid; executable passenger capability semantics
remain authoritative.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Mapping, Sequence, Set, Tuple

from capplan.data.schemas import CandidateTransition, ViolationRecord
from capplan.models.predictors import TransitionPrediction
from capplan.semantics.service_automaton import PHASE_INDEX, ServiceAutomaton

State = Tuple[str, str]
EPS = 1e-9


@dataclass(frozen=True)
class SuffixWitness:
    transition_ids: Tuple[str, ...]
    cost: float


@dataclass
class CapabilityViabilityKernel:
    """Backward executable suffix set with concrete proof witnesses."""

    suffixes: Dict[State, Tuple[SuffixWitness, ...]] = field(default_factory=dict)
    reachable: Dict[State, bool] = field(default_factory=dict)
    overflow_states: Set[State] = field(default_factory=set)
    structural_failure_witness: Dict[State, ViolationRecord] = field(default_factory=dict)
    # V6: canonical rejected-branch proof available from every state, including
    # states that remain structurally reachable.  V5 only propagated witnesses
    # through structural dead ends, which hid interface/physical failures when
    # typed pruning cut a still-reachable subtree.
    downstream_failure_witness: Dict[State, ViolationRecord] = field(default_factory=dict)
    # Direct rejected hard branch at each state and the exact hard-valid
    # adjacency are exported for V6 conditional proof-precondition compilation.
    # Unlike ``downstream_failure_witness``, these primitives do not claim that
    # a downstream witness is reachable under a particular passenger ledger.
    direct_failure_witness: Dict[State, ViolationRecord] = field(default_factory=dict)
    valid_outgoing_ids: Dict[State, Tuple[str, ...]] = field(default_factory=dict)
    edge_by_id: Dict[str, CandidateTransition] = field(default_factory=dict)
    n_states: int = 0
    n_valid_edges: int = 0
    n_invalid_edges: int = 0
    max_paths_per_state: int = 0
    max_depth: int = 0

    def is_reachable(self, state: State) -> bool:
        return bool(self.reachable.get(state, False))

    def state_suffixes(self, state: State) -> Tuple[SuffixWitness, ...]:
        return self.suffixes.get(state, ())

    def overflowed(self, state: State) -> bool:
        return state in self.overflow_states

    def failure_witness(self, state: State) -> ViolationRecord | None:
        return self.structural_failure_witness.get(state)

    def proof_envelope_witness(self, state: State) -> ViolationRecord | None:
        return self.downstream_failure_witness.get(state)


def _state_from(e: CandidateTransition) -> State:
    return (str(e.from_anchor), str(e.from_phase))


def _state_to(e: CandidateTransition) -> State:
    return (str(e.to_anchor), str(e.to_phase))


def _violation_key(v: ViolationRecord) -> Tuple[float, float, int, str]:
    # Match the planner/oracle certificate ordering and add transition id only as
    # a deterministic final tie-breaker.
    return (
        float(v.signed_margin),
        -float(v.confidence),
        PHASE_INDEX.get(str(v.phase), 999),
        str(v.transition_id),
    )


def _better_violation(a: ViolationRecord | None, b: ViolationRecord | None) -> ViolationRecord | None:
    if a is None:
        return b
    if b is None:
        return a
    return a if _violation_key(a) <= _violation_key(b) else b


def _hard_edge_violation(
    e: CandidateTransition,
    automaton: ServiceAutomaton,
    pred: TransitionPrediction | None,
    min_availability: float,
) -> ViolationRecord | None:
    """Return the exact first non-resource hard-gate violation used by TSBS."""

    if automaton.disabled:
        return None
    if not automaton.legal(e.from_phase, e.action, e.to_phase) or not e.tests.legal_lifecycle:
        return ViolationRecord(e.from_phase, e.transition_id, "lifecycle", -1.0, "service_automaton", 1.0, "illegal_lifecycle")
    for attr, resource, reason in [
        ("spatially_anchored", "anchor", "not_spatially_anchored"),
        ("topologically_valid", "topology", "not_topologically_valid"),
        ("physically_valid", "physical", "not_physically_valid"),
    ]:
        if not bool(getattr(e.tests, attr)):
            return ViolationRecord(e.to_phase, e.transition_id, resource, -1.0, "transition_tests", e.map_confidence, reason)
    if not e.tests.interface_valid:
        return ViolationRecord(
            e.to_phase,
            e.transition_id,
            "interface",
            -1.0,
            "transition_tests",
            e.map_confidence,
            ";".join(e.tests.reasons) or "interface_invalid",
        )
    availability = float(pred.dynamic_availability if pred is not None else e.availability)
    if availability < float(min_availability) or not e.tests.dynamically_available or bool((e.dynamic or {}).get("blocked", False)):
        return ViolationRecord(
            e.to_phase,
            e.transition_id,
            "availability",
            availability - float(min_availability),
            "prediction",
            e.map_confidence,
            "dynamic_unavailable",
        )
    return None


def build_capability_viability_kernel(
    transitions: Sequence[CandidateTransition],
    predictions: Mapping[str, TransitionPrediction],
    automaton: ServiceAutomaton,
    *,
    min_availability: float = 0.05,
    max_paths_per_state: int = 256,
    max_depth: int = 16,
) -> CapabilityViabilityKernel:
    """Build a proof-carrying backward kernel of executable suffixes.

    Structural reachability is computed *exactly* on the frozen hard-valid
    transition graph and is therefore safe to use for pruning.  Concrete suffix
    enumeration is a separate, bounded step used only for typed replay.  If the
    enumerator hits either the path-count or depth guard, that state is marked
    ``overflow`` and typed pruning is disabled there.  The guard therefore
    degrades to ordinary forward TSBS rather than introducing a false reject.

    Cyclic suffixes are not enumerated.  Under CapPlan's typed resource algebra,
    traversing an additional wait/replan cycle cannot improve a hard feasibility
    state (cumulative/probabilistic burdens are monotone non-decreasing,
    bottleneck affordances are monotone non-improving, and categorical/interface
    predicates are not repaired by repeating a transition).  Hence a feasible
    cyclic suffix always has a no-worse simple-state witness.
    """

    kernel = CapabilityViabilityKernel(
        max_paths_per_state=max(1, int(max_paths_per_state)),
        max_depth=max(1, int(max_depth)),
    )
    if automaton.disabled:
        return kernel

    states: Set[State] = set()
    valid_edges: List[CandidateTransition] = []
    direct_failure: Dict[State, ViolationRecord] = {}
    outgoing: Dict[State, List[CandidateTransition]] = {}
    reverse: Dict[State, List[CandidateTransition]] = {}
    for e in transitions:
        kernel.edge_by_id[str(e.transition_id)] = e
        u, v = _state_from(e), _state_to(e)
        states.add(u)
        states.add(v)
        violation = _hard_edge_violation(e, automaton, predictions.get(e.transition_id), min_availability)
        if violation is None:
            valid_edges.append(e)
            outgoing.setdefault(u, []).append(e)
            reverse.setdefault(v, []).append(e)
        else:
            direct_failure[u] = _better_violation(direct_failure.get(u), violation)  # type: ignore[assignment]

    for es in outgoing.values():
        es.sort(key=lambda e: (max(0.0, float(e.cost)), str(e.transition_id)))
    kernel.direct_failure_witness = dict(direct_failure)
    kernel.valid_outgoing_ids = {
        state: tuple(str(e.transition_id) for e in es)
        for state, es in outgoing.items()
    }

    kernel.n_states = len(states)
    kernel.n_valid_edges = len(valid_edges)
    kernel.n_invalid_edges = max(0, len(transitions) - len(valid_edges))
    if not states:
        return kernel

    destination_states = {s for s in states if automaton.accept(s[1])}

    # Exact structural reachability: backward graph traversal from every
    # accepting destination.  This part is independent of suffix enumeration
    # limits, so structural dead-end pruning remains sound even when the typed
    # path cache overflows.
    reachable: Set[State] = set(destination_states)
    stack = list(destination_states)
    while stack:
        v = stack.pop()
        for e in reverse.get(v, ()):  # valid hard edge u -> v
            u = _state_from(e)
            if u not in reachable:
                reachable.add(u)
                stack.append(u)
    kernel.reachable = {s: (s in reachable) for s in states}

    # Enumerate concrete simple-state suffixes independently for each reachable
    # state.  A bounded/incomplete enumeration is never used to prove typed
    # impossibility; overflow disables typed pruning for that state.
    for root in states:
        if root not in reachable:
            kernel.suffixes[root] = ()
            continue
        if root in destination_states:
            kernel.suffixes[root] = (SuffixWitness((), 0.0),)
            continue

        found: List[SuffixWitness] = []
        seen_paths: Set[Tuple[str, ...]] = set()
        incomplete = False
        # stack entries: state, transition_ids, cost, visited_states
        work: List[Tuple[State, Tuple[str, ...], float, frozenset[State]]] = [
            (root, (), 0.0, frozenset({root}))
        ]
        while work:
            state, tids, cost, visited = work.pop()
            if state in destination_states:
                if tids not in seen_paths:
                    seen_paths.add(tids)
                    found.append(SuffixWitness(tids, cost))
                    if len(found) > kernel.max_paths_per_state:
                        incomplete = True
                        break
                continue

            if len(tids) >= kernel.max_depth:
                # There may be a longer simple witness; do not use an incomplete
                # set for typed pruning.
                if any(_state_to(e) in reachable for e in outgoing.get(state, ())):
                    incomplete = True
                continue

            pushed = False
            for e in reversed(outgoing.get(state, ())):
                nxt = _state_to(e)
                if nxt not in reachable:
                    continue
                # Remove wait/replan cycles from the witness set. See docstring.
                if nxt in visited:
                    continue
                pushed = True
                work.append((
                    nxt,
                    tids + (str(e.transition_id),),
                    cost + max(0.0, float(e.cost)),
                    visited | frozenset({nxt}),
                ))
            # Exact reachability says a route exists, but all continuations may
            # have been cut by the simple-state/depth guard. Be conservative.
            if not pushed and state not in destination_states and state in reachable:
                # If there are valid outgoing edges but all revisit a state, the
                # cycle can be removed and need not mark incompleteness.  Only a
                # depth cut is handled above; a pure cycle cannot be the sole
                # route to a distinct destination in an exact reachable graph.
                pass

        if incomplete:
            kernel.overflow_states.add(root)
        found = found[: kernel.max_paths_per_state]
        found.sort(key=lambda w: (float(w.cost), len(w.transition_ids), w.transition_ids))
        kernel.suffixes[root] = tuple(found)
        if root in reachable and not found:
            # We could not materialize a complete witness set despite exact
            # reachability; typed pruning must fail open.
            kernel.overflow_states.add(root)

    # Propagate a concrete failure witness backwards through hard-valid edges
    # for states that are structurally unable to reach destination.  This turns
    # V4's generic reachability pseudo-certificate into a real phase/resource/
    # source boundary whenever one is available.
    witness: Dict[State, ViolationRecord] = dict(direct_failure)
    for _ in range(max(1, len(states))):
        changed = False
        for e in valid_edges:
            u, v = _state_from(e), _state_to(e)
            if u in reachable or v in reachable:
                continue
            cand = witness.get(v)
            if cand is None:
                continue
            best = _better_violation(witness.get(u), cand)
            if best is not None and best != witness.get(u):
                witness[u] = best
                changed = True
        if not changed:
            break
    kernel.structural_failure_witness = witness

    # V6 proof envelope: propagate the canonical invalid-edge witness through
    # *all* hard-valid reachable edges, not only through structural dead ends.
    # The independent oracle inspects invalid outgoing branches whenever it
    # visits a state.  A typed-pruned subtree must therefore preserve those
    # rejected-branch witnesses or the same pruning decision can silently change
    # T5 phase/resource/source diagnosis.  This fixed-point stores the minimum
    # certificate-key witness reachable from each state without changing search.
    proof: Dict[State, ViolationRecord] = dict(direct_failure)
    for _ in range(max(1, len(states))):
        changed = False
        for e in valid_edges:
            u, v = _state_from(e), _state_to(e)
            cand = proof.get(v)
            if cand is None:
                continue
            best = _better_violation(proof.get(u), cand)
            if best is not None and best != proof.get(u):
                proof[u] = best
                changed = True
        if not changed:
            break
    kernel.downstream_failure_witness = proof
    return kernel
