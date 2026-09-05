"""V9 capability-projected incremental acceptance precondition compiler.

V8 showed that incremental typed backward viability is semantically strong but
its accepting frontier is still compiled in the full evidence-resource space.
That is unnecessarily expensive for passenger-complete planning: a compiled
passenger capability program observes only a small subset of the service
resource registry.  Two suffixes that differ exclusively on resources that can
never affect any hard clause/group are therefore acceptance-equivalent.

V9 compiles the same V8 accepting transformer recurrence in the quotient space
induced by the hard capability program.  Edge effects outside the hard contract
support are projected away *before* frontier dominance.  The query semantics
are unchanged because those dimensions are unobservable by ``Sat(Psi)``.

The module also indexes exact frontier signatures and delays deterministic
sorting until compilation completes.  These are representation optimizations,
not additional semantic claims.  Bounded-frontier/depth handling stays
fail-open exactly as in V8.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from time import perf_counter
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
from capplan.planning.incremental_capability_precondition_kernel import compose_suffix_summaries
from capplan.semantics.capability_compiler import CompiledContract
from capplan.semantics.resource_registry import DEFAULT_REGISTRY, ResourceRegistry


@dataclass(frozen=True)
class _FrontierUpdate:
    changed: bool
    overflow: bool = False


def hard_contract_resource_support(compiled: CompiledContract) -> frozenset[str]:
    """Resources observable by the hard passenger-complete acceptance program.

    A clause is relevant when it is itself hard or participates in a hard
    requirement group.  The latter matters for contracts whose member clauses
    are represented as alternatives under a group-level hard predicate.
    """
    hard_group_clause_ids = {
        str(cid)
        for group in compiled.groups
        if bool(group.hard)
        for cid in group.clause_ids
    }
    return frozenset(
        str(clause.resource_name)
        for clause in compiled.clauses
        if bool(clause.hard) or str(clause.id) in hard_group_clause_ids
    )


def _identity_summary() -> SuffixEffectSummary:
    return SuffixEffectSummary(
        transition_ids=(), cost=0.0, effects={}, required_observed={},
        effect_witness={}, first_active_witness={}, active_clause_ids=(),
        active_group_ids=(),
    )


def _insert_accept_indexed(
    frontier: List[SuffixEffectSummary],
    signatures: Dict[Tuple[Any, ...], SuffixEffectSummary],
    cand: SuffixEffectSummary,
    registry: ResourceRegistry,
    max_labels: int,
    stats: Dict[str, int],
) -> _FrontierUpdate:
    """Pareto insertion with exact-signature indexing and no per-insert sort."""
    sig = _effect_signature(cand)
    old_same = signatures.get(sig)
    if old_same is not None:
        stats["frontier_signature_hits"] = stats.get("frontier_signature_hits", 0) + 1
        if (cand.cost, len(cand.transition_ids), cand.transition_ids) >= (
            old_same.cost, len(old_same.transition_ids), old_same.transition_ids
        ):
            return _FrontierUpdate(False)
        # Same semantic transformer but cheaper/shorter deterministic witness.
        try:
            frontier.remove(old_same)
        except ValueError:
            pass
        signatures.pop(sig, None)

    for old in frontier:
        stats["frontier_dominance_checks"] = stats.get("frontier_dominance_checks", 0) + 1
        if _summary_dominates(old, cand, registry):
            return _FrontierUpdate(False)

    kept: List[SuffixEffectSummary] = []
    removed_sigs: List[Tuple[Any, ...]] = []
    for old in frontier:
        stats["frontier_dominance_checks"] = stats.get("frontier_dominance_checks", 0) + 1
        if _summary_dominates(cand, old, registry):
            removed_sigs.append(_effect_signature(old))
        else:
            kept.append(old)
    for old_sig in removed_sigs:
        signatures.pop(old_sig, None)
    kept.append(cand)
    signatures[sig] = cand

    overflow = len(kept) > max_labels
    if overflow:
        # Deterministic truncation is needed only on the fail-open overflow path.
        kept.sort(key=lambda x: (x.cost, len(x.transition_ids), x.transition_ids))
        kept = kept[:max_labels]
        signatures.clear()
        signatures.update({_effect_signature(row): row for row in kept})

    frontier[:] = kept
    stats["frontier_peak_size"] = max(stats.get("frontier_peak_size", 0), len(frontier))
    return _FrontierUpdate(True, overflow)


def _insert_accept_linear_projected(
    frontier: List[SuffixEffectSummary],
    cand: SuffixEffectSummary,
    registry: ResourceRegistry,
    max_labels: int,
    stats: Dict[str, int],
) -> _FrontierUpdate:
    """Projection-only control retaining V8-style linear signature scans."""
    sig = _effect_signature(cand)
    for old in frontier:
        if _effect_signature(old) == sig:
            stats["frontier_signature_hits"] = stats.get("frontier_signature_hits", 0) + 1
            if (cand.cost, len(cand.transition_ids), cand.transition_ids) >= (
                old.cost, len(old.transition_ids), old.transition_ids
            ):
                return _FrontierUpdate(False)
    for old in frontier:
        stats["frontier_dominance_checks"] = stats.get("frontier_dominance_checks", 0) + 1
        if _summary_dominates(old, cand, registry):
            return _FrontierUpdate(False)
    new: List[SuffixEffectSummary] = []
    for old in frontier:
        stats["frontier_dominance_checks"] = stats.get("frontier_dominance_checks", 0) + 1
        if not _summary_dominates(cand, old, registry):
            new.append(old)
    new.append(cand)
    overflow = len(new) > max_labels
    if overflow:
        new.sort(key=lambda x: (x.cost, len(x.transition_ids), x.transition_ids))
        new = new[:max_labels]
    frontier[:] = new
    stats["frontier_peak_size"] = max(stats.get("frontier_peak_size", 0), len(frontier))
    return _FrontierUpdate(True, overflow)


def build_capability_projected_acceptance_kernel(
    kernel: CapabilityViabilityKernel,
    compiled: CompiledContract,
    predictions: Mapping[str, TransitionPrediction],
    registry: ResourceRegistry = DEFAULT_REGISTRY,
    *,
    no_conservative_margins: bool = False,
    default_beta: float = 1.0,
    max_frontier_per_state: int | None = None,
    max_depth: int | None = None,
    use_capability_projection: bool = True,
    use_signature_index: bool = True,
) -> CapabilityPreconditionAntichain:
    """Build V9 in the hard-contract observable resource quotient space."""
    started = perf_counter()
    out = CapabilityPreconditionAntichain()
    max_labels = max(1, int(max_frontier_per_state or kernel.max_paths_per_state or 256))
    depth_cap = max(1, int(max_depth or kernel.max_depth or 16))
    support = hard_contract_resource_support(compiled)
    resource_filter = set(support) if use_capability_projection else None
    stats: Dict[str, int] = {}
    out.projected_resource_count = len(support) if use_capability_projection else len(registry.names())

    states: Set[State] = set(kernel.reachable)
    incoming: Dict[State, List[str]] = {}
    for tids in kernel.valid_outgoing_ids.values():
        for tid in tids:
            edge = kernel.edge_by_id.get(tid)
            if edge is None:
                continue
            incoming.setdefault((str(edge.to_anchor), str(edge.to_phase)), []).append(str(tid))
    for tids in incoming.values():
        tids.sort()

    # Compile each hard-valid edge once, but only in the acceptance-observable
    # resource support.  Missing/confidence preconditions are still built from
    # the active hard clauses, so fail-closed semantics are not weakened.
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
            resource_filter=resource_filter,
            instrumentation=stats,
        )
        if summary is not None and intrinsic is None:
            edge_summary[str(tid)] = summary

    acc: Dict[State, List[SuffixEffectSummary]] = {s: [] for s in states}
    sig_index: Dict[State, Dict[Tuple[Any, ...], SuffixEffectSummary]] = {s: {} for s in states}
    incomplete: Set[State] = set()
    ident = _identity_summary()
    q: deque[State] = deque()
    queued: Set[State] = set()
    path_state_cache: Dict[Tuple[str, ...], Set[State]] = {(): set()}

    for dest in sorted(kernel.destination_states):
        acc.setdefault(dest, []).append(ident)
        sig_index.setdefault(dest, {})[_effect_signature(ident)] = ident
        states.add(dest)
        q.append(dest)
        queued.add(dest)

    def path_states(transition_ids: Tuple[str, ...]) -> Set[State]:
        cached = path_state_cache.get(transition_ids)
        if cached is not None:
            return cached
        rows: Set[State] = set()
        for tid in transition_ids:
            edge = kernel.edge_by_id.get(tid)
            if edge is not None:
                rows.add((str(edge.from_anchor), str(edge.from_phase)))
                rows.add((str(edge.to_anchor), str(edge.to_phase)))
        path_state_cache[transition_ids] = rows
        return rows

    while q:
        child = q.popleft()
        queued.discard(child)
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
                if parent in path_states(tuple(child_summary.transition_ids)):
                    continue
                if 1 + len(child_summary.transition_ids) > depth_cap:
                    incomplete.add(parent)
                    continue
                out.direct_build_candidates_total += 1
                cand = compose_suffix_summaries(local, child_summary, registry)
                if use_signature_index:
                    upd = _insert_accept_indexed(
                        acc.setdefault(parent, []), sig_index.setdefault(parent, {}),
                        cand, registry, max_labels, stats,
                    )
                else:
                    upd = _insert_accept_linear_projected(
                        acc.setdefault(parent, []), cand, registry, max_labels, stats,
                    )
                parent_changed = parent_changed or upd.changed
                if upd.overflow:
                    incomplete.add(parent)
            if parent_changed and parent not in queued:
                q.append(parent)
                queued.add(parent)

    # Any possibly truncated descendant invalidates a hard negative proof at its
    # ancestors, preserving V8's fail-open boundedness.
    iq = deque(incomplete)
    while iq:
        child = iq.popleft()
        for tid in incoming.get(child, ()):
            edge = kernel.edge_by_id.get(tid)
            if edge is None:
                continue
            parent = (str(edge.from_anchor), str(edge.from_phase))
            if parent not in incomplete:
                incomplete.add(parent)
                iq.append(parent)

    # Deterministic output ordering is paid once, after the fixed point.
    for state in states:
        reachable = kernel.is_reachable(state)
        rows = list(acc.get(state, ())) if reachable else []
        rows.sort(key=lambda x: (x.cost, len(x.transition_ids), x.transition_ids))
        out.complete[state] = bool(reachable and state not in incomplete)
        out.summaries[state] = tuple(rows)
        out.raw_suffix_count[state] = 0
        out.proof_raw_count[state] = 0
        out.proof_complete[state] = True
        out.proof_summaries[state] = ()
        out.rejection_summaries[state] = ()
        out.antichain_total += len(rows)

    out.raw_total = 0
    out.proof_raw_total = 0
    out.proof_antichain_total = 0
    out.rejection_antichain_total = 0
    out.direct_incomplete_states = len(incomplete)
    out.projected_evidence_dropped = int(stats.get("projected_evidence_dropped", 0))
    out.frontier_signature_hits = int(stats.get("frontier_signature_hits", 0))
    out.frontier_dominance_checks = int(stats.get("frontier_dominance_checks", 0))
    out.frontier_peak_size = int(stats.get("frontier_peak_size", 0))
    out.precondition_build_ms = (perf_counter() - started) * 1000.0
    return out
