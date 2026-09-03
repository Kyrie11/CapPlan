"""V7 direct asymmetric capability-precondition compiler.

V6 proved that the V5 path-coupled typed viability mechanism can be represented
by compact antichains at query time, but its implementation still paid the full
cost of enumerating the V5 suffix/proof path universes *before* compression.  It
also reused the existential-viability dominance relation for diagnostic
summaries.  The V6-fast experiment showed that those two choices have different
failure modes: search decisions remain exact, while wall-clock latency and T5
witness fidelity regress.

V7 therefore compiles three frontiers directly over the hard-valid service graph:

* ``A_acc``: best-effect antichain for existential accepting continuation;
* ``A_rej``: certificate-preserving worst-effect antichain for typed rejection;
* ``A_proof``: easiest executable prefixes to concrete hard rejected branches.

The first and second frontiers intentionally use opposite resource orders.  A
suffix that is dominated by a no-worse suffix is irrelevant to ``exists a
feasible continuation`` but can be *more* informative for ``why all
continuations fail``.  Hence viability equivalence does not imply diagnostic
witness equivalence.

No complete raw suffix or raw proof-prefix universe is materialized.  Candidate
paths are propagated backward and dominance-pruned immediately.  Depth/frontier
caps are fail-open: any state whose accepting frontier may be incomplete is
marked incomplete, disabling typed pruning there.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Sequence, Set, Tuple

from capplan.data.schemas import ViolationRecord
from capplan.models.predictors import TransitionPrediction
from capplan.planning.capability_precondition_antichain import (
    CapabilityPreconditionAntichain,
    ProofPrefixSummary,
    SuffixEffectSummary,
    _build_suffix_summary,
    _effect_signature,
    _resource_effect_no_worse,
    _summary_dominates,
    _vio_key,
)
from capplan.planning.capability_viability_kernel import CapabilityViabilityKernel, State, SuffixWitness
from capplan.semantics.capability_compiler import CompiledContract
from capplan.semantics.resource_registry import DEFAULT_REGISTRY, ResourceRegistry


@dataclass(frozen=True)
class _FrontierUpdate:
    changed: bool
    overflow: bool = False


def _identity_summary() -> SuffixEffectSummary:
    return SuffixEffectSummary(
        transition_ids=(),
        cost=0.0,
        effects={},
        required_observed={},
        effect_witness={},
        first_active_witness={},
        active_clause_ids=(),
        active_group_ids=(),
    )


def _state_from_tid(kernel: CapabilityViabilityKernel, tid: str) -> State | None:
    e = kernel.edge_by_id.get(str(tid))
    return None if e is None else (str(e.from_anchor), str(e.from_phase))


def _state_to_tid(kernel: CapabilityViabilityKernel, tid: str) -> State | None:
    e = kernel.edge_by_id.get(str(tid))
    return None if e is None else (str(e.to_anchor), str(e.to_phase))


def _path_states(kernel: CapabilityViabilityKernel, transition_ids: Sequence[str]) -> Set[State]:
    states: Set[State] = set()
    for tid in transition_ids:
        u = _state_from_tid(kernel, tid)
        v = _state_to_tid(kernel, tid)
        if u is not None:
            states.add(u)
        if v is not None:
            states.add(v)
    return states


def _witness_signature(v: ViolationRecord) -> Tuple[Any, ...]:
    return (
        str(v.phase), str(v.transition_id), str(v.resource_type),
        str(v.evidence_source), round(float(v.confidence), 12), str(v.reason),
    )


def _summary_witness_signature(s: SuffixEffectSummary) -> Tuple[Any, ...]:
    def map_sig(rows: Mapping[str, ViolationRecord]) -> Tuple[Any, ...]:
        return tuple(sorted((str(k), _witness_signature(v)) for k, v in rows.items()))
    return (
        map_sig(s.required_observed),
        map_sig(s.effect_witness),
        map_sig(s.first_active_witness),
        tuple(sorted(s.active_clause_ids)),
        tuple(sorted(s.active_group_ids)),
    )


def _diagnostic_dominates(a: SuffixEffectSummary, b: SuffixEffectSummary, registry: ResourceRegistry) -> bool:
    """Whether diagnostic summary ``a`` safely subsumes ``b``.

    This is the reverse of acceptance dominance on resource effects: ``a`` must
    be no *better* than ``b`` on every typed resource so any failure represented
    by ``b`` is at least as strong under ``a``.  To keep the replacement
    certificate- and reachability-preserving, the complete witness/precondition
    signature must match exactly.
    """
    if _summary_witness_signature(a) != _summary_witness_signature(b):
        return False
    names = set(a.effects) | set(b.effects)
    for name in names:
        if not registry.has(name):
            continue
        # b <= a in the ordinary "no-worse" order means a is no better / at
        # least as burdensome as b, which is the safe direction for rejection.
        if not _resource_effect_no_worse(b.effects.get(name), a.effects.get(name), registry.get(name)):
            return False
    return True


def _insert_accept(
    frontier: List[SuffixEffectSummary],
    cand: SuffixEffectSummary,
    registry: ResourceRegistry,
    max_labels: int,
) -> _FrontierUpdate:
    sig = _effect_signature(cand)
    for old in frontier:
        if _effect_signature(old) == sig:
            # Viability is cost-independent; keep a deterministic cheapest trace
            # for diagnostics/debugging only.
            if (cand.cost, len(cand.transition_ids), cand.transition_ids) >= (old.cost, len(old.transition_ids), old.transition_ids):
                return _FrontierUpdate(False)
    if any(_summary_dominates(old, cand, registry) for old in frontier):
        return _FrontierUpdate(False)
    new = [x for x in frontier if not _summary_dominates(cand, x, registry)]
    new.append(cand)
    new.sort(key=lambda x: (x.cost, len(x.transition_ids), x.transition_ids))
    overflow = len(new) > max_labels
    if overflow:
        # Keep bounded memory but mark the state incomplete so typed pruning will
        # fail open.  The retained prefix is only diagnostic/debug information.
        new = new[:max_labels]
    frontier[:] = new
    return _FrontierUpdate(True, overflow)


def _insert_rejection(
    frontier: List[SuffixEffectSummary],
    cand: SuffixEffectSummary,
    registry: ResourceRegistry,
    max_labels: int,
) -> _FrontierUpdate:
    if any(_diagnostic_dominates(old, cand, registry) for old in frontier):
        return _FrontierUpdate(False)
    new = [x for x in frontier if not _diagnostic_dominates(cand, x, registry)]
    new.append(cand)
    new.sort(key=lambda x: (_summary_witness_signature(x), x.cost, len(x.transition_ids), x.transition_ids))
    overflow = len(new) > max_labels
    if overflow:
        new = new[:max_labels]
    frontier[:] = new
    return _FrontierUpdate(True, overflow)


def _insert_proof(
    frontier: List[ProofPrefixSummary],
    cand: ProofPrefixSummary,
    registry: ResourceRegistry,
    max_labels: int,
) -> _FrontierUpdate:
    wkey = _witness_signature(cand.witness)
    same = [x for x in frontier if _witness_signature(x.witness) == wkey]
    if any(_summary_dominates(x.prefix, cand.prefix, registry) for x in same):
        return _FrontierUpdate(False)
    new = [
        x for x in frontier
        if _witness_signature(x.witness) != wkey or not _summary_dominates(cand.prefix, x.prefix, registry)
    ]
    new.append(cand)
    new.sort(key=lambda x: (_vio_key(x.witness), x.prefix.cost, len(x.prefix.transition_ids), x.prefix.transition_ids))
    overflow = len(new) > max_labels
    if overflow:
        new = new[:max_labels]
    frontier[:] = new
    return _FrontierUpdate(True, overflow)


def _compile_path(
    tids: Tuple[str, ...],
    cost: float,
    kernel: CapabilityViabilityKernel,
    compiled: CompiledContract,
    predictions: Mapping[str, TransitionPrediction],
    registry: ResourceRegistry,
    *,
    no_conservative_margins: bool,
    default_beta: float,
) -> Tuple[SuffixEffectSummary | None, ViolationRecord | None]:
    return _build_suffix_summary(
        SuffixWitness(tids, float(cost)), kernel, compiled, predictions, registry,
        no_conservative_margins=no_conservative_margins,
        default_beta=default_beta,
    )


def _prefix_before_intrinsic(
    tids: Tuple[str, ...],
    intrinsic: ViolationRecord,
) -> Tuple[str, ...]:
    target = str(intrinsic.transition_id)
    try:
        idx = list(tids).index(target)
    except ValueError:
        return tids[:-1]
    return tids[:idx]


def build_direct_dual_precondition_kernel(
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
    """Directly compile V7 acceptance/rejection/proof frontiers.

    ``kernel`` must be built with ``enumerate_suffixes=False``.  The function is
    still correct if raw suffixes are present, but it never consumes them.
    """
    out = CapabilityPreconditionAntichain()
    max_labels = max(1, int(max_frontier_per_state or kernel.max_paths_per_state or 256))
    depth_cap = max(1, int(max_depth or kernel.max_depth or 16))

    states = set(kernel.reachable)
    incoming: Dict[State, List[str]] = {}
    for u, tids in kernel.valid_outgoing_ids.items():
        for tid in tids:
            e = kernel.edge_by_id.get(tid)
            if e is None:
                continue
            v = (str(e.to_anchor), str(e.to_phase))
            incoming.setdefault(v, []).append(str(tid))
    for tids in incoming.values():
        tids.sort()

    acc: Dict[State, List[SuffixEffectSummary]] = {s: [] for s in states}
    rej: Dict[State, List[SuffixEffectSummary]] = {s: [] for s in states}
    proof: Dict[State, List[ProofPrefixSummary]] = {s: [] for s in states}
    incomplete_acc: Set[State] = set()
    incomplete_proof: Set[State] = set()
    seen_paths: Dict[State, Set[Tuple[str, ...]]] = {s: set() for s in states}
    seen_proof_paths: Dict[State, Set[Tuple[Tuple[str, ...], Tuple[Any, ...]]]] = {s: set() for s in states}

    q: deque[State] = deque()
    queued: Set[State] = set()

    # Destination identity is the unique zero-length accepting transformer.
    ident = _identity_summary()
    for d in sorted(kernel.destination_states):
        if d not in acc:
            acc[d] = []
            rej[d] = []
            proof[d] = []
            seen_paths[d] = set()
            seen_proof_paths[d] = set()
            states.add(d)
        acc[d].append(ident)
        seen_paths[d].add(())
        q.append(d); queued.add(d)

    # A direct hard-invalid branch is reachable from its own state with an empty
    # prefix.  This seed is propagated backward under the exact prefix semantics.
    for s, witness in kernel.direct_failure_witness.items():
        if s not in proof:
            proof[s] = []; acc.setdefault(s, []); rej.setdefault(s, [])
            seen_paths.setdefault(s, set()); seen_proof_paths.setdefault(s, set())
            states.add(s)
        ps = ProofPrefixSummary(ident, witness)
        _insert_proof(proof[s], ps, registry, max_labels)
        seen_proof_paths[s].add(((), _witness_signature(witness)))
        if s not in queued:
            q.append(s); queued.add(s)

    # Backward worklist.  Propagation uses the union of the acceptance-best and
    # rejection-worst frontiers because either may carry a path that matters to an
    # ancestor under its respective order.
    while q:
        child = q.popleft(); queued.discard(child)
        child_paths: Dict[Tuple[str, ...], SuffixEffectSummary] = {}
        for row in acc.get(child, ()):
            child_paths.setdefault(tuple(row.transition_ids), row)
        for row in rej.get(child, ()):
            child_paths.setdefault(tuple(row.transition_ids), row)

        for tid in incoming.get(child, ()):
            edge = kernel.edge_by_id.get(tid)
            if edge is None:
                continue
            parent = (str(edge.from_anchor), str(edge.from_phase))
            parent_changed = False
            out.direct_build_edge_relaxations += 1

            for child_summary in list(child_paths.values()):
                # Match the V5 reference universe: simple-state paths only.
                if parent in _path_states(kernel, child_summary.transition_ids):
                    continue
                tids = (str(tid),) + tuple(child_summary.transition_ids)
                if len(tids) > depth_cap:
                    incomplete_acc.add(parent)
                    continue
                if tids in seen_paths.setdefault(parent, set()):
                    continue
                seen_paths[parent].add(tids)
                out.direct_build_candidates_total += 1
                cost = max(0.0, float(edge.cost)) + float(child_summary.cost)
                summary, intrinsic = _compile_path(
                    tids, cost, kernel, compiled, predictions, registry,
                    no_conservative_margins=no_conservative_margins,
                    default_beta=default_beta,
                )
                if intrinsic is not None:
                    # Intrinsic evidence/uncertainty rejection is a diagnostic
                    # boundary.  Attach it to the executable prefix preceding the
                    # failing transition when that prefix itself compiles cleanly.
                    prefix_tids = _prefix_before_intrinsic(tids, intrinsic)
                    prefix_cost = 0.0
                    for ptid in prefix_tids:
                        pe = kernel.edge_by_id.get(ptid)
                        if pe is not None:
                            prefix_cost += max(0.0, float(pe.cost))
                    prefix, pintrinsic = _compile_path(
                        prefix_tids, prefix_cost, kernel, compiled, predictions, registry,
                        no_conservative_margins=no_conservative_margins,
                        default_beta=default_beta,
                    )
                    if prefix is not None and pintrinsic is None:
                        upd = _insert_proof(proof.setdefault(parent, []), ProofPrefixSummary(prefix, intrinsic), registry, max_labels)
                        parent_changed = parent_changed or upd.changed
                        if upd.overflow:
                            incomplete_proof.add(parent)
                    continue
                if summary is None:
                    continue
                upd_a = _insert_accept(acc.setdefault(parent, []), summary, registry, max_labels)
                upd_r = _insert_rejection(rej.setdefault(parent, []), summary, registry, max_labels)
                parent_changed = parent_changed or upd_a.changed or upd_r.changed
                if upd_a.overflow:
                    incomplete_acc.add(parent)
                if upd_r.overflow:
                    # Rejection incompleteness does not affect feasibility soundness,
                    # only diagnostic completeness.
                    incomplete_proof.add(parent)

            # Propagate concrete direct/hard proofs through hard-valid typed-
            # executable prefixes without enumerating all root-to-proof paths.
            for child_proof in tuple(proof.get(child, ())):
                if parent in _path_states(kernel, child_proof.prefix.transition_ids):
                    continue
                ptids = (str(tid),) + tuple(child_proof.prefix.transition_ids)
                if len(ptids) > depth_cap:
                    incomplete_proof.add(parent)
                    continue
                pkey = (ptids, _witness_signature(child_proof.witness))
                if pkey in seen_proof_paths.setdefault(parent, set()):
                    continue
                seen_proof_paths[parent].add(pkey)
                pcost = max(0.0, float(edge.cost)) + float(child_proof.prefix.cost)
                prefix, intrinsic = _compile_path(
                    ptids, pcost, kernel, compiled, predictions, registry,
                    no_conservative_margins=no_conservative_margins,
                    default_beta=default_beta,
                )
                if prefix is None or intrinsic is not None:
                    continue
                upd_p = _insert_proof(
                    proof.setdefault(parent, []),
                    ProofPrefixSummary(prefix, child_proof.witness),
                    registry, max_labels,
                )
                parent_changed = parent_changed or upd_p.changed
                if upd_p.overflow:
                    incomplete_proof.add(parent)

            if parent_changed and parent not in queued:
                q.append(parent); queued.add(parent)

    # Any omitted accepting continuation in a descendant can invalidate an
    # ancestor's negative viability proof.  Propagate acceptance incompleteness
    # backward and fail open at every affected ancestor.
    iq = deque(incomplete_acc)
    while iq:
        child = iq.popleft()
        for tid in incoming.get(child, ()):
            e = kernel.edge_by_id.get(tid)
            if e is None:
                continue
            parent = (str(e.from_anchor), str(e.from_phase))
            if parent not in incomplete_acc:
                incomplete_acc.add(parent); iq.append(parent)

    for s in states:
        reachable = kernel.is_reachable(s)
        out.complete[s] = bool(reachable and s not in incomplete_acc)
        out.summaries[s] = tuple(acc.get(s, ())) if reachable else ()
        out.rejection_summaries[s] = tuple(rej.get(s, ())) if reachable else ()
        out.proof_summaries[s] = tuple(proof.get(s, ()))
        out.proof_complete[s] = s not in incomplete_proof
        out.raw_suffix_count[s] = 0
        out.proof_raw_count[s] = 0
        out.antichain_total += len(out.summaries[s])
        out.rejection_antichain_total += len(out.rejection_summaries[s])
        out.proof_antichain_total += len(out.proof_summaries[s])

    out.raw_total = 0
    out.proof_raw_total = 0
    out.direct_incomplete_states = len(incomplete_acc)
    return out
