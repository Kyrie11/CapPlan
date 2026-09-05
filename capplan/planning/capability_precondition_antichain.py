"""Executable capability weakest-precondition antichain (V6).

V5 established that exact, path-coupled typed suffix viability is useful, but
its query implementation replays hundreds of concrete suffix paths per forward
label.  V6 compiles each *complete* V5 suffix witness once into a monotone typed
suffix transformer and keeps only a nondominated antichain of transformers.

For a forward typed ledger R and a state s, each antichain element phi induces a
weakest-precondition-style test: applying the suffix effect phi to R must satisfy
the same compiled passenger contract.  A state is typed-viable iff at least one
antichain element passes.  Because dominated suffix effects are removed only
when they are no better on every typed resource and require no weaker prior
observation, existential viability is preserved.

The antichain is *proof carrying*.  It stores compact resource witnesses for
query-specific typed failures, while the underlying V5 kernel stores an exact
prefix-independent rejected-branch proof envelope.  Search therefore uses the
same object for pruning and for concrete phase/resource/source diagnosis.

Soundness boundary: typed pruning is enabled only for V5 kernel states whose
concrete suffix enumeration was complete.  Overflow remains fail-open.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from capplan.data.schemas import CapabilityClause, RequirementGroup, ResourceEvidence, ViolationRecord
from capplan.models.predictors import TransitionPrediction
from capplan.planning.capability_viability_kernel import CapabilityViabilityKernel, SuffixWitness
from capplan.semantics.capability_compiler import CompiledContract
from capplan.semantics.resource_registry import DEFAULT_REGISTRY, ResourceRegistry, ResourceType
from capplan.semantics.typed_resource_algebra import (
    MissingEvidence,
    PredicateState,
    active_clauses,
    active_groups,
    conservative_value,
    is_missing,
    neutral_value,
    satisfy_all,
    signed_margin,
    update_value,
)

State = Tuple[str, str]
EPS = 1e-9


@dataclass(frozen=True)
class SuffixEffectSummary:
    transition_ids: Tuple[str, ...]
    cost: float
    effects: Mapping[str, Any]
    required_observed: Mapping[str, ViolationRecord]
    effect_witness: Mapping[str, ViolationRecord]
    first_active_witness: Mapping[str, ViolationRecord]
    # Union of phase-scoped requirements actually encountered along this
    # transition sequence.  Complete destination suffixes are checked against
    # the whole contract; proof-prefix summaries use these sets to establish
    # that a rejected downstream branch is *typed reachable* from the current
    # ledger before its witness may participate in T5 certificate selection.
    active_clause_ids: Tuple[str, ...] = ()
    active_group_ids: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ProofPrefixSummary:
    prefix: SuffixEffectSummary
    witness: ViolationRecord


@dataclass
class CapabilityPreconditionAntichain:
    summaries: Dict[State, Tuple[SuffixEffectSummary, ...]] = field(default_factory=dict)
    raw_suffix_count: Dict[State, int] = field(default_factory=dict)
    complete: Dict[State, bool] = field(default_factory=dict)
    raw_total: int = 0
    antichain_total: int = 0
    # Conditional rejected-branch proofs.  A proof is eligible only when its
    # hard-valid prefix also satisfies the current passenger's typed ledger.
    proof_summaries: Dict[State, Tuple[ProofPrefixSummary, ...]] = field(default_factory=dict)
    proof_raw_count: Dict[State, int] = field(default_factory=dict)
    proof_complete: Dict[State, bool] = field(default_factory=dict)
    proof_raw_total: int = 0
    proof_antichain_total: int = 0
    # V7: acceptance and rejection require different dominance orders.  The
    # acceptance antichain above keeps resource-wise *best* suffixes for an
    # existential feasibility query; the rejection antichain keeps certificate-
    # preserving *worst* suffixes so diagnostic failures are not erased by the
    # same compression relation.
    rejection_summaries: Dict[State, Tuple[SuffixEffectSummary, ...]] = field(default_factory=dict)
    rejection_antichain_total: int = 0
    # Direct-compiler instrumentation.  V6 leaves these at zero because it first
    # enumerates the V5 raw suffix/proof universes and compresses afterwards.
    direct_build_candidates_total: int = 0
    direct_build_edge_relaxations: int = 0
    direct_incomplete_states: int = 0
    # V9 capability-projected compiler instrumentation.  These fields are kept
    # on the shared container so evaluation can compare V8/V9 representations
    # without changing the search API.
    projected_resource_count: int = 0
    projected_evidence_dropped: int = 0
    frontier_signature_hits: int = 0
    frontier_dominance_checks: int = 0
    frontier_peak_size: int = 0
    precondition_build_ms: float = 0.0

    def state_summaries(self, state: State) -> Tuple[SuffixEffectSummary, ...]:
        return self.summaries.get(state, ())

    def state_complete(self, state: State) -> bool:
        return bool(self.complete.get(state, False))

    def state_proofs(self, state: State) -> Tuple[ProofPrefixSummary, ...]:
        return self.proof_summaries.get(state, ())

    def state_rejections(self, state: State) -> Tuple[SuffixEffectSummary, ...]:
        return self.rejection_summaries.get(state, ())


@dataclass(frozen=True)
class AntichainDecision:
    viable: bool
    witness: ViolationRecord | None
    checked_summaries: int


@dataclass(frozen=True)
class ProofDecision:
    witness: ViolationRecord | None
    checked_proofs: int


@dataclass(frozen=True)
class RejectionDecision:
    witness: ViolationRecord | None
    checked_summaries: int


def _vio_key(v: ViolationRecord) -> Tuple[float, float, int, str]:
    from capplan.semantics.service_automaton import PHASE_INDEX
    return (float(v.signed_margin), -float(v.confidence), PHASE_INDEX.get(str(v.phase), 999), str(v.transition_id))


def better_violation(a: ViolationRecord | None, b: ViolationRecord | None) -> ViolationRecord | None:
    if a is None:
        return b
    if b is None:
        return a
    return a if _vio_key(a) <= _vio_key(b) else b


def _beta_for(compiled: CompiledContract, resource_name: str, no_conservative_margins: bool, default_beta: float) -> float:
    if no_conservative_margins:
        return 0.0
    spec = compiled.uncertainty.get(resource_name)
    return float(spec.beta_tau if spec is not None else default_beta)


def _resource_effect_no_worse(a: Any, b: Any, rt: ResourceType) -> bool:
    """Whether suffix effect ``a`` is never worse than ``b`` for this contract.

    ``None`` means the suffix does not update this resource.  For numeric
    monotone resources, no update is the neutral/best suffix effect.  Categorical
    transforms are kept conservative: only identical observed semantics are
    dominance-comparable.
    """
    if a is None and b is None:
        return True
    if rt.kind in ("cumulative", "upper", "probabilistic"):
        av = float(neutral_value(rt) if a is None else a)
        bv = float(neutral_value(rt) if b is None else b)
        return av <= bv + EPS
    if rt.kind == "lower":
        av = float(neutral_value(rt) if a is None else a)
        bv = float(neutral_value(rt) if b is None else b)
        return av + EPS >= bv
    if rt.kind == "categorical":
        if a is None or b is None:
            return a is None and b is None
        if is_missing(a) or is_missing(b):
            return is_missing(a) and is_missing(b)
        if not isinstance(a, PredicateState) or not isinstance(b, PredicateState):
            return repr(a) == repr(b)
        # The forward ledger retains last-observation audit fields.  Requiring
        # those fields to agree makes dominance conservative even when one
        # resource appears in multiple phase-scoped categorical clauses.
        same_semantics = (
            repr(a.observed) == repr(b.observed)
            and repr(a.required) == repr(b.required)
            and str(a.operator) == str(b.operator)
        )
        return bool(same_semantics and (bool(a.ok) or not bool(b.ok)))
    return False


def _summary_dominates(a: SuffixEffectSummary, b: SuffixEffectSummary, registry: ResourceRegistry) -> bool:
    # A cannot be stronger in its prefix-observation or phase-scoped
    # requirement precondition.  This matters for proof prefixes that may reach
    # the same rejected branch through different lifecycle-preserving routes.
    if not set(a.required_observed).issubset(set(b.required_observed)):
        return False
    if not set(a.active_clause_ids).issubset(set(b.active_clause_ids)):
        return False
    if not set(a.active_group_ids).issubset(set(b.active_group_ids)):
        return False
    names = set(a.effects) | set(b.effects)
    for name in names:
        if not registry.has(name):
            continue
        if not _resource_effect_no_worse(a.effects.get(name), b.effects.get(name), registry.get(name)):
            return False
    return True


def _effect_signature(summary: SuffixEffectSummary) -> Tuple[Any, ...]:
    def norm(v: Any):
        if isinstance(v, MissingEvidence):
            return ("missing", v.reason, v.evidence_source, float(v.confidence))
        if isinstance(v, PredicateState):
            return ("predicate", bool(v.ok), repr(v.observed), repr(v.required), str(v.operator))
        if isinstance(v, (int, float)):
            return ("numeric", round(float(v), 12))
        return ("other", repr(v))
    return (
        tuple(sorted((k, norm(v)) for k, v in summary.effects.items())),
        tuple(sorted(summary.required_observed)),
        tuple(sorted(summary.active_clause_ids)),
        tuple(sorted(summary.active_group_ids)),
    )


def _build_suffix_summary(
    suffix: SuffixWitness,
    kernel: CapabilityViabilityKernel,
    compiled: CompiledContract,
    predictions: Mapping[str, TransitionPrediction],
    registry: ResourceRegistry,
    *,
    no_conservative_margins: bool,
    default_beta: float,
    resource_filter: set[str] | None = None,
    instrumentation: Dict[str, int] | None = None,
) -> Tuple[SuffixEffectSummary | None, ViolationRecord | None]:
    effects: Dict[str, Any] = {}
    required_observed: Dict[str, ViolationRecord] = {}
    effect_witness: Dict[str, ViolationRecord] = {}
    first_active_witness: Dict[str, ViolationRecord] = {}
    seen_valid_observation: set[str] = set()
    best_intrinsic: ViolationRecord | None = None
    active_clause_ids: set[str] = set()
    active_group_ids: set[str] = set()

    for tid in suffix.transition_ids:
        edge = kernel.edge_by_id.get(tid)
        if edge is None:
            continue
        active = active_clauses(compiled.clauses, [edge.from_phase, edge.to_phase])
        active_groups_for_edge = active_groups(compiled.groups, [edge.from_phase, edge.to_phase])
        grouped_ids = {cid for g in active_groups_for_edge for cid in g.clause_ids}
        active_clause_ids.update(c.id for c in active)
        active_group_ids.update(g.group_id for g in active_groups_for_edge)
        by_resource: Dict[str, List[CapabilityClause]] = {}
        for c in active:
            by_resource.setdefault(c.resource_name, []).append(c)
            first_active_witness.setdefault(
                c.resource_name,
                ViolationRecord(
                    edge.to_phase, edge.transition_id, c.resource_name, -1.0,
                    c.source, c.confidence, "resource_or_interface",
                ),
            )

        pred = predictions.get(edge.transition_id)
        evidence_list: Sequence[ResourceEvidence] = pred.typed_evidence if pred is not None else edge.resource_evidence
        observed_resources = {ev.resource_name for ev in evidence_list if registry.has(ev.resource_name)}

        # Resource-transform composition.  This is the same associative monotone
        # algebra used by forward TSBS, but it is performed once per suffix.
        for ev in evidence_list:
            if not registry.has(ev.resource_name):
                continue
            name = ev.resource_name
            if resource_filter is not None and name not in resource_filter:
                if instrumentation is not None:
                    instrumentation["projected_evidence_dropped"] = instrumentation.get("projected_evidence_dropped", 0) + 1
                continue
            rt = registry.get(name)
            clauses_for_resource = by_resource.get(name, [])
            cur = effects.get(name, neutral_value(rt))
            if ev.missing or ev.value is None:
                xbar: Any = MissingEvidence(name, edge.to_phase, ev.reason or "not_observed", ev.source, ev.confidence)
            elif rt.kind == "categorical":
                xbar = ev.value
            else:
                beta = _beta_for(compiled, name, no_conservative_margins, default_beta)
                xbar = conservative_value(ev.value, ev.sigma, rt, beta=beta)

            if rt.kind == "categorical" and clauses_for_resource:
                new = cur
                for c in clauses_for_resource:
                    new = update_value(new, xbar, rt, evidence=ev, clause=c)
                effects[name] = new
            else:
                effects[name] = update_value(cur, xbar, rt, evidence=ev)

            # Store a representative boundary for query-time typed proof.
            replace = name not in effect_witness
            if name in effect_witness and not (ev.missing or ev.value is None) and rt.kind in ("upper", "lower"):
                old = effects.get(name)
                # ``effects`` already contains the composed value.  For upper/lower
                # choose the edge that attains the suffix bottleneck.
                try:
                    val = float(xbar)
                    if rt.kind == "upper":
                        replace = abs(float(old) - val) <= 1e-9
                    else:
                        replace = abs(float(old) - val) <= 1e-9
                except Exception:
                    pass
            elif name in effect_witness and rt.kind in ("cumulative", "probabilistic"):
                # Threshold crossing, if any, happens no earlier than the last
                # contributing edge under the monotone algebra.
                replace = True
            if replace:
                c0 = clauses_for_resource[0] if clauses_for_resource else None
                effect_witness[name] = ViolationRecord(
                    edge.to_phase, edge.transition_id, name, -1.0,
                    c0.source if c0 is not None else ev.source,
                    c0.confidence if c0 is not None else ev.confidence,
                    "resource_or_interface",
                )

        # Prefix-observation weakest preconditions and intrinsic evidence failures.
        for c in active:
            if c.id in grouped_ids:
                continue
            name = c.resource_name
            if name not in observed_resources and c.hard and c.missing_policy == "fail_closed":
                if name not in seen_valid_observation:
                    required_observed.setdefault(
                        name,
                        ViolationRecord(
                            edge.to_phase, edge.transition_id, name, -1.0,
                            c.source, 0.0, "missing_evidence",
                        ),
                    )
            elif name in observed_resources:
                for ev in evidence_list:
                    if ev.resource_name != name:
                        continue
                    if ev.missing and c.hard and c.missing_policy == "fail_closed":
                        best_intrinsic = better_violation(
                            best_intrinsic,
                            ViolationRecord(edge.to_phase, edge.transition_id, name, -1.0, ev.source, ev.confidence, "missing_evidence"),
                        )
                    uspec = compiled.uncertainty.get(name)
                    if uspec and uspec.min_confidence > 0 and ev.confidence < uspec.min_confidence and c.hard:
                        margin = (ev.confidence - uspec.min_confidence) / max(abs(uspec.min_confidence), EPS)
                        best_intrinsic = better_violation(
                            best_intrinsic,
                            ViolationRecord(
                                edge.to_phase, edge.transition_id,
                                name if name == "map_confidence" else "map_confidence",
                                margin, ev.source, ev.confidence,
                                "low_confidence" if uspec.missing_policy != "inconclusive_if_low_confidence" else "inconclusive_low_confidence",
                            ),
                        )

        for ev in evidence_list:
            if (
                registry.has(ev.resource_name)
                and (resource_filter is None or ev.resource_name in resource_filter)
                and not ev.missing and ev.value is not None
            ):
                seen_valid_observation.add(ev.resource_name)

    # A suffix with an intrinsic hard evidence failure can never be a viable
    # alternative for any prefix ledger.  Keep its proof outside the viability
    # antichain via the caller's proof envelope.
    if best_intrinsic is not None:
        return None, best_intrinsic

    return SuffixEffectSummary(
        transition_ids=tuple(suffix.transition_ids),
        cost=float(suffix.cost),
        effects=effects,
        required_observed=required_observed,
        effect_witness=effect_witness,
        first_active_witness=first_active_witness,
        active_clause_ids=tuple(sorted(active_clause_ids)),
        active_group_ids=tuple(sorted(active_group_ids)),
    ), None


def build_capability_precondition_antichain(
    kernel: CapabilityViabilityKernel,
    compiled: CompiledContract,
    predictions: Mapping[str, TransitionPrediction],
    registry: ResourceRegistry = DEFAULT_REGISTRY,
    *,
    no_conservative_margins: bool = False,
    default_beta: float = 1.0,
) -> CapabilityPreconditionAntichain:
    out = CapabilityPreconditionAntichain()
    for state, suffixes in kernel.suffixes.items():
        complete = kernel.is_reachable(state) and not kernel.overflowed(state)
        out.complete[state] = bool(complete)
        out.raw_suffix_count[state] = len(suffixes)
        out.raw_total += len(suffixes)
        if not complete:
            out.summaries[state] = ()
            continue

        candidates: List[SuffixEffectSummary] = []
        proof = kernel.proof_envelope_witness(state)
        seen_sig: Dict[Tuple[Any, ...], SuffixEffectSummary] = {}
        for suffix in suffixes:
            summary, intrinsic = _build_suffix_summary(
                suffix, kernel, compiled, predictions, registry,
                no_conservative_margins=no_conservative_margins,
                default_beta=default_beta,
            )
            if intrinsic is not None:
                proof = better_violation(proof, intrinsic)
                continue
            if summary is None:
                continue
            sig = _effect_signature(summary)
            prev = seen_sig.get(sig)
            if prev is None or (summary.cost, len(summary.transition_ids), summary.transition_ids) < (prev.cost, len(prev.transition_ids), prev.transition_ids):
                seen_sig[sig] = summary
        # The V5 kernel owns the structural proof envelope.  Intrinsic typed
        # evidence failures above are request/contract specific and are surfaced
        # by query-time failed summaries; they need not mutate the kernel.
        candidates = list(seen_sig.values())
        candidates.sort(key=lambda s: (s.cost, len(s.transition_ids), s.transition_ids))

        antichain: List[SuffixEffectSummary] = []
        for cand in candidates:
            if any(_summary_dominates(existing, cand, registry) for existing in antichain):
                continue
            antichain = [x for x in antichain if not _summary_dominates(cand, x, registry)]
            antichain.append(cand)
        antichain.sort(key=lambda s: (s.cost, len(s.transition_ids), s.transition_ids))
        out.summaries[state] = tuple(antichain)
        out.antichain_total += len(antichain)

    # Compile conditional proof-precondition antichains.  V5 showed that typed
    # pruning can skip a still-structurally-reachable subtree and thereby hide
    # the oracle/V2 rejected hard branch (predominantly board/alight interface
    # and physical failures).  A prefix-independent propagated witness is not
    # exact: it may cross a typed-infeasible prefix.  We therefore enumerate
    # hard-valid simple prefixes and attach a direct rejected-branch witness to
    # the prefix transformer.  At query time the witness is admitted only if the
    # current typed ledger satisfies that prefix's weakest precondition.
    max_proofs = max(1, int(kernel.max_paths_per_state))
    max_depth = max(1, int(kernel.max_depth))
    for root in kernel.reachable:
        found: List[ProofPrefixSummary] = []
        incomplete = False
        work: List[Tuple[State, Tuple[str, ...], float, frozenset[State]]] = [
            (root, (), 0.0, frozenset({root}))
        ]
        seen_paths: set[Tuple[Tuple[str, ...], str]] = set()
        while work:
            state, tids, cost, visited = work.pop()
            direct = kernel.direct_failure_witness.get(state)
            if direct is not None:
                key = (tids, str(direct.transition_id))
                if key not in seen_paths:
                    seen_paths.add(key)
                    seq = SuffixWitness(tuple(tids), float(cost))
                    summary, intrinsic = _build_suffix_summary(
                        seq, kernel, compiled, predictions, registry,
                        no_conservative_margins=no_conservative_margins,
                        default_beta=default_beta,
                    )
                    # If the prefix itself has an intrinsic typed/evidence
                    # failure, the rejected branch at its end is unreachable and
                    # must not be used as a proof for this ledger.
                    if summary is not None and intrinsic is None:
                        found.append(ProofPrefixSummary(summary, direct))
                        if len(found) > max_proofs:
                            incomplete = True
                            break
            if len(tids) >= max_depth:
                if kernel.valid_outgoing_ids.get(state):
                    incomplete = True
                continue
            for tid in reversed(kernel.valid_outgoing_ids.get(state, ())):
                edge = kernel.edge_by_id.get(tid)
                if edge is None:
                    continue
                nxt = (str(edge.to_anchor), str(edge.to_phase))
                if nxt in visited:
                    continue
                work.append((
                    nxt, tids + (tid,), cost + max(0.0, float(edge.cost)),
                    visited | frozenset({nxt}),
                ))

        found = found[:max_proofs]
        out.proof_raw_count[root] = len(found)
        out.proof_raw_total += len(found)
        out.proof_complete[root] = not incomplete

        # Compress only proofs carrying the same concrete rejected branch.
        # Different witnesses remain distinct because certificate ordering is
        # query dependent on whether their prefixes are executable.
        grouped: Dict[Tuple[Any, ...], List[ProofPrefixSummary]] = {}
        for ps in found:
            w = ps.witness
            wkey = (w.phase, w.transition_id, w.resource_type, float(w.signed_margin), w.evidence_source, float(w.confidence), w.reason)
            grouped.setdefault(wkey, []).append(ps)
        proof_antichain: List[ProofPrefixSummary] = []
        for rows in grouped.values():
            rows.sort(key=lambda x: (x.prefix.cost, len(x.prefix.transition_ids), x.prefix.transition_ids))
            keep: List[ProofPrefixSummary] = []
            for cand in rows:
                if any(_summary_dominates(x.prefix, cand.prefix, registry) for x in keep):
                    continue
                keep = [x for x in keep if not _summary_dominates(cand.prefix, x.prefix, registry)]
                keep.append(cand)
            proof_antichain.extend(keep)
        proof_antichain.sort(key=lambda x: (_vio_key(x.witness), x.prefix.cost, len(x.prefix.transition_ids), x.prefix.transition_ids))
        out.proof_summaries[root] = tuple(proof_antichain)
        out.proof_antichain_total += len(proof_antichain)
    return out


def _combine_effect(prefix: Mapping[str, Any], summary: SuffixEffectSummary, registry: ResourceRegistry) -> Dict[str, Any]:
    ledger = dict(prefix)
    for name, effect in summary.effects.items():
        if not registry.has(name):
            continue
        rt = registry.get(name)
        current = ledger.get(name, MissingEvidence(name, reason="not_observed"))
        if isinstance(effect, MissingEvidence):
            ledger[name] = effect
            continue
        if rt.kind == "categorical" and isinstance(effect, PredicateState):
            cur = neutral_value(rt) if is_missing(current) else current
            if isinstance(cur, PredicateState):
                failures = list(cur.failures or []) + list(effect.failures or [])
                ledger[name] = PredicateState(
                    ok=bool(cur.ok) and bool(effect.ok),
                    observed=effect.observed,
                    required=effect.required,
                    operator=effect.operator,
                    evidence_source=effect.evidence_source,
                    confidence=min(float(cur.confidence), float(effect.confidence)),
                    failures=failures,
                )
            else:
                ledger[name] = effect
        else:
            ledger[name] = update_value(current, effect, rt)
    return ledger


def _proof_prefix_executable(
    ledger: Mapping[str, Any],
    summary: SuffixEffectSummary,
    compiled: CompiledContract,
    registry: ResourceRegistry,
) -> bool:
    for name in summary.required_observed:
        if is_missing(ledger.get(name, MissingEvidence(name, reason="not_observed"))):
            return False
    combined = _combine_effect(ledger, summary, registry)
    clause_ids = set(summary.active_clause_ids)
    group_ids = set(summary.active_group_ids)
    clauses = [c for c in compiled.clauses if c.id in clause_ids]
    groups = [g for g in compiled.groups if g.group_id in group_ids]
    ok, _, _ = satisfy_all(combined, clauses, groups, registry)
    return bool(ok)


def _reachable_proof_witness(
    state: State,
    ledger: Mapping[str, Any],
    compiled: CompiledContract,
    antichain: CapabilityPreconditionAntichain,
    registry: ResourceRegistry,
) -> Tuple[ViolationRecord | None, int]:
    best: ViolationRecord | None = None
    checked = 0
    for ps in antichain.state_proofs(state):
        checked += 1
        if _proof_prefix_executable(ledger, ps.prefix, compiled, registry):
            best = better_violation(best, ps.witness)
    return best, checked


def evaluate_proof_precondition_antichain(
    state: State,
    ledger: Mapping[str, Any],
    compiled: CompiledContract,
    antichain: CapabilityPreconditionAntichain,
    registry: ResourceRegistry = DEFAULT_REGISTRY,
) -> ProofDecision:
    witness, checked = _reachable_proof_witness(state, ledger, compiled, antichain, registry)
    return ProofDecision(witness, checked)


def _typed_failure_witness(
    state: State,
    ledger: Mapping[str, Any],
    summary: SuffixEffectSummary,
    compiled: CompiledContract,
    registry: ResourceRegistry,
) -> ViolationRecord | None:
    # Prefix-observation preconditions are exact and must be checked before the
    # suffix effect is applied.
    best: ViolationRecord | None = None
    for name, vio in summary.required_observed.items():
        if is_missing(ledger.get(name, MissingEvidence(name, reason="not_observed"))):
            best = better_violation(best, vio)
    if best is not None:
        return best

    combined = _combine_effect(ledger, summary, registry)
    ok, _, failed = satisfy_all(combined, compiled.clauses, compiled.groups, registry)
    if ok:
        return None
    for name in failed:
        c = next((x for x in compiled.clauses if x.resource_name == name or x.id == name), None)
        base = summary.effect_witness.get(name) or summary.first_active_witness.get(name)
        if base is None:
            phase = state[1]
            tid = summary.transition_ids[0] if summary.transition_ids else "destination"
            src = c.source if c is not None else "capability_contract"
            conf = c.confidence if c is not None else 1.0
        else:
            phase, tid, src, conf = base.phase, base.transition_id, (c.source if c is not None else base.evidence_source), (c.confidence if c is not None else base.confidence)
        margin = signed_margin(combined, c, registry) if c is not None else -1.0
        best = better_violation(best, ViolationRecord(phase, tid, name, margin, src, conf, "resource_or_interface"))
    return best


def evaluate_rejection_precondition_antichain(
    state: State,
    ledger: Mapping[str, Any],
    compiled: CompiledContract,
    antichain: CapabilityPreconditionAntichain,
    registry: ResourceRegistry = DEFAULT_REGISTRY,
) -> RejectionDecision:
    """Evaluate the V7 diagnostic (worst-effect) antichain.

    Unlike the existential acceptance antichain, these summaries are retained
    under a certificate-preserving reverse dominance order.  They are queried
    only after acceptance viability has failed; therefore they cannot alter hard
    planning decisions or search expansions.
    """
    best: ViolationRecord | None = None
    checked = 0
    for summary in antichain.state_rejections(state):
        checked += 1
        vio = _typed_failure_witness(state, ledger, summary, compiled, registry)
        best = better_violation(best, vio)
    return RejectionDecision(best, checked)


def evaluate_precondition_antichain(
    state: State,
    ledger: Mapping[str, Any],
    compiled: CompiledContract,
    antichain: CapabilityPreconditionAntichain,
    registry: ResourceRegistry = DEFAULT_REGISTRY,
) -> AntichainDecision:
    summaries = antichain.state_summaries(state)
    if not antichain.state_complete(state):
        # Fail-open: the V5 suffix universe was incomplete for this state.
        return AntichainDecision(True, None, 0)
    if not summaries:
        return AntichainDecision(False, None, 0)

    best: ViolationRecord | None = None
    checked = 0
    for summary in summaries:
        checked += 1
        vio = _typed_failure_witness(state, ledger, summary, compiled, registry)
        if vio is None:
            return AntichainDecision(True, None, checked)
        best = better_violation(best, vio)
    return AntichainDecision(False, best, checked)
