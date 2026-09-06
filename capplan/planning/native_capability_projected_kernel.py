"""V11 native capability-projected fixed-point kernel.

V9 established that a concrete passenger capability program induces an
observational quotient over backward executable-precondition states.  V10 made
that exact fixed point semi-naive and accelerated the same dominance relation
with a packed comparator, but every candidate was still materialized as a
``SuffixEffectSummary`` before being packed.

V11 closes that representation inversion.  Edge transformers are compiled once
into passenger-projected coordinates and fixed-point composition happens
*natively* in those coordinates.  Full ``SuffixEffectSummary`` objects are
materialized only for the final nondominated frontier consumed by TSBS.

The construction is exact on the registered monotone typed algebra.  If an edge
requires a representation that cannot use the native fast path (for example an
unusual missing-valued effect), V11 fails open to the exact V10 builder rather
than approximating passenger feasibility.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Dict, List, Mapping, Sequence, Set, Tuple

from capplan.data.schemas import ViolationRecord
from capplan.models.predictors import TransitionPrediction
from capplan.planning.capability_precondition_antichain import (
    CapabilityPreconditionAntichain,
    SuffixEffectSummary,
    _build_suffix_summary,
)
from capplan.planning.capability_projected_precondition_kernel import (
    _identity_summary,
    hard_contract_resource_support,
)
from capplan.planning.capability_viability_kernel import CapabilityViabilityKernel, SuffixWitness
from capplan.planning.incremental_capability_precondition_kernel import _compose_effect
from capplan.planning.semnaive_capability_projected_kernel import (
    _categorical_no_worse,
    _make_plan,
    _norm_categorical,
    _pack_summary,
    build_semnaive_capability_projected_acceptance_kernel,
)
from capplan.semantics.capability_compiler import CompiledContract
from capplan.semantics.resource_registry import DEFAULT_REGISTRY, ResourceRegistry, ResourceType

State = Tuple[str, str]
EPS = 1e-9


@dataclass(frozen=True)
class _NativePlan:
    resource_names: Tuple[str, ...]
    resource_types: Tuple[ResourceType, ...]
    resource_bit: Mapping[str, int]
    numeric_names: Tuple[str, ...]
    numeric_types: Tuple[ResourceType, ...]
    numeric_sign: Tuple[float, ...]
    numeric_bits: Tuple[int, ...]
    numeric_index: Mapping[str, int]
    categorical_names: Tuple[str, ...]
    categorical_types: Tuple[ResourceType, ...]
    categorical_bits: Tuple[int, ...]
    clause_ids: Tuple[str, ...]
    group_ids: Tuple[str, ...]


@dataclass(frozen=True)
class _NativeSummary:
    transition_ids: Tuple[str, ...]
    cost: float
    observed_mask: int
    required_mask: int
    clause_mask: int
    group_mask: int
    numeric: Tuple[float, ...]  # normalized so smaller is no worse
    categorical: Tuple[Any, ...]  # actual PredicateState-like effects
    categorical_norm: Tuple[Tuple[Any, ...], ...]
    required_witness: Tuple[ViolationRecord | None, ...]
    effect_witness: Tuple[ViolationRecord | None, ...]
    first_active_witness: Tuple[ViolationRecord | None, ...]
    visited_mask: int
    signature: Tuple[Any, ...]


@dataclass(frozen=True)
class _InsertResult:
    changed: bool
    admitted: bool
    overflow: bool


def _native_plan(compiled: CompiledContract, registry: ResourceRegistry, resource_filter: Set[str] | None) -> _NativePlan:
    base = _make_plan(compiled, registry, resource_filter)
    resource_names = tuple(name for name, _ in sorted(base.resource_bit.items(), key=lambda kv: kv[1]))
    resource_types = tuple(registry.get(name) for name in resource_names)
    numeric_bits = tuple(base.resource_bit[name] for name in base.numeric_names)
    categorical_bits = tuple(base.resource_bit[name] for name in base.categorical_names)
    return _NativePlan(
        resource_names=resource_names,
        resource_types=resource_types,
        resource_bit=base.resource_bit,
        numeric_names=base.numeric_names,
        numeric_types=tuple(registry.get(name) for name in base.numeric_names),
        numeric_sign=base.numeric_sign,
        numeric_bits=numeric_bits,
        numeric_index={name: i for i, name in enumerate(base.numeric_names)},
        categorical_names=base.categorical_names,
        categorical_types=tuple(registry.get(name) for name in base.categorical_names),
        categorical_bits=categorical_bits,
        clause_ids=tuple(str(c.id) for c in compiled.clauses),
        group_ids=tuple(str(g.group_id) for g in compiled.groups),
    )


def _mask(keys: Sequence[str] | Mapping[str, Any], table: Mapping[str, int]) -> int:
    out = 0
    values = keys.keys() if isinstance(keys, Mapping) else keys
    for key in values:
        bit = table.get(str(key))
        if bit is not None:
            out |= 1 << int(bit)
    return out


def _signature(
    observed_mask: int,
    required_mask: int,
    clause_mask: int,
    group_mask: int,
    numeric: Tuple[float, ...],
    categorical_norm: Tuple[Tuple[Any, ...], ...],
    plan: _NativePlan,
) -> Tuple[Any, ...]:
    # Match V9/V10 effect-signature semantics on the fixed passenger support:
    # absent numeric resources are distinguished from an observed neutral value.
    num = tuple(
        None if not (observed_mask & (1 << bit)) else round(float(value), 12)
        for bit, value in zip(plan.numeric_bits, numeric)
    )
    cat = tuple(
        ("absent",) if not (observed_mask & (1 << bit)) else value
        for bit, value in zip(plan.categorical_bits, categorical_norm)
    )
    return (observed_mask, required_mask, clause_mask, group_mask, num, cat)


def _from_summary(
    summary: SuffixEffectSummary,
    plan: _NativePlan,
    compiled: CompiledContract,
    registry: ResourceRegistry,
    state_bit: Mapping[State, int],
    kernel: CapabilityViabilityKernel,
    base_plan,
) -> _NativeSummary | None:
    packed = _pack_summary(summary, base_plan, registry)
    if packed.fallback:
        return None
    observed_mask = _mask(summary.effects, plan.resource_bit)
    required_mask = _mask(summary.required_observed, plan.resource_bit)
    clause_bit = {cid: i for i, cid in enumerate(plan.clause_ids)}
    group_bit = {gid: i for i, gid in enumerate(plan.group_ids)}
    clause_mask = _mask(summary.active_clause_ids, clause_bit)
    group_mask = _mask(summary.active_group_ids, group_bit)
    categorical = tuple(summary.effects.get(name) for name in plan.categorical_names)
    categorical_norm = tuple(_norm_categorical(v) for v in categorical)
    required_witness = tuple(summary.required_observed.get(name) for name in plan.resource_names)
    effect_witness = tuple(summary.effect_witness.get(name) for name in plan.resource_names)
    first_active_witness = tuple(summary.first_active_witness.get(name) for name in plan.resource_names)
    visited = 0
    for tid in summary.transition_ids:
        edge = kernel.edge_by_id.get(tid)
        if edge is None:
            continue
        for state in ((str(edge.from_anchor), str(edge.from_phase)), (str(edge.to_anchor), str(edge.to_phase))):
            bit = state_bit.get(state)
            if bit is not None:
                visited |= 1 << bit
    sig = _signature(
        observed_mask, required_mask, clause_mask, group_mask,
        tuple(packed.numeric), categorical_norm, plan,
    )
    return _NativeSummary(
        transition_ids=tuple(summary.transition_ids),
        cost=float(summary.cost),
        observed_mask=observed_mask,
        required_mask=required_mask,
        clause_mask=clause_mask,
        group_mask=group_mask,
        numeric=tuple(packed.numeric),
        categorical=categorical,
        categorical_norm=categorical_norm,
        required_witness=required_witness,
        effect_witness=effect_witness,
        first_active_witness=first_active_witness,
        visited_mask=visited,
        signature=sig,
    )


def _compose_numeric(a: float, b: float, rt: ResourceType) -> float:
    if rt.kind == "cumulative":
        return float(a) + float(b)
    if rt.kind in ("upper", "lower"):
        # Lower resources are stored with a negative sign, making min(actual)
        # equivalent to max(normalized).
        return max(float(a), float(b))
    if rt.kind == "probabilistic":
        aa = min(1.0, max(0.0, float(a)))
        bb = min(1.0, max(0.0, float(b)))
        return 1.0 - (1.0 - aa) * (1.0 - bb)
    raise ValueError(rt.kind)


def _compose_native(first: _NativeSummary, second: _NativeSummary, plan: _NativePlan, stats: Dict[str, int]) -> _NativeSummary:
    stats["native_projected_compositions"] = stats.get("native_projected_compositions", 0) + 1
    numeric: List[float] = []
    for i, (bit, rt) in enumerate(zip(plan.numeric_bits, plan.numeric_types)):
        has_a = bool(first.observed_mask & (1 << bit))
        has_b = bool(second.observed_mask & (1 << bit))
        if not has_a:
            value = second.numeric[i]
        elif not has_b:
            value = first.numeric[i]
        else:
            value = _compose_numeric(first.numeric[i], second.numeric[i], rt)
        numeric.append(float(value))

    categorical: List[Any] = []
    categorical_norm: List[Tuple[Any, ...]] = []
    for i, (bit, rt) in enumerate(zip(plan.categorical_bits, plan.categorical_types)):
        has_a = bool(first.observed_mask & (1 << bit))
        has_b = bool(second.observed_mask & (1 << bit))
        if not has_a:
            value = second.categorical[i]
        elif not has_b:
            value = first.categorical[i]
        else:
            value = _compose_effect(first.categorical[i], second.categorical[i], rt)
        categorical.append(value)
        categorical_norm.append(_norm_categorical(value))

    observed_mask = first.observed_mask | second.observed_mask
    # A child prefix-observation requirement is discharged exactly when the
    # first segment has already observed that resource.
    required_mask = first.required_mask | (second.required_mask & ~first.observed_mask)
    clause_mask = first.clause_mask | second.clause_mask
    group_mask = first.group_mask | second.group_mask

    req: List[ViolationRecord | None] = []
    eff: List[ViolationRecord | None] = []
    first_active: List[ViolationRecord | None] = []
    for idx, (name, rt) in enumerate(zip(plan.resource_names, plan.resource_types)):
        bit = plan.resource_bit[name]
        r1 = first.required_witness[idx]
        r2 = second.required_witness[idx]
        if r1 is not None:
            req.append(r1)
        elif r2 is not None and not (first.observed_mask & (1 << bit)):
            req.append(r2)
        else:
            req.append(None)

        f1 = first.effect_witness[idx]
        f2 = second.effect_witness[idx]
        if f2 is None:
            eff.append(f1)
        elif not (first.observed_mask & (1 << bit)):
            eff.append(f2)
        elif rt.kind in ("cumulative", "probabilistic"):
            eff.append(f2)
        elif rt.kind == "categorical":
            eff.append(f1 if f1 is not None else f2)
        elif rt.kind in ("upper", "lower"):
            # The second edge owns the bottleneck witness iff the composed
            # normalized effect is exactly its normalized value.
            try:
                ni = plan.numeric_index[name]
                eff.append(f2 if abs(float(numeric[ni]) - float(second.numeric[ni])) <= EPS else f1)
            except Exception:
                eff.append(f1 if f1 is not None else f2)
        else:
            eff.append(f1 if f1 is not None else f2)

        a1 = first.first_active_witness[idx]
        a2 = second.first_active_witness[idx]
        first_active.append(a1 if a1 is not None else a2)

    sig = _signature(
        observed_mask, required_mask, clause_mask, group_mask,
        tuple(numeric), tuple(categorical_norm), plan,
    )
    return _NativeSummary(
        transition_ids=first.transition_ids + second.transition_ids,
        cost=float(first.cost) + float(second.cost),
        observed_mask=observed_mask,
        required_mask=required_mask,
        clause_mask=clause_mask,
        group_mask=group_mask,
        numeric=tuple(numeric),
        categorical=tuple(categorical),
        categorical_norm=tuple(categorical_norm),
        required_witness=tuple(req),
        effect_witness=tuple(eff),
        first_active_witness=tuple(first_active),
        visited_mask=first.visited_mask | second.visited_mask,
        signature=sig,
    )


def _native_dominates(a: _NativeSummary, b: _NativeSummary, stats: Dict[str, int]) -> bool:
    stats["frontier_dominance_checks"] = stats.get("frontier_dominance_checks", 0) + 1
    if a.required_mask & ~b.required_mask:
        stats["frontier_mask_rejects"] = stats.get("frontier_mask_rejects", 0) + 1
        return False
    if a.clause_mask & ~b.clause_mask:
        stats["frontier_mask_rejects"] = stats.get("frontier_mask_rejects", 0) + 1
        return False
    if a.group_mask & ~b.group_mask:
        stats["frontier_mask_rejects"] = stats.get("frontier_mask_rejects", 0) + 1
        return False
    for av, bv in zip(a.numeric, b.numeric):
        if av > bv + EPS:
            return False
    for av, bv in zip(a.categorical_norm, b.categorical_norm):
        if not _categorical_no_worse(av, bv):
            return False
    stats["frontier_packed_fastpath"] = stats.get("frontier_packed_fastpath", 0) + 1
    return True


def _rank(summary: _NativeSummary) -> Tuple[Any, ...]:
    return (float(summary.cost), len(summary.transition_ids), summary.transition_ids)


def _insert_native(
    frontier: List[_NativeSummary],
    signatures: Dict[Tuple[Any, ...], _NativeSummary],
    cand: _NativeSummary,
    max_labels: int,
    stats: Dict[str, int],
    *,
    fused: bool,
) -> _InsertResult:
    old_same = signatures.get(cand.signature)
    if old_same is not None:
        stats["frontier_signature_hits"] = stats.get("frontier_signature_hits", 0) + 1
        if _rank(cand) >= _rank(old_same):
            return _InsertResult(False, False, False)

    if not fused:
        # Causal control: native composition with V10's historical two-pass
        # frontier insertion shape.
        for old in frontier:
            if old is old_same:
                continue
            if _native_dominates(old, cand, stats):
                return _InsertResult(False, False, False)
        kept: List[_NativeSummary] = []
        removed_sigs: List[Tuple[Any, ...]] = []
        for old in frontier:
            if old is old_same:
                continue
            if not _native_dominates(cand, old, stats):
                kept.append(old)
            else:
                removed_sigs.append(old.signature)
        stats["fused_frontier_passes"] = stats.get("fused_frontier_passes", 0)
    else:
        # One traversal performs both dominance directions.  Frontier sizes are
        # deliberately small; this removes Python list/dict churn without
        # changing the partial order.
        stats["fused_frontier_passes"] = stats.get("fused_frontier_passes", 0) + 1
        kept = []
        removed_sigs = []
        for old in frontier:
            if old is old_same:
                continue
            if _native_dominates(old, cand, stats):
                return _InsertResult(False, False, False)
            if not _native_dominates(cand, old, stats):
                kept.append(old)
            else:
                removed_sigs.append(old.signature)

    if old_same is not None:
        signatures.pop(old_same.signature, None)
    for sig in removed_sigs:
        signatures.pop(sig, None)
    kept.append(cand)
    signatures[cand.signature] = cand
    overflow = len(kept) > max_labels
    if overflow:
        kept = sorted(kept, key=_rank)[:max_labels]
        signatures.clear()
        signatures.update({row.signature: row for row in kept})
    frontier[:] = kept
    admitted = any(row is cand for row in frontier)
    stats["frontier_peak_size"] = max(stats.get("frontier_peak_size", 0), len(frontier))
    return _InsertResult(True, admitted, overflow)


def _materialize(summary: _NativeSummary, plan: _NativePlan, stats: Dict[str, int]) -> SuffixEffectSummary:
    stats["native_projected_materializations"] = stats.get("native_projected_materializations", 0) + 1
    effects: Dict[str, Any] = {}
    for i, (name, bit, sign) in enumerate(zip(plan.numeric_names, plan.numeric_bits, plan.numeric_sign)):
        if summary.observed_mask & (1 << bit):
            effects[name] = float(summary.numeric[i]) * float(sign)
    for i, (name, bit) in enumerate(zip(plan.categorical_names, plan.categorical_bits)):
        if summary.observed_mask & (1 << bit):
            effects[name] = summary.categorical[i]

    required = {
        name: summary.required_witness[i]
        for i, name in enumerate(plan.resource_names)
        if (summary.required_mask & (1 << plan.resource_bit[name])) and summary.required_witness[i] is not None
    }
    effect_witness = {
        name: summary.effect_witness[i]
        for i, name in enumerate(plan.resource_names)
        if summary.effect_witness[i] is not None
    }
    first_active = {
        name: summary.first_active_witness[i]
        for i, name in enumerate(plan.resource_names)
        if summary.first_active_witness[i] is not None
    }
    active_clause_ids = tuple(cid for i, cid in enumerate(plan.clause_ids) if summary.clause_mask & (1 << i))
    active_group_ids = tuple(gid for i, gid in enumerate(plan.group_ids) if summary.group_mask & (1 << i))
    return SuffixEffectSummary(
        transition_ids=summary.transition_ids,
        cost=float(summary.cost),
        effects=effects,
        required_observed=required,
        effect_witness=effect_witness,
        first_active_witness=first_active,
        active_clause_ids=active_clause_ids,
        active_group_ids=active_group_ids,
    )


def build_native_capability_projected_acceptance_kernel(
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
    use_fused_frontier_insertion: bool = True,
) -> CapabilityPreconditionAntichain:
    """Construct the exact projected acceptance kernel natively in quotient coordinates."""
    started = perf_counter()
    out = CapabilityPreconditionAntichain()
    max_labels = max(1, int(max_frontier_per_state or kernel.max_paths_per_state or 256))
    depth_cap = max(1, int(max_depth or kernel.max_depth or 16))
    support = hard_contract_resource_support(compiled)
    resource_filter = set(support) if use_capability_projection else None
    plan = _native_plan(compiled, registry, resource_filter)
    out.projected_resource_count = len(support) if use_capability_projection else len(registry.names())
    stats: Dict[str, int] = {}

    states: Set[State] = set(kernel.reachable)
    for dest in kernel.destination_states:
        states.add(dest)
    state_bit = {state: i for i, state in enumerate(sorted(states))}

    incoming: Dict[State, List[str]] = {}
    valid_ids = {str(x) for rows in kernel.valid_outgoing_ids.values() for x in rows}
    for tids in kernel.valid_outgoing_ids.values():
        for tid in tids:
            edge = kernel.edge_by_id.get(tid)
            if edge is not None:
                incoming.setdefault((str(edge.to_anchor), str(edge.to_phase)), []).append(str(tid))
    for rows in incoming.values():
        rows.sort()

    edge_native: Dict[str, _NativeSummary] = {}
    base_plan = _make_plan(compiled, registry, resource_filter)
    fallback = False
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
        if summary is None or intrinsic is not None:
            continue
        # Explicitly verify V10's fast-path domain.  Any unusual/missing typed
        # effect falls back to the exact historical implementation.
        if _pack_summary(summary, base_plan, registry).fallback:
            fallback = True
            break
        native = _from_summary(summary, plan, compiled, registry, state_bit, kernel, base_plan)
        if native is None:
            fallback = True
            break
        edge_native[str(tid)] = native

    if fallback:
        exact = build_semnaive_capability_projected_acceptance_kernel(
            kernel, compiled, predictions, registry,
            no_conservative_margins=no_conservative_margins,
            default_beta=default_beta,
            max_frontier_per_state=max_frontier_per_state,
            max_depth=max_depth,
            use_capability_projection=use_capability_projection,
            use_signature_index=True,
            use_delta_propagation=True,
            use_packed_dominance=True,
        )
        exact.native_projected_fallbacks = 1
        return exact

    frontiers: Dict[State, List[_NativeSummary]] = {s: [] for s in states}
    signatures: Dict[State, Dict[Tuple[Any, ...], _NativeSummary]] = {s: {} for s in states}
    incomplete: Set[State] = set()

    ident = _identity_summary()
    nident = _from_summary(ident, plan, compiled, registry, state_bit, kernel, base_plan)
    assert nident is not None
    for dest in sorted(kernel.destination_states):
        frontiers.setdefault(dest, []).append(nident)
        signatures.setdefault(dest, {})[nident.signature] = nident

    q: deque[Tuple[State, _NativeSummary]] = deque((dest, nident) for dest in sorted(kernel.destination_states))
    while q:
        child, child_summary = q.popleft()
        if not any(row is child_summary for row in frontiers.get(child, ())):
            stats["delta_stale_skips"] = stats.get("delta_stale_skips", 0) + 1
            continue
        stats["delta_propagations"] = stats.get("delta_propagations", 0) + 1
        for tid in incoming.get(child, ()):
            edge = kernel.edge_by_id.get(tid)
            local = edge_native.get(tid)
            if edge is None or local is None:
                continue
            parent = (str(edge.from_anchor), str(edge.from_phase))
            out.direct_build_edge_relaxations += 1
            pbit = state_bit.get(parent)
            if pbit is not None and (child_summary.visited_mask & (1 << pbit)):
                continue
            if 1 + len(child_summary.transition_ids) > depth_cap:
                incomplete.add(parent)
                continue
            out.direct_build_candidates_total += 1
            cand = _compose_native(local, child_summary, plan, stats)
            upd = _insert_native(
                frontiers.setdefault(parent, []), signatures.setdefault(parent, {}),
                cand, max_labels, stats, fused=use_fused_frontier_insertion,
            )
            if upd.overflow:
                incomplete.add(parent)
            if upd.changed and upd.admitted:
                stats["delta_admissions"] = stats.get("delta_admissions", 0) + 1
                q.append((parent, cand))

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
        native_rows = list(frontiers.get(state, ())) if reachable else []
        native_rows.sort(key=_rank)
        rows = [_materialize(row, plan, stats) for row in native_rows]
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
    out.frontier_packed_fallbacks = 0
    out.native_projected_compositions = int(stats.get("native_projected_compositions", 0))
    out.native_projected_materializations = int(stats.get("native_projected_materializations", 0))
    out.native_projected_fallbacks = 0
    out.fused_frontier_passes = int(stats.get("fused_frontier_passes", 0))
    out.precondition_build_ms = (perf_counter() - started) * 1000.0
    return out
