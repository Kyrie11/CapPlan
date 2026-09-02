"""Capability Continuation Envelope (CCE) for passenger-complete TSBS.

V4 computes an *optimistic*, contract-conditioned suffix envelope from every
service state to an accepting destination.  The envelope is built only from
hard-runtime evidence and the same lifecycle / availability gates used by
TSBS.  Numeric resource dimensions are relaxed independently: each resource is
allowed to take its own best suffix path.  This makes the vector optimistic and
therefore safe for impossibility pruning -- if even this relaxation cannot
satisfy a hard numeric clause, no concrete common suffix can satisfy it.

The relaxation deliberately excludes categorical requirement groups (e.g.
ramp OR lift OR low-floor).  Those remain authoritative in forward TSBS rather
than being approximated unsafely.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from capplan.data.schemas import CandidateTransition, CapabilityClause, ResourceEvidence
from capplan.models.predictors import TransitionPrediction
from capplan.semantics.capability_compiler import CompiledContract
from capplan.semantics.resource_registry import DEFAULT_REGISTRY, ResourceRegistry, ResourceType
from capplan.semantics.service_automaton import ServiceAutomaton
from capplan.semantics.typed_resource_algebra import (
    MissingEvidence,
    conservative_value,
    is_missing,
    neutral_value,
    satisfy,
    signed_margin,
    update_value,
)

State = Tuple[str, str]
EPS = 1e-9


@dataclass
class ContinuationEnvelope:
    """Optimistic suffix summaries keyed by ``(anchor, phase)``."""

    reachable: Dict[State, bool] = field(default_factory=dict)
    cost_to_go: Dict[State, float] = field(default_factory=dict)
    resource_to_go: Dict[str, Dict[State, Any]] = field(default_factory=dict)
    iterations: int = 0
    n_states: int = 0
    n_edges: int = 0
    resources: List[str] = field(default_factory=list)

    def is_reachable(self, state: State) -> bool:
        return bool(self.reachable.get(state, False))

    def suffix_value(self, resource_name: str, state: State) -> Any:
        return self.resource_to_go.get(resource_name, {}).get(state)


@dataclass(frozen=True)
class EnvelopeDecision:
    impossible: bool
    failed_resources: Tuple[str, ...] = ()
    optimistic_margins: Mapping[str, float] = field(default_factory=dict)
    min_margin: float = 1.0
    cost_to_go: float = 0.0
    structural_reachable: bool = True


def _state_from(e: CandidateTransition) -> State:
    return (str(e.from_anchor), str(e.from_phase))


def _state_to(e: CandidateTransition) -> State:
    return (str(e.to_anchor), str(e.to_phase))


def _hard_edge_allowed(
    e: CandidateTransition,
    automaton: ServiceAutomaton,
    pred: TransitionPrediction | None,
    min_availability: float,
) -> bool:
    if automaton.disabled:
        return False
    if not automaton.legal(e.from_phase, e.action, e.to_phase):
        return False
    if not (
        e.tests.legal_lifecycle
        and e.tests.spatially_anchored
        and e.tests.topologically_valid
        and e.tests.physically_valid
        and e.tests.interface_valid
        and e.tests.dynamically_available
    ):
        return False
    if bool((e.dynamic or {}).get("blocked", False)):
        return False
    availability = float(pred.dynamic_availability if pred is not None else e.availability)
    return availability + EPS >= float(min_availability)


def _edge_evidence(
    e: CandidateTransition,
    pred: TransitionPrediction | None,
) -> Sequence[ResourceEvidence]:
    return pred.typed_evidence if pred is not None else e.resource_evidence


def _resource_edge_value(
    e: CandidateTransition,
    pred: TransitionPrediction | None,
    resource_name: str,
    clauses: Sequence[CapabilityClause],
    compiled: CompiledContract,
    registry: ResourceRegistry,
    *,
    no_conservative_margins: bool,
) -> Any:
    """Aggregate this edge's conservative contribution for one numeric resource.

    If no hard clause for the resource is active on this edge, the neutral value
    is returned.  If the resource is active but fail-closed evidence is missing,
    ``None`` marks the edge unusable for that resource's optimistic suffix.
    """

    rt = registry.get(resource_name)
    active = [
        c for c in clauses
        if c.resource_name == resource_name
        and c.hard
        and ("all" in c.phase_scope or e.from_phase in c.phase_scope or e.to_phase in c.phase_scope)
    ]
    if not active:
        return neutral_value(rt)

    evs = [ev for ev in _edge_evidence(e, pred) if ev.resource_name == resource_name]
    if not evs:
        # If any active clause is fail-closed, no valid continuation through this
        # edge exists for this resource.  Optional/inconclusive clauses keep the
        # optimistic neutral value.
        if any(c.missing_policy == "fail_closed" for c in active):
            return None
        return neutral_value(rt)

    value: Any = neutral_value(rt)
    for ev in evs:
        if ev.missing or ev.value is None:
            if any(c.missing_policy == "fail_closed" for c in active):
                return None
            continue
        beta = 0.0 if no_conservative_margins else float(
            compiled.uncertainty.get(resource_name).beta_tau
            if compiled.uncertainty.get(resource_name) is not None
            else 1.0
        )
        try:
            xbar = conservative_value(ev.value, ev.sigma, rt, beta=beta)
            value = update_value(value, xbar, rt, evidence=ev)
        except Exception:
            return None
    return value


def _compose(edge_value: Any, suffix_value: Any, rt: ResourceType) -> Any:
    if edge_value is None or suffix_value is None:
        return None
    # Both operands already represent aggregate resource values over their
    # respective path segments; update_value is the canonical associative
    # resource-extension operator for numeric kinds.
    return update_value(edge_value, suffix_value, rt)


def _better(candidate: Any, current: Any, rt: ResourceType) -> bool:
    if candidate is None:
        return False
    if current is None:
        return True
    c = float(candidate)
    x = float(current)
    if rt.kind in ("cumulative", "upper", "probabilistic"):
        return c < x - EPS
    if rt.kind == "lower":
        return c > x + EPS
    return False


def _best_identity(rt: ResourceType) -> Any:
    # Destination suffix consumes no further resource.
    return neutral_value(rt)


def build_continuation_envelope(
    compiled: CompiledContract,
    transitions: Sequence[CandidateTransition],
    predictions: Mapping[str, TransitionPrediction],
    automaton: ServiceAutomaton,
    registry: ResourceRegistry = DEFAULT_REGISTRY,
    *,
    min_availability: float = 0.05,
    no_conservative_margins: bool = False,
) -> ContinuationEnvelope:
    """Build an optimistic typed suffix envelope for one passenger request."""

    env = ContinuationEnvelope()
    if automaton.disabled:
        return env

    edges: List[CandidateTransition] = []
    states: set[State] = set()
    for e in transitions:
        pred = predictions.get(e.transition_id)
        if not _hard_edge_allowed(e, automaton, pred, min_availability):
            continue
        edges.append(e)
        states.add(_state_from(e))
        states.add(_state_to(e))

    destination_states = {s for s in states if automaton.accept(s[1])}
    env.n_states = len(states)
    env.n_edges = len(edges)
    if not states or not destination_states:
        env.reachable = {s: False for s in states}
        env.cost_to_go = {s: math.inf for s in states}
        return env

    # Exact structural reachability + minimum remaining service cost under the
    # same non-resource hard gates.
    reachable = {s: s in destination_states for s in states}
    cost = {s: (0.0 if s in destination_states else math.inf) for s in states}
    max_iter = max(1, len(states))
    iterations = 0
    for it in range(max_iter):
        changed = False
        for e in edges:
            u, v = _state_from(e), _state_to(e)
            if reachable.get(v, False) and not reachable.get(u, False):
                reachable[u] = True
                changed = True
            if math.isfinite(cost.get(v, math.inf)):
                cand = max(0.0, float(e.cost)) + cost[v]
                if cand < cost.get(u, math.inf) - EPS:
                    cost[u] = cand
                    changed = True
        iterations = it + 1
        if not changed:
            break
    env.reachable = reachable
    env.cost_to_go = cost
    env.iterations = iterations

    # Requirement-group members are excluded from independent-clause pruning:
    # checking them separately would make any_of groups unsafely conjunctive.
    grouped_ids = {cid for g in compiled.groups for cid in g.clause_ids}
    numeric_clauses = [
        c for c in compiled.clauses
        if c.hard
        and c.id not in grouped_ids
        and registry.has(c.resource_name)
        and registry.get(c.resource_name).kind in {"cumulative", "upper", "lower", "probabilistic"}
    ]
    resources = sorted({c.resource_name for c in numeric_clauses})
    env.resources = resources

    for resource_name in resources:
        rt = registry.get(resource_name)
        rclauses = [c for c in numeric_clauses if c.resource_name == resource_name]
        values: Dict[State, Any] = {s: None for s in states}
        for s in destination_states:
            values[s] = _best_identity(rt)

        # Bellman-style fixed point over the monotone resource extension algebra.
        # Cycles cannot improve cumulative/upper/probabilistic burdens indefinitely;
        # lower resources converge to a widest-path bottleneck.
        for _ in range(max_iter):
            changed = False
            for e in edges:
                u, v = _state_from(e), _state_to(e)
                suffix = values.get(v)
                if suffix is None:
                    continue
                edge_value = _resource_edge_value(
                    e,
                    predictions.get(e.transition_id),
                    resource_name,
                    rclauses,
                    compiled,
                    registry,
                    no_conservative_margins=no_conservative_margins,
                )
                cand = _compose(edge_value, suffix, rt)
                if _better(cand, values.get(u), rt):
                    values[u] = cand
                    changed = True
            if not changed:
                break
        env.resource_to_go[resource_name] = values

    return env


def evaluate_continuation(
    state: State,
    ledger: Mapping[str, Any],
    compiled: CompiledContract,
    envelope: ContinuationEnvelope,
    registry: ResourceRegistry = DEFAULT_REGISTRY,
) -> EnvelopeDecision:
    """Evaluate whether a forward label has any relaxed feasible continuation."""

    if not envelope.is_reachable(state):
        return EnvelopeDecision(
            impossible=True,
            failed_resources=("continuation_reachability",),
            optimistic_margins={"continuation_reachability": -1.0},
            min_margin=-1.0,
            cost_to_go=float(envelope.cost_to_go.get(state, math.inf)),
            structural_reachable=False,
        )

    grouped_ids = {cid for g in compiled.groups for cid in g.clause_ids}
    margins: Dict[str, float] = {}
    failed: List[str] = []
    for c in compiled.clauses:
        if not c.hard or c.id in grouped_ids or not registry.has(c.resource_name):
            continue
        rt = registry.get(c.resource_name)
        if rt.kind not in {"cumulative", "upper", "lower", "probabilistic"}:
            continue
        suffix = envelope.suffix_value(c.resource_name, state)
        if suffix is None:
            # The relaxation could not prove a suffix bound for this resource.
            # Stay conservative with respect to pruning: do not reject.
            continue
        current = ledger.get(c.resource_name, MissingEvidence(c.resource_name))
        if is_missing(current):
            combined = suffix
        else:
            combined = _compose(current, suffix, rt)
        if combined is None:
            continue
        probe = dict(ledger)
        probe[c.resource_name] = combined
        try:
            margin = float(signed_margin(probe, c, registry))
            margins[c.resource_name] = min(margins.get(c.resource_name, math.inf), margin)
            if not satisfy(probe, c, registry):
                failed.append(c.resource_name)
        except Exception:
            continue

    min_margin = min(margins.values()) if margins else 1.0
    return EnvelopeDecision(
        impossible=bool(failed),
        failed_resources=tuple(sorted(set(failed))),
        optimistic_margins=margins,
        min_margin=float(min_margin),
        cost_to_go=float(envelope.cost_to_go.get(state, 0.0)),
        structural_reachable=True,
    )
