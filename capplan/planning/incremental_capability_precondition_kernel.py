"""V8 incremental acceptance precondition compiler.

V7 established that direct backward capability reasoning can prune more search
than the V5/V6 reference, but its implementation recompiles every candidate
transition *path* from scratch and simultaneously materializes a large reverse
rejection frontier.  The resulting work is dominated by repeated path scans and
frontier comparisons.

V8 separates the two questions:

* acceptance is an existential monotone query and is compiled incrementally as
  a nondominated frontier of typed suffix transformers;
* diagnosis is demand driven (handled by the planner's lazy exact diagnostic
  replay) and therefore is not precompiled into a reverse resource frontier.

Each hard-valid edge is compiled exactly once into a local typed transformer.
Backward propagation then composes ``T_e`` with a child summary using the same
associative typed resource algebra as forward TSBS.  No complete raw suffix or
proof universe is materialized and no candidate path is replayed from its first
edge merely to obtain a summary.

A bounded frontier is fail-open.  If a depth/frontier cap could have removed an
accepting continuation, the affected state and all of its ancestors are marked
incomplete; query-time typed pruning is disabled for those states.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Set, Tuple

from capplan.models.predictors import TransitionPrediction
from capplan.planning.capability_precondition_antichain import (
    CapabilityPreconditionAntichain,
    SuffixEffectSummary,
    _build_suffix_summary,
    _effect_signature,
    _summary_dominates,
)
from capplan.planning.capability_viability_kernel import CapabilityViabilityKernel, State, SuffixWitness
from capplan.semantics.capability_compiler import CompiledContract
from capplan.semantics.resource_registry import DEFAULT_REGISTRY, ResourceRegistry
from capplan.semantics.typed_resource_algebra import MissingEvidence, PredicateState, is_missing, neutral_value, update_value


@dataclass(frozen=True)
class _FrontierUpdate:
    changed: bool
    overflow: bool = False


def _identity_summary() -> SuffixEffectSummary:
    return SuffixEffectSummary(
        transition_ids=(), cost=0.0, effects={}, required_observed={},
        effect_witness={}, first_active_witness={}, active_clause_ids=(),
        active_group_ids=(),
    )


def _resource_observed(summary: SuffixEffectSummary, name: str) -> bool:
    """Whether this summary contains a usable observation of ``name``.

    ``_build_suffix_summary`` records only resources encountered on the suffix.
    A non-missing effect therefore means that a valid observation occurred
    before any later child precondition is evaluated.
    """
    if name not in summary.effects:
        return False
    return not is_missing(summary.effects[name])


def _compose_effect(a: Any, b: Any, resource_type) -> Any:
    """Compose two neutral-origin resource effects in forward order a -> b."""
    if a is None:
        return b
    if b is None:
        return a
    if isinstance(b, MissingEvidence):
        return b
    if resource_type.kind == "categorical" and isinstance(b, PredicateState):
        cur = neutral_value(resource_type) if is_missing(a) else a
        if isinstance(cur, PredicateState):
            return PredicateState(
                ok=bool(cur.ok) and bool(b.ok),
                observed=b.observed,
                required=b.required,
                operator=b.operator,
                evidence_source=b.evidence_source,
                confidence=min(float(cur.confidence), float(b.confidence)),
                failures=list(cur.failures or []) + list(b.failures or []),
            )
        return b
    return update_value(a, b, resource_type)


def compose_suffix_summaries(
    first: SuffixEffectSummary,
    second: SuffixEffectSummary,
    registry: ResourceRegistry = DEFAULT_REGISTRY,
) -> SuffixEffectSummary:
    """Associatively compose two already-compiled suffix transformers.

    The implementation intentionally keeps witness bookkeeping conservative;
    final failure certificates in V8 are produced by lazy exact replay.  The
    fields required for acceptance (effects, missing-observation preconditions,
    and phase-scoped clause/group activation) are preserved exactly under the
    typed algebra used by TSBS.
    """
    effects: Dict[str, Any] = {}
    for name in set(first.effects) | set(second.effects):
        if not registry.has(name):
            continue
        effects[name] = _compose_effect(first.effects.get(name), second.effects.get(name), registry.get(name))

    # A requirement that appears in the first segment remains required there.
    # A requirement first raised in the second segment is discharged when the
    # first segment already provides a valid observation of that resource.
    required = dict(first.required_observed)
    for name, vio in second.required_observed.items():
        if not _resource_observed(first, name):
            required.setdefault(name, vio)

    first_active = dict(second.first_active_witness)
    first_active.update(first.first_active_witness)  # first segment wins

    # Witnesses are not authoritative for V8 diagnosis, but keeping a stable
    # representative makes acceptance-side debugging and failure fallback useful.
    effect_witness = dict(first.effect_witness)
    for name, vio in second.effect_witness.items():
        if not registry.has(name):
            effect_witness.setdefault(name, vio)
            continue
        rt = registry.get(name)
        if name not in first.effects:
            effect_witness[name] = vio
        elif rt.kind in ("cumulative", "probabilistic"):
            effect_witness[name] = vio
        elif rt.kind == "categorical":
            effect_witness.setdefault(name, vio)
        elif rt.kind in ("upper", "lower"):
            try:
                composed = float(effects[name])
                second_val = float(second.effects.get(name))
                if abs(composed - second_val) <= 1e-9:
                    effect_witness[name] = vio
            except Exception:
                effect_witness.setdefault(name, vio)

    return SuffixEffectSummary(
        transition_ids=tuple(first.transition_ids) + tuple(second.transition_ids),
        cost=float(first.cost) + float(second.cost),
        effects=effects,
        required_observed=required,
        effect_witness=effect_witness,
        first_active_witness=first_active,
        active_clause_ids=tuple(sorted(set(first.active_clause_ids) | set(second.active_clause_ids))),
        active_group_ids=tuple(sorted(set(first.active_group_ids) | set(second.active_group_ids))),
    )


def _insert_accept(
    frontier: List[SuffixEffectSummary],
    cand: SuffixEffectSummary,
    registry: ResourceRegistry,
    max_labels: int,
) -> _FrontierUpdate:
    sig = _effect_signature(cand)
    for old in frontier:
        if _effect_signature(old) == sig:
            if (cand.cost, len(cand.transition_ids), cand.transition_ids) >= (old.cost, len(old.transition_ids), old.transition_ids):
                return _FrontierUpdate(False)
    if any(_summary_dominates(old, cand, registry) for old in frontier):
        return _FrontierUpdate(False)
    new = [x for x in frontier if not _summary_dominates(cand, x, registry)]
    new.append(cand)
    new.sort(key=lambda x: (x.cost, len(x.transition_ids), x.transition_ids))
    overflow = len(new) > max_labels
    if overflow:
        new = new[:max_labels]
    frontier[:] = new
    return _FrontierUpdate(True, overflow)


def _path_states(kernel: CapabilityViabilityKernel, transition_ids: Tuple[str, ...]) -> Set[State]:
    states: Set[State] = set()
    for tid in transition_ids:
        edge = kernel.edge_by_id.get(tid)
        if edge is not None:
            states.add((str(edge.from_anchor), str(edge.from_phase)))
            states.add((str(edge.to_anchor), str(edge.to_phase)))
    return states


def build_incremental_acceptance_kernel(
    kernel: CapabilityViabilityKernel,
    compiled: CompiledContract,
    predictions: Mapping[str, TransitionPrediction],
    registry: ResourceRegistry = DEFAULT_REGISTRY,
    *,
    no_conservative_margins: bool = False,
    default_beta: float = 1.0,
    max_frontier_per_state: int | None = None,
    max_depth: int | None = None,
) -> CapabilityPreconditionAntichain:
    """Build the V8 acceptance kernel by edge-local associative composition."""
    out = CapabilityPreconditionAntichain()
    max_labels = max(1, int(max_frontier_per_state or kernel.max_paths_per_state or 256))
    depth_cap = max(1, int(max_depth or kernel.max_depth or 16))

    states: Set[State] = set(kernel.reachable)
    incoming: Dict[State, List[str]] = {}
    for u, tids in kernel.valid_outgoing_ids.items():
        for tid in tids:
            edge = kernel.edge_by_id.get(tid)
            if edge is None:
                continue
            incoming.setdefault((str(edge.to_anchor), str(edge.to_phase)), []).append(str(tid))
    for tids in incoming.values():
        tids.sort()

    # Compile every hard-valid edge once.  Intrinsic contract/evidence failure
    # means the edge cannot participate in an accepting suffix for this request.
    edge_summary: Dict[str, SuffixEffectSummary] = {}
    valid_ids = {str(x) for rows in kernel.valid_outgoing_ids.values() for x in rows}
    for tid, edge in kernel.edge_by_id.items():
        if str(tid) not in valid_ids:
            continue
        summary, intrinsic = _build_suffix_summary(
            SuffixWitness((str(tid),), max(0.0, float(edge.cost))),
            kernel, compiled, predictions, registry,
            no_conservative_margins=no_conservative_margins,
            default_beta=default_beta,
        )
        if summary is not None and intrinsic is None:
            edge_summary[str(tid)] = summary

    acc: Dict[State, List[SuffixEffectSummary]] = {s: [] for s in states}
    incomplete: Set[State] = set()
    ident = _identity_summary()
    q: deque[State] = deque()
    queued: Set[State] = set()

    for dest in sorted(kernel.destination_states):
        acc.setdefault(dest, []).append(ident)
        states.add(dest)
        q.append(dest)
        queued.add(dest)

    while q:
        child = q.popleft(); queued.discard(child)
        child_rows = tuple(acc.get(child, ()))
        for tid in incoming.get(child, ()):
            edge = kernel.edge_by_id.get(tid)
            local = edge_summary.get(tid)
            if edge is None or local is None:
                continue
            parent = (str(edge.from_anchor), str(edge.from_phase))
            parent_changed = False
            out.direct_build_edge_relaxations += 1
            for child_summary in child_rows:
                if parent in _path_states(kernel, tuple(child_summary.transition_ids)):
                    continue
                if 1 + len(child_summary.transition_ids) > depth_cap:
                    incomplete.add(parent)
                    continue
                out.direct_build_candidates_total += 1
                cand = compose_suffix_summaries(local, child_summary, registry)
                upd = _insert_accept(acc.setdefault(parent, []), cand, registry, max_labels)
                parent_changed = parent_changed or upd.changed
                if upd.overflow:
                    incomplete.add(parent)
            if parent_changed and parent not in queued:
                q.append(parent); queued.add(parent)

    # Missing information may invalidate an ancestor's negative viability proof.
    # Mark all ancestors incomplete so typed pruning fails open there.
    iq = deque(incomplete)
    while iq:
        child = iq.popleft()
        for tid in incoming.get(child, ()):
            edge = kernel.edge_by_id.get(tid)
            if edge is None:
                continue
            parent = (str(edge.from_anchor), str(edge.from_phase))
            if parent not in incomplete:
                incomplete.add(parent); iq.append(parent)

    for state in states:
        reachable = kernel.is_reachable(state)
        out.complete[state] = bool(reachable and state not in incomplete)
        out.summaries[state] = tuple(acc.get(state, ())) if reachable else ()
        out.raw_suffix_count[state] = 0
        out.proof_raw_count[state] = 0
        out.proof_complete[state] = True
        out.proof_summaries[state] = ()
        out.rejection_summaries[state] = ()
        out.antichain_total += len(out.summaries[state])
    out.raw_total = 0
    out.proof_raw_total = 0
    out.proof_antichain_total = 0
    out.rejection_antichain_total = 0
    out.direct_incomplete_states = len(incomplete)
    return out
