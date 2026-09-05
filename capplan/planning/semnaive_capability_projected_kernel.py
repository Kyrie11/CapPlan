"""V10 semi-naive capability-projected acceptance precondition compiler.

V9 established the useful semantic quotient: the backward accepting frontier is
represented only on resources observable by the compiled hard passenger
capability program.  The V9 implementation, however, still uses a classical
worklist that re-propagates *the entire current child frontier* every time the
frontier changes.  On branching scenes this repeatedly composes summaries that
were already propagated and then asks the exact-signature/dominance layer to
reject the duplicates.

V10 keeps the V9 fixed point and changes only its construction algorithm:

* **semi-naive delta propagation**: only a newly admitted frontier summary is
  propagated to predecessor edges.  If it is later dominated, monotonicity of
  the registered typed edge transformers guarantees that its already-propagated
  parent image is itself dominated by the image of the better replacement, so
  no retraction is required;
* **capability-compiled packed dominance**: clause/group/observation subset
  preconditions are bit-packed once per summary and typed resource effects are
  normalized into the passenger-visible resource order.  The comparison is
  exactly the V9 dominance relation, but avoids rebuilding Python sets and
  repeatedly looking up resource types in the hot loop.

The semantic contract is intentionally unchanged: returned-plan soundness,
capability-projection invariance, lazy exact diagnosis, and fail-open bounded
frontiers are inherited from V9.  This module is a runtime/representation
closure candidate rather than a new paper-level problem formulation.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Dict, List, Mapping, Sequence, Set, Tuple

from capplan.models.predictors import TransitionPrediction
from capplan.planning.capability_precondition_antichain import (
    CapabilityPreconditionAntichain,
    SuffixEffectSummary,
    _build_suffix_summary,
    _effect_signature,
    _summary_dominates,
)
from capplan.planning.capability_projected_precondition_kernel import (
    hard_contract_resource_support,
    _identity_summary,
    _insert_accept_indexed,
    _insert_accept_linear_projected,
)
from capplan.planning.capability_viability_kernel import CapabilityViabilityKernel, State, SuffixWitness
from capplan.planning.incremental_capability_precondition_kernel import compose_suffix_summaries
from capplan.semantics.capability_compiler import CompiledContract
from capplan.semantics.resource_registry import DEFAULT_REGISTRY, ResourceRegistry
from capplan.semantics.typed_resource_algebra import MissingEvidence, PredicateState, neutral_value

EPS = 1e-9


@dataclass(frozen=True)
class _FrontierUpdate:
    changed: bool
    admitted: bool = False
    overflow: bool = False


@dataclass(frozen=True)
class _PackedSummary:
    signature: Tuple[Any, ...]
    required_mask: int
    clause_mask: int
    group_mask: int
    numeric: Tuple[float, ...]
    categorical: Tuple[Any, ...]
    fallback: bool = False


@dataclass(frozen=True)
class _DominancePlan:
    numeric_names: Tuple[str, ...]
    numeric_sign: Tuple[float, ...]
    categorical_names: Tuple[str, ...]
    resource_bit: Mapping[str, int]
    clause_bit: Mapping[str, int]
    group_bit: Mapping[str, int]


def _make_plan(compiled: CompiledContract, registry: ResourceRegistry, resource_filter: Set[str] | None) -> _DominancePlan:
    names = [n for n in registry.names() if resource_filter is None or n in resource_filter]
    numeric_names: List[str] = []
    numeric_sign: List[float] = []
    categorical_names: List[str] = []
    for name in names:
        rt = registry.get(name)
        if rt.kind == "categorical":
            categorical_names.append(name)
        else:
            numeric_names.append(name)
            # Normalize every numeric resource to "smaller is no worse".
            numeric_sign.append(-1.0 if rt.kind == "lower" else 1.0)
    return _DominancePlan(
        numeric_names=tuple(numeric_names),
        numeric_sign=tuple(numeric_sign),
        categorical_names=tuple(categorical_names),
        resource_bit={name: i for i, name in enumerate(names)},
        clause_bit={str(c.id): i for i, c in enumerate(compiled.clauses)},
        group_bit={str(g.group_id): i for i, g in enumerate(compiled.groups)},
    )


def _mask(values: Sequence[str] | Mapping[str, Any], table: Mapping[str, int]) -> int:
    out = 0
    it = values.keys() if isinstance(values, Mapping) else values
    for value in it:
        bit = table.get(str(value))
        if bit is not None:
            out |= 1 << int(bit)
    return out


def _norm_categorical(v: Any) -> Tuple[Any, ...]:
    if v is None:
        return ("absent",)
    if isinstance(v, MissingEvidence):
        # The historical V9 relation is deliberately conservative around
        # missing categorical effects.  Mark it for exact fallback.
        return ("missing", repr(v))
    if isinstance(v, PredicateState):
        return (
            "predicate", bool(v.ok), repr(v.observed), repr(v.required), str(v.operator)
        )
    return ("other", repr(v))


def _pack_summary(summary: SuffixEffectSummary, plan: _DominancePlan, registry: ResourceRegistry) -> _PackedSummary:
    numeric: List[float] = []
    fallback = False
    for name, sign in zip(plan.numeric_names, plan.numeric_sign):
        v = summary.effects.get(name)
        rt = registry.get(name)
        if v is None:
            v = neutral_value(rt)
        if isinstance(v, MissingEvidence):
            fallback = True
            numeric.append(0.0)
            continue
        try:
            numeric.append(float(sign) * float(v))
        except Exception:
            fallback = True
            numeric.append(0.0)
    categorical = tuple(_norm_categorical(summary.effects.get(name)) for name in plan.categorical_names)
    if any(row and row[0] == "missing" for row in categorical):
        fallback = True
    return _PackedSummary(
        signature=_effect_signature(summary),
        required_mask=_mask(summary.required_observed, plan.resource_bit),
        clause_mask=_mask(summary.active_clause_ids, plan.clause_bit),
        group_mask=_mask(summary.active_group_ids, plan.group_bit),
        numeric=tuple(numeric),
        categorical=categorical,
        fallback=fallback,
    )


def _categorical_no_worse(a: Tuple[Any, ...], b: Tuple[Any, ...]) -> bool:
    if a[0] == "absent" or b[0] == "absent":
        return a[0] == "absent" and b[0] == "absent"
    if a[0] == "predicate" and b[0] == "predicate":
        # Match V9: observed/required/operator must agree; True is no worse than
        # False, while False cannot dominate True.
        same = a[2:] == b[2:]
        return bool(same and (bool(a[1]) or not bool(b[1])))
    return a == b


def _packed_dominates(
    a: SuffixEffectSummary,
    pa: _PackedSummary,
    b: SuffixEffectSummary,
    pb: _PackedSummary,
    registry: ResourceRegistry,
    stats: Dict[str, int],
) -> bool:
    stats["frontier_dominance_checks"] = stats.get("frontier_dominance_checks", 0) + 1
    # V9's three subset preconditions become constant-time bit operations.
    if pa.required_mask & ~pb.required_mask:
        stats["frontier_mask_rejects"] = stats.get("frontier_mask_rejects", 0) + 1
        return False
    if pa.clause_mask & ~pb.clause_mask:
        stats["frontier_mask_rejects"] = stats.get("frontier_mask_rejects", 0) + 1
        return False
    if pa.group_mask & ~pb.group_mask:
        stats["frontier_mask_rejects"] = stats.get("frontier_mask_rejects", 0) + 1
        return False
    if pa.fallback or pb.fallback:
        stats["frontier_packed_fallbacks"] = stats.get("frontier_packed_fallbacks", 0) + 1
        return _summary_dominates(a, b, registry)
    for av, bv in zip(pa.numeric, pb.numeric):
        if av > bv + EPS:
            return False
    for av, bv in zip(pa.categorical, pb.categorical):
        if not _categorical_no_worse(av, bv):
            return False
    stats["frontier_packed_fastpath"] = stats.get("frontier_packed_fastpath", 0) + 1
    return True


def _insert_packed(
    frontier: List[SuffixEffectSummary],
    packed: List[_PackedSummary],
    signatures: Dict[Tuple[Any, ...], SuffixEffectSummary],
    cand: SuffixEffectSummary,
    pcand: _PackedSummary,
    registry: ResourceRegistry,
    max_labels: int,
    stats: Dict[str, int],
) -> _FrontierUpdate:
    old_same = signatures.get(pcand.signature)
    if old_same is not None:
        stats["frontier_signature_hits"] = stats.get("frontier_signature_hits", 0) + 1
        if (cand.cost, len(cand.transition_ids), cand.transition_ids) >= (
            old_same.cost, len(old_same.transition_ids), old_same.transition_ids
        ):
            return _FrontierUpdate(False, False, False)
        # Same semantic transformer but a cheaper deterministic witness.  A
        # signature maps to the summary object (rather than a positional index),
        # so ordinary admissions do not rebuild the whole index.
        try:
            same_i = next(i for i, row in enumerate(frontier) if row is old_same)
        except StopIteration:
            same_i = next((i for i, row in enumerate(frontier) if row == old_same), -1)
        if same_i >= 0:
            frontier.pop(same_i)
            packed.pop(same_i)
        signatures.pop(pcand.signature, None)

    for old, pold in zip(frontier, packed):
        if _packed_dominates(old, pold, cand, pcand, registry, stats):
            return _FrontierUpdate(False, False, False)

    new_frontier: List[SuffixEffectSummary] = []
    new_packed: List[_PackedSummary] = []
    removed_sigs: List[Tuple[Any, ...]] = []
    for old, pold in zip(frontier, packed):
        if not _packed_dominates(cand, pcand, old, pold, registry, stats):
            new_frontier.append(old)
            new_packed.append(pold)
        else:
            removed_sigs.append(pold.signature)
    for old_sig in removed_sigs:
        signatures.pop(old_sig, None)
    new_frontier.append(cand)
    new_packed.append(pcand)
    signatures[pcand.signature] = cand
    overflow = len(new_frontier) > max_labels
    if overflow:
        order = sorted(
            range(len(new_frontier)),
            key=lambda i: (new_frontier[i].cost, len(new_frontier[i].transition_ids), new_frontier[i].transition_ids),
        )[:max_labels]
        new_frontier = [new_frontier[i] for i in order]
        new_packed = [new_packed[i] for i in order]
    frontier[:] = new_frontier
    packed[:] = new_packed
    if overflow:
        signatures.clear()
        signatures.update({prow.signature: row for row, prow in zip(frontier, packed)})
    admitted = any(row is cand for row in frontier)
    stats["frontier_peak_size"] = max(stats.get("frontier_peak_size", 0), len(frontier))
    return _FrontierUpdate(True, admitted, overflow)


def build_semnaive_capability_projected_acceptance_kernel(
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
    use_delta_propagation: bool = True,
    use_packed_dominance: bool = True,
) -> CapabilityPreconditionAntichain:
    """Build the exact V9 projected fixed point with a semi-naive worklist."""
    started = perf_counter()
    out = CapabilityPreconditionAntichain()
    max_labels = max(1, int(max_frontier_per_state or kernel.max_paths_per_state or 256))
    depth_cap = max(1, int(max_depth or kernel.max_depth or 16))
    support = hard_contract_resource_support(compiled)
    resource_filter = set(support) if use_capability_projection else None
    stats: Dict[str, int] = {}
    out.projected_resource_count = len(support) if use_capability_projection else len(registry.names())
    plan = _make_plan(compiled, registry, resource_filter)

    states: Set[State] = set(kernel.reachable)
    incoming: Dict[State, List[str]] = {}
    for tids in kernel.valid_outgoing_ids.values():
        for tid in tids:
            edge = kernel.edge_by_id.get(tid)
            if edge is not None:
                incoming.setdefault((str(edge.to_anchor), str(edge.to_phase)), []).append(str(tid))
    for tids in incoming.values():
        tids.sort()

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
    packed_frontier: Dict[State, List[_PackedSummary]] = {s: [] for s in states}
    sig_index: Dict[State, Dict[Tuple[Any, ...], SuffixEffectSummary]] = {s: {} for s in states}
    exact_sig_index: Dict[State, Dict[Tuple[Any, ...], SuffixEffectSummary]] = {s: {} for s in states}
    incomplete: Set[State] = set()
    path_state_cache: Dict[Tuple[str, ...], Set[State]] = {(): set()}

    ident = _identity_summary()
    pident = _pack_summary(ident, plan, registry)

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

    def insert(parent: State, cand: SuffixEffectSummary) -> _FrontierUpdate:
        if use_packed_dominance:
            pcand = _pack_summary(cand, plan, registry)
            return _insert_packed(
                acc.setdefault(parent, []), packed_frontier.setdefault(parent, []),
                sig_index.setdefault(parent, {}), cand, pcand, registry, max_labels, stats,
            )
        # Exact V9 insertion as the causal control.
        acc.setdefault(parent, [])
        if use_signature_index:
            upd = _insert_accept_indexed(
                acc[parent], exact_sig_index.setdefault(parent, {}),
                cand, registry, max_labels, stats,
            )
        else:
            upd = _insert_accept_linear_projected(
                acc[parent], cand, registry, max_labels, stats,
            )
        admitted = any(row is cand for row in acc[parent])
        return _FrontierUpdate(bool(upd.changed), admitted, bool(upd.overflow))

    for dest in sorted(kernel.destination_states):
        states.add(dest)
        acc.setdefault(dest, []).append(ident)
        if use_packed_dominance:
            packed_frontier.setdefault(dest, []).append(pident)
            sig_index.setdefault(dest, {})[pident.signature] = ident
        exact_sig_index.setdefault(dest, {})[_effect_signature(ident)] = ident

    if use_delta_propagation:
        # Semi-naive worklist: each newly admitted summary is propagated once.
        q: deque[Tuple[State, SuffixEffectSummary]] = deque(
            (dest, ident) for dest in sorted(kernel.destination_states)
        )
        while q:
            child, child_summary = q.popleft()
            # A queued summary can be dominated before its turn arrives.  In
            # that case its monotone image cannot improve any parent frontier,
            # so skip the stale delta rather than doing redundant compositions.
            if not any(row is child_summary for row in acc.get(child, ())):
                stats["delta_stale_skips"] = stats.get("delta_stale_skips", 0) + 1
                continue
            stats["delta_propagations"] = stats.get("delta_propagations", 0) + 1
            for tid in incoming.get(child, ()):
                edge = kernel.edge_by_id.get(tid)
                local = edge_summary.get(tid)
                if edge is None or local is None:
                    continue
                parent = (str(edge.from_anchor), str(edge.from_phase))
                out.direct_build_edge_relaxations += 1
                if parent in path_states(tuple(child_summary.transition_ids)):
                    continue
                if 1 + len(child_summary.transition_ids) > depth_cap:
                    incomplete.add(parent)
                    continue
                out.direct_build_candidates_total += 1
                cand = compose_suffix_summaries(local, child_summary, registry)
                upd = insert(parent, cand)
                if upd.overflow:
                    incomplete.add(parent)
                if upd.changed and upd.admitted:
                    stats["delta_admissions"] = stats.get("delta_admissions", 0) + 1
                    q.append((parent, cand))
    else:
        # Full-frontier propagation control: semantically equivalent to V9 but
        # using the same packed dominance implementation as V10.
        q2: deque[State] = deque(sorted(kernel.destination_states))
        queued: Set[State] = set(kernel.destination_states)
        while q2:
            child = q2.popleft()
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
                    upd = insert(parent, cand)
                    parent_changed = parent_changed or upd.changed
                    if upd.overflow:
                        incomplete.add(parent)
                if parent_changed and parent not in queued:
                    q2.append(parent)
                    queued.add(parent)

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
    out.delta_propagations = int(stats.get("delta_propagations", 0))
    out.delta_admissions = int(stats.get("delta_admissions", 0))
    out.delta_stale_skips = int(stats.get("delta_stale_skips", 0))
    out.frontier_mask_rejects = int(stats.get("frontier_mask_rejects", 0))
    out.frontier_packed_fastpath = int(stats.get("frontier_packed_fastpath", 0))
    out.frontier_packed_fallbacks = int(stats.get("frontier_packed_fallbacks", 0))
    out.precondition_build_ms = (perf_counter() - started) * 1000.0
    return out
