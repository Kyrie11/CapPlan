"""V16 parametric capability-kernel reuse.

The passenger capability program is split into a *structural shape* and a set
of purely numerical monotone thresholds.  Under the frozen evidence-grounded
SN-CPK semantics, suffix-transformer construction and projected dominance do
not depend on those numerical thresholds; thresholds are consulted only by the
forward/query-time ``Sat`` predicate.  Contracts with the same structural shape
can therefore reuse the exact backward acceptance kernel.

This module deliberately treats categorical/interface requirements,
requirement-group topology, missing-evidence policy, conservative uncertainty
parameters, and confidence/risk boundaries as structural.  Reuse is scoped to
one episode and to an identical hard-valid/evidence fingerprint.  Any mismatch
is a cache miss, so the optimization fails open to the historical exact build.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import time
from dataclasses import dataclass, field
from typing import Any, Dict, Mapping, Sequence, Tuple

from capplan.data.schemas import CandidateTransition, CapabilityClause, ResourceEvidence
from capplan.models.predictors import TransitionPrediction
from capplan.planning.capability_precondition_antichain import CapabilityPreconditionAntichain
from capplan.planning.capability_viability_kernel import CapabilityViabilityKernel
from capplan.semantics.capability_compiler import CompiledContract


def _norm(x: Any) -> Any:
    if isinstance(x, bool) or x is None or isinstance(x, str):
        return x
    if isinstance(x, (int, float)):
        xf = float(x)
        if math.isnan(xf):
            return "NaN"
        if math.isinf(xf):
            return "Inf" if xf > 0 else "-Inf"
        return round(xf, 12)
    if isinstance(x, Mapping):
        return tuple(sorted((str(k), _norm(v)) for k, v in x.items()))
    if isinstance(x, (list, tuple, set)):
        return tuple(_norm(v) for v in x)
    return repr(x)


def _threshold_is_parametric(clause: CapabilityClause) -> bool:
    """Numerical thresholds that do not affect SN-CPK construction.

    Probabilistic/confidence thresholds are kept structural because the current
    compiler can turn them into intrinsic evidence-validity conditions.
    Categorical/interface requirements are always structural.
    """
    # Confidence thresholds are consumed by ``CompiledContract.uncertainty``
    # during intrinsic hard-evidence validation, so they are not late-bound
    # feasibility parameters even though the registered resource is ``lower``.
    if clause.resource_name in {"map_confidence", "dynamic_confidence"}:
        return False
    return (
        clause.kind in {"cumulative", "upper", "lower"}
        and isinstance(clause.threshold, (int, float))
        and not isinstance(clause.threshold, bool)
        and clause.risk_tolerance is None
    )


def capability_program_shape(
    compiled: CompiledContract,
    *,
    erase_numeric_thresholds: bool = True,
) -> Tuple[Tuple[Any, ...], int]:
    """Return a stable exact-kernel shape signature and erased-threshold count."""
    clauses = []
    erased = 0
    id_to_slot: Dict[str, int] = {}
    for i, c in enumerate(compiled.clauses):
        parametric = bool(erase_numeric_thresholds and _threshold_is_parametric(c))
        threshold = "<PARAMETRIC_NUMERIC_THRESHOLD>" if parametric else _norm(c.threshold)
        if parametric:
            erased += 1
        # ``risk_tolerance`` is intentionally retained.  It may act as a
        # probabilistic hard boundary even when ``threshold`` is numeric.
        sig = (
            str(c.resource_name), tuple(sorted(map(str, c.phase_scope))), str(c.operator),
            threshold, str(c.kind), round(float(c.confidence), 12), _norm(c.risk_tolerance),
            str(c.source), str(c.consent_scope), bool(c.hard), round(float(c.beta_tau), 12),
            str(c.missing_policy),
        )
        clauses.append(sig)
        id_to_slot[c.id] = i

    groups = []
    for g in compiled.groups:
        members = tuple(id_to_slot.get(cid, -1) for cid in g.clause_ids)
        groups.append((tuple(sorted(map(str, g.phase_scope))), str(g.logic), members, bool(g.hard)))

    # The compiler may generate uncertainty objects whose min-confidence/max-risk
    # are semantically stronger than an ordinary numeric feasibility threshold.
    uncertainty = tuple(sorted(
        (
            str(name), round(float(spec.min_confidence), 12), round(float(spec.beta_tau), 12),
            str(spec.missing_policy), _norm(spec.max_risk), tuple(sorted(map(str, spec.phases))),
        )
        for name, spec in compiled.uncertainty.items()
    ))
    return (tuple(clauses), tuple(groups), uncertainty, bool(compiled.soft_only)), erased


def _evidence_signature(ev: ResourceEvidence) -> Tuple[Any, ...]:
    return (
        str(ev.resource_name), _norm(ev.value), _norm(ev.sigma), bool(ev.missing),
        str(ev.reason), str(ev.source), round(float(ev.confidence), 12),
    )


def executable_graph_signature(
    kernel: CapabilityViabilityKernel,
    predictions: Mapping[str, TransitionPrediction],
) -> str:
    """Fingerprint the exact hard-valid graph and the evidence used by SN-CPK."""
    rows = []
    for tid in sorted(kernel.edge_by_id):
        e = kernel.edge_by_id[tid]
        pred = predictions.get(tid)
        evidence = tuple(_evidence_signature(x) for x in (pred.typed_evidence if pred is not None else e.resource_evidence))
        rows.append((
            str(tid), str(e.from_anchor), str(e.from_phase), str(e.to_anchor), str(e.to_phase),
            str(e.action), round(float(e.cost), 12), evidence,
        ))
    valid = tuple(sorted((str(a), str(p), tuple(sorted(map(str, tids)))) for (a, p), tids in kernel.valid_outgoing_ids.items()))
    payload = repr((tuple(rows), valid, tuple(sorted(kernel.overflow_states)))).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def stable_shape_id(shape: Tuple[Any, ...]) -> str:
    return hashlib.sha256(repr(shape).encode("utf-8")).hexdigest()[:16]


@dataclass
class ParametricKernelCacheEntry:
    antichain: CapabilityPreconditionAntichain
    source_build_ms: float
    thresholds_erased: int
    shape_id: str


@dataclass
class ParametricKernelCache:
    """Episode-scoped exact SN-CPK cache.

    Only one episode is retained because benchmark requests are naturally
    grouped as eight same-scene counterfactual contracts.  This keeps memory
    bounded and makes the intended amortization explicit.
    """
    episode_id: str | None = None
    entries: Dict[Tuple[Any, ...], ParametricKernelCacheEntry] = field(default_factory=dict)
    hits_total: int = 0
    misses_total: int = 0

    def reset_episode(self, episode_id: str) -> None:
        eid = str(episode_id)
        if self.episode_id != eid:
            self.episode_id = eid
            self.entries.clear()

    def lookup(self, key: Tuple[Any, ...]) -> ParametricKernelCacheEntry | None:
        out = self.entries.get(key)
        if out is None:
            self.misses_total += 1
        else:
            self.hits_total += 1
        return out

    def store(
        self, key: Tuple[Any, ...], antichain: CapabilityPreconditionAntichain, *,
        thresholds_erased: int, shape_id: str, source_build_ms: float | None = None,
    ) -> None:
        self.entries[key] = ParametricKernelCacheEntry(
            antichain=antichain,
            source_build_ms=float(antichain.precondition_build_ms if source_build_ms is None else source_build_ms),
            thresholds_erased=int(thresholds_erased),
            shape_id=str(shape_id),
        )


def reused_antichain(entry: ParametricKernelCacheEntry, *, lookup_ms: float, cache_entries: int) -> CapabilityPreconditionAntichain:
    """Return an immutable-summary sharing view with actual reuse instrumentation."""
    out = copy.copy(entry.antichain)
    # Construction counters represent work performed *for this request*.  The
    # exact summaries/frontiers are shared, while the current request pays only
    # lookup/specialization overhead.
    out.direct_build_candidates_total = 0
    out.direct_build_edge_relaxations = 0
    out.frontier_signature_hits = 0
    out.frontier_dominance_checks = 0
    out.delta_propagations = 0
    out.delta_admissions = 0
    out.delta_stale_skips = 0
    out.frontier_mask_rejects = 0
    out.frontier_packed_fastpath = 0
    out.frontier_packed_fallbacks = 0
    out.precondition_build_ms = float(lookup_ms)
    out.parametric_kernel_cache_hit = 1
    out.parametric_kernel_cache_miss = 0
    out.parametric_kernel_cache_entries = int(cache_entries)
    out.parametric_kernel_thresholds_erased = int(entry.thresholds_erased)
    out.parametric_kernel_lookup_ms = float(lookup_ms)
    out.parametric_kernel_source_build_ms = float(entry.source_build_ms)
    out.parametric_kernel_shape_id = str(entry.shape_id)
    return out


def annotate_cache_miss(antichain: CapabilityPreconditionAntichain, *, thresholds_erased: int, shape_id: str, cache_entries: int, lookup_ms: float) -> float:
    """Attach request-local cache telemetry and return the raw SN-CPK build time.

    ``precondition_build_ms`` is the user-visible V16 construction cost.  On a
    miss it must include shape/fingerprint/cache-lookup overhead as well as the
    historical SN-CPK build; otherwise the confirmatory build-time comparison
    would unfairly omit the optimization's own dispatch cost.
    """
    source_build_ms = float(antichain.precondition_build_ms)
    antichain.parametric_kernel_cache_hit = 0
    antichain.parametric_kernel_cache_miss = 1
    antichain.parametric_kernel_cache_entries = int(cache_entries)
    antichain.parametric_kernel_thresholds_erased = int(thresholds_erased)
    antichain.parametric_kernel_lookup_ms = float(lookup_ms)
    antichain.parametric_kernel_source_build_ms = source_build_ms
    antichain.precondition_build_ms = source_build_ms + float(lookup_ms)
    antichain.parametric_kernel_shape_id = str(shape_id)
    return source_build_ms
