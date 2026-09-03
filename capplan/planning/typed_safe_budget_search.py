"""Typed safe-budget search over passenger-service transitions."""
from __future__ import annotations

import heapq
import itertools
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from capplan.data.schemas import CandidateTransition, LedgerStep, PassengerCompleteSkeleton, ResourceEvidence, ViolationRecord
from capplan.models.predictors import TransitionPrediction
from capplan.planning.capability_continuation_envelope import (
    EnvelopeDecision,
    build_continuation_envelope,
    evaluate_continuation,
)
from capplan.planning.certificates import select_certificate
from capplan.planning.capability_viability_kernel import (
    CapabilityViabilityKernel,
    build_capability_viability_kernel,
)
from capplan.semantics.capability_compiler import CompiledContract, UncertaintySpec
from capplan.semantics.resource_registry import DEFAULT_REGISTRY, ResourceRegistry
from capplan.semantics.service_automaton import ServiceAutomaton
from capplan.semantics.typed_resource_algebra import (
    MissingEvidence,
    active_clauses,
    active_groups,
    all_margins,
    conservative_value,
    dominates,
    init_ledger,
    is_missing,
    satisfy,
    satisfy_all,
    signed_margin,
    update_value,
)


@dataclass
class SearchConfig:
    beta: float = 1.0
    lambda_value: float = 0.5
    lambda_edge_validity: float = 0.25
    lambda_learned_feasibility: float = 0.20
    # V3: state-dependent ranker over the already hard-feasible successor frontier.
    lambda_frontier_ranker: float = 0.35
    # V4: contract-conditioned optimistic suffix envelope.  It may prune a
    # state only when even the independently optimistic typed continuation
    # violates a hard numeric clause; otherwise it contributes ordering terms.
    use_continuation_envelope: bool = False
    continuation_pruning: bool = True
    lambda_continuation_cost: float = 0.20
    lambda_continuation_margin: float = 0.35
    # V5: proof-carrying exact suffix viability. The kernel stores concrete
    # structurally executable suffixes and may typed-prune only when the suffix
    # set is complete for the state (overflow disables typed pruning).
    use_viability_kernel: bool = False
    viability_pruning: bool = True
    viability_typed_pruning: bool = True
    viability_generic_certificates: bool = False
    viability_max_paths_per_state: int = 256
    viability_max_depth: int = 16
    min_availability: float = 0.05
    # Untyped-ledger ablation: one unit of normalized budget per canonical
    # service transition.  Individual resource kinds may trade off because their
    # distinct sum/max/min/predicate algebras are deliberately removed.
    scalar_budget_limit: float = 7.0
    max_expansions: int = 10000
    no_typed_resource_ledger: bool = False
    no_conservative_margins: bool = False
    no_completion_value_guidance: bool = False
    soft_only_capability: bool = False


@dataclass
class SearchLabel:
    anchor: str
    phase: str
    resource_ledger: Dict[str, Any]
    cost: float
    history: List[CandidateTransition] = field(default_factory=list)
    steps: List[LedgerStep] = field(default_factory=list)

    def as_dominance_dict(self) -> Dict[str, Any]:
        return {"anchor": self.anchor, "phase": self.phase, "resource_ledger": self.resource_ledger, "cost": self.cost}


class TypedSafeBudgetSearch:
    def __init__(self, automaton: ServiceAutomaton, registry: ResourceRegistry = DEFAULT_REGISTRY, config: SearchConfig | None = None, frontier_ranker: Any | None = None) -> None:
        self.automaton = automaton
        self.registry = registry
        self.config = config or SearchConfig()
        # Optional V3 ranker. It is queried only after _try_expand has accepted a
        # successor under the symbolic typed contract, so it cannot change hard
        # feasibility.
        self.frontier_ranker = frontier_ranker

    def search(
        self,
        episode_id: str,
        compiled: CompiledContract,
        transitions: List[CandidateTransition],
        predictions: Dict[str, TransitionPrediction] | None = None,
        initial_anchor: str = "origin",
        initial_phase: str = "origin",
    ):
        predictions = predictions or {}
        clauses = [] if (compiled.soft_only or self.config.soft_only_capability) else compiled.clauses
        groups = [] if (compiled.soft_only or self.config.soft_only_capability) else compiled.groups
        init_resources = {c.resource_name for c in clauses}
        outgoing: Dict[Tuple[str, str], List[CandidateTransition]] = {}
        for e in transitions:
            outgoing.setdefault((e.from_anchor, e.from_phase), []).append(e)

        continuation_envelope = None
        if self.config.use_continuation_envelope and not self.automaton.disabled and not self.config.no_typed_resource_ledger:
            continuation_envelope = build_continuation_envelope(
                compiled, transitions, predictions, self.automaton, self.registry,
                min_availability=self.config.min_availability,
                no_conservative_margins=self.config.no_conservative_margins,
            )

        viability_kernel: CapabilityViabilityKernel | None = None
        if self.config.use_viability_kernel and not self.automaton.disabled and not self.config.no_typed_resource_ledger:
            viability_kernel = build_capability_viability_kernel(
                transitions, predictions, self.automaton,
                min_availability=self.config.min_availability,
                max_paths_per_state=self.config.viability_max_paths_per_state,
                max_depth=self.config.viability_max_depth,
            )
        viability_cache: Dict[Tuple[Any, ...], Tuple[bool, Optional[ViolationRecord], int]] = {}

        # The dataset uses concrete entrance IDs, not the literal string
        # ``origin``.  The offline oracle already derives these states from the
        # access transitions; runtime TSBS must do the same or it can falsely
        # fail before expanding the first edge.  A caller-provided entrance is
        # preferred, with transition-derived states as a deterministic fallback.
        requested_state = (str(initial_anchor), str(initial_phase))
        origin_states = sorted({
            (str(e.from_anchor), str(e.from_phase))
            for e in transitions
            if str(e.from_phase) == str(initial_phase) and str(e.action) == "access"
        })
        if requested_state in outgoing:
            start_states = [requested_state]
            initial_state_source = "caller_request_anchor"
        elif origin_states:
            start_states = origin_states
            initial_state_source = "transition_access_origin_fallback"
        else:
            start_states = [requested_state]
            initial_state_source = "literal_fallback"

        pq: List[Tuple[float, int, SearchLabel]] = []
        counter = itertools.count()
        labels: List[SearchLabel] = []
        for anchor, phase in start_states:
            ledger = ({"scalar_budget": 0.0} if self.config.no_typed_resource_ledger else init_ledger(init_resources, self.registry))
            start = SearchLabel(anchor, phase, ledger, 0.0, [], [])
            labels.append(start)
            heapq.heappush(pq, (0.0, next(counter), start))
        violations: List[ViolationRecord] = []

        expansions = 0
        continuation_pruned = 0
        continuation_scored = 0
        viability_pruned = 0
        viability_structural_pruned = 0
        viability_typed_pruned = 0
        viability_path_checks = 0
        viability_cache_hits = 0
        while pq and expansions < self.config.max_expansions:
            _, _, label = heapq.heappop(pq)
            expansions += 1
            if self.config.no_typed_resource_ledger:
                ok_final = float(label.resource_ledger.get("scalar_budget", 0.0)) <= float(self.config.scalar_budget_limit)
            else:
                ok_final, _, _ = satisfy_all(label.resource_ledger, clauses, groups, self.registry)
            if self.automaton.accept(label.phase) and ok_final:
                return PassengerCompleteSkeleton(
                    episode_id=episode_id,
                    passenger_id=compiled.passenger_id,
                    accepted=True,
                    transitions=[e.transition_id for e in label.history],
                    steps=label.steps,
                    final_ledger=label.resource_ledger,
                    cost=label.cost,
                ), None, {
                    "expansions": expansions,
                    "violations": len(violations),
                    "initial_states": start_states,
                    "initial_state_source": initial_state_source,
                    "continuation_pruned": continuation_pruned,
                    "continuation_scored": continuation_scored,
                    "viability_pruned": viability_pruned,
                    "viability_structural_pruned": viability_structural_pruned,
                    "viability_typed_pruned": viability_typed_pruned,
                    "viability_path_checks": viability_path_checks,
                    "viability_cache_hits": viability_cache_hits,
                    "viability_kernel": ({
                        "n_states": viability_kernel.n_states,
                        "n_valid_edges": viability_kernel.n_valid_edges,
                        "n_invalid_edges": viability_kernel.n_invalid_edges,
                        "overflow_states": len(viability_kernel.overflow_states),
                        "max_paths_per_state": viability_kernel.max_paths_per_state,
                        "max_depth": viability_kernel.max_depth,
                    } if viability_kernel is not None else None),
                    "continuation_envelope": ({
                        "n_states": continuation_envelope.n_states,
                        "n_edges": continuation_envelope.n_edges,
                        "resources": continuation_envelope.resources,
                        "iterations": continuation_envelope.iterations,
                    } if continuation_envelope is not None else None),
                }

            if self.automaton.disabled:
                # ``w/o service automaton`` must actually remove lifecycle-state
                # coupling. Candidate transitions are therefore connected by the
                # current *spatial anchor* only and their recorded source phase is
                # ignored for reachability. The historical implementation used the
                # normal (anchor, phase) adjacency whenever it was non-empty, so the
                # ablation followed the exact same lifecycle as full CapPlan.
                candidates = [e for e in transitions if e.from_anchor == label.anchor]
            else:
                candidates = list(outgoing.get((label.anchor, label.phase), []))
            pushable = []
            for e in candidates:
                ok, new_ledger, step, vios = self._try_expand(label, e, compiled, clauses, groups, predictions.get(e.transition_id))
                if not ok:
                    violations.extend(vios)
                    continue
                new_label = SearchLabel(e.to_anchor, e.to_phase, new_ledger, label.cost + e.cost, label.history + [e], label.steps + [step])
                continuation: EnvelopeDecision | None = None
                if continuation_envelope is not None:
                    continuation = evaluate_continuation(
                        (new_label.anchor, new_label.phase),
                        new_label.resource_ledger,
                        compiled,
                        continuation_envelope,
                        self.registry,
                    )
                    continuation_scored += 1
                    if self.config.continuation_pruning and continuation.impossible:
                        continuation_pruned += 1
                        failed_names = list(continuation.failed_resources) or ["continuation"]
                        for name in failed_names:
                            violations.append(ViolationRecord(
                                new_label.phase,
                                e.transition_id,
                                name,
                                float(continuation.optimistic_margins.get(name, continuation.min_margin)),
                                "capability_continuation_envelope",
                                1.0,
                                "no_relaxed_typed_continuation",
                            ))
                        continue

                if viability_kernel is not None and self.config.viability_pruning:
                    v_state = (new_label.anchor, new_label.phase)
                    if not viability_kernel.is_reachable(v_state):
                        viability_pruned += 1
                        viability_structural_pruned += 1
                        witness = viability_kernel.failure_witness(v_state)
                        if self.config.viability_generic_certificates or witness is None:
                            violations.append(ViolationRecord(
                                new_label.phase, e.transition_id, "viability_reachability", -1.0,
                                "capability_viability_kernel", 1.0, "no_structurally_executable_suffix",
                            ))
                        else:
                            violations.append(witness)
                        continue
                    if self.config.viability_typed_pruning and not viability_kernel.overflowed(v_state):
                        cache_key = (v_state, self._ledger_signature(new_label.resource_ledger))
                        cached = viability_cache.get(cache_key)
                        if cached is None:
                            viable, witness, checked = self._typed_suffix_viability(
                                new_label, compiled, clauses, groups, predictions, viability_kernel
                            )
                            viability_cache[cache_key] = (viable, witness, checked)
                        else:
                            viable, witness, checked = cached
                            viability_cache_hits += 1
                        viability_path_checks += int(checked)
                        if not viable:
                            viability_pruned += 1
                            viability_typed_pruned += 1
                            if self.config.viability_generic_certificates or witness is None:
                                violations.append(ViolationRecord(
                                    new_label.phase, e.transition_id, "typed_viability", -1.0,
                                    "capability_viability_kernel", 1.0, "no_typed_executable_suffix",
                                ))
                            else:
                                violations.append(witness)
                            continue
                d_new = new_label.as_dominance_dict()
                if any(dominates(existing.as_dominance_dict(), d_new, self.registry) for existing in labels):
                    continue
                labels = [l for l in labels if not dominates(d_new, l.as_dominance_dict(), self.registry)]
                labels.append(new_label)
                pushable.append((new_label, e, predictions.get(e.transition_id), continuation))

            # V3 scores the sibling frontier in one batch. The raw pairwise ranker
            # score is converted to a within-frontier softmax prior; a single feasible
            # successor therefore receives prior=1 and incurs no learned penalty.
            frontier_priors = [1.0 for _ in pushable]
            if self.frontier_ranker is not None and pushable:
                raw_scores = self.frontier_ranker.score_successors(
                    [(nl, edge) for nl, edge, _, _ in pushable], compiled, self.registry
                )
                if raw_scores:
                    m = max(raw_scores)
                    exps = [math.exp(max(-40.0, min(40.0, float(v) - m))) for v in raw_scores]
                    z = max(sum(exps), 1e-12)
                    frontier_priors = [max(1e-6, float(v) / z) for v in exps]
            for (new_label, e, pred, continuation), frontier_prior in zip(pushable, frontier_priors):
                heapq.heappush(
                    pq,
                    (self._priority(new_label, pred, frontier_prior=frontier_prior, continuation=continuation), next(counter), new_label),
                )

        cert = select_certificate(episode_id, compiled.passenger_id, violations)
        return None, cert, {
            "expansions": expansions,
            "violations": len(violations),
            "frontier_exhausted": True,
            "initial_states": start_states,
            "initial_state_source": initial_state_source,
            "continuation_pruned": continuation_pruned,
            "continuation_scored": continuation_scored,
            "viability_pruned": viability_pruned,
            "viability_structural_pruned": viability_structural_pruned,
            "viability_typed_pruned": viability_typed_pruned,
            "viability_path_checks": viability_path_checks,
            "viability_cache_hits": viability_cache_hits,
            "viability_kernel": ({
                "n_states": viability_kernel.n_states,
                "n_valid_edges": viability_kernel.n_valid_edges,
                "n_invalid_edges": viability_kernel.n_invalid_edges,
                "overflow_states": len(viability_kernel.overflow_states),
                "max_paths_per_state": viability_kernel.max_paths_per_state,
                "max_depth": viability_kernel.max_depth,
            } if viability_kernel is not None else None),
            "continuation_envelope": ({
                "n_states": continuation_envelope.n_states,
                "n_edges": continuation_envelope.n_edges,
                "resources": continuation_envelope.resources,
                "iterations": continuation_envelope.iterations,
            } if continuation_envelope is not None else None),
        }

    def _try_expand(self, label: SearchLabel, e: CandidateTransition, compiled: CompiledContract, clauses: Sequence, groups: Sequence, pred: Optional[TransitionPrediction]):
        # 1. Legal lifecycle.
        if not self.automaton.legal(label.phase, e.action, e.to_phase) or not e.tests.legal_lifecycle:
            return False, label.resource_ledger, None, [ViolationRecord(label.phase, e.transition_id, "lifecycle", -1.0, "service_automaton", 1.0, "illegal_lifecycle")]
        # 2. Anchor/spatial/topological/physical tests.
        for attr, resource, reason in [
            ("spatially_anchored", "anchor", "not_spatially_anchored"),
            ("topologically_valid", "topology", "not_topologically_valid"),
            ("physically_valid", "physical", "not_physically_valid"),
        ]:
            if not getattr(e.tests, attr):
                return False, label.resource_ledger, None, [ViolationRecord(e.to_phase, e.transition_id, resource, -1.0, "transition_tests", e.map_confidence, reason)]
        # 3. Interface validity independent of passenger-specific resource clauses.
        if not e.tests.interface_valid:
            return False, label.resource_ledger, None, [ViolationRecord(e.to_phase, e.transition_id, "interface", -1.0, "transition_tests", e.map_confidence, ";".join(e.tests.reasons) or "interface_invalid")]
        # 4. Dynamic availability from CASA prediction and tests.
        a_hat = pred.dynamic_availability if pred else e.availability
        if a_hat < self.config.min_availability or not e.tests.dynamically_available or e.dynamic.get("blocked", False):
            margin = float(a_hat) - self.config.min_availability
            return False, label.resource_ledger, None, [ViolationRecord(e.to_phase, e.transition_id, "availability", margin, "prediction", e.map_confidence, "dynamic_unavailable")]

        if self.config.no_typed_resource_ledger:
            evidence_list = pred.typed_evidence if pred else e.resource_evidence
            active = active_clauses(clauses, [e.from_phase, e.to_phase])
            active_groups_for_edge = active_groups(groups, [e.from_phase, e.to_phase])
            edge_burden = self._scalarized_edge_burden(active, active_groups_for_edge, evidence_list, compiled, e.to_phase)
            burden = float(label.resource_ledger.get("scalar_budget", 0.0)) + float(edge_burden)
            new_ledger = {"scalar_budget": burden}
            margins = {"scalar_budget": (float(self.config.scalar_budget_limit) - burden) / max(float(self.config.scalar_budget_limit), 1e-9)}
            step = LedgerStep(e.transition_id, e.to_phase, e.action, new_ledger, margins, [ev.__dict__ for ev in e.resource_evidence])
            # Unlike the historical implementation, this branch can reach an
            # accepting state. Feasibility is decided by the single global scalar
            # budget at acceptance rather than by a missing typed ledger.
            return True, new_ledger, step, []

        # 5. Resource update using conservative evidence and per-resource beta.
        new_ledger = dict(label.resource_ledger)
        evidence_list = pred.typed_evidence if pred else e.resource_evidence
        active = active_clauses(clauses, [e.from_phase, e.to_phase])
        active_by_resource: Dict[str, List[Any]] = {}
        for c in active:
            active_by_resource.setdefault(c.resource_name, []).append(c)
        observed_resources = set()
        for ev in evidence_list:
            if not self.registry.has(ev.resource_name):
                continue
            observed_resources.add(ev.resource_name)
            rt = self.registry.get(ev.resource_name)
            if ev.resource_name not in new_ledger:
                new_ledger[ev.resource_name] = MissingEvidence(ev.resource_name, phase=e.to_phase)
            clauses_for_resource = active_by_resource.get(ev.resource_name, [])
            # Categorical evidence must be evaluated clause-specifically so any_of
            # alternatives retain their own observed/required audit values.
            if rt.kind == "categorical" and clauses_for_resource:
                for c in clauses_for_resource:
                    beta = self._beta_for(compiled, c.resource_name)
                    new_ledger[ev.resource_name] = update_value(new_ledger.get(ev.resource_name), ev.value if not ev.missing else MissingEvidence(ev.resource_name, e.to_phase, ev.reason or "not_observed", ev.source, ev.confidence), rt, evidence=ev, clause=c)
            else:
                beta = self._beta_for(compiled, ev.resource_name)
                if self.config.no_conservative_margins:
                    beta = 0.0
                elif beta is None:
                    beta = self.config.beta
                xbar = MissingEvidence(ev.resource_name, e.to_phase, ev.reason or "not_observed", ev.source, ev.confidence) if ev.missing or ev.value is None else conservative_value(ev.value, ev.sigma, rt, beta=float(beta))
                new_ledger[ev.resource_name] = update_value(new_ledger.get(ev.resource_name), xbar, rt, evidence=ev)

        violations: List[ViolationRecord] = []
        active_groups_for_edge = active_groups(groups, [e.from_phase, e.to_phase])
        # 6. Uncertainty: missing hard evidence and confidence thresholds fail closed.
        grouped_clause_ids = {cid for g in active_groups_for_edge for cid in g.clause_ids}
        for c in active:
            if c.id in grouped_clause_ids:
                # Group logic, especially any_of, decides whether missing or low
                # confidence on one alternative is fatal.  Do not fail a ramp OR
                # lift OR low-floor group merely because one unused alternative is
                # unobserved.
                continue
            if c.resource_name not in observed_resources and c.hard and c.missing_policy == "fail_closed":
                # Missing evidence fails only when the ledger has not already
                # observed this active resource on an earlier edge.
                if is_missing(new_ledger.get(c.resource_name)) and (e.to_phase in c.phase_scope or e.from_phase in c.phase_scope or "all" in c.phase_scope):
                    violations.append(ViolationRecord(e.to_phase, e.transition_id, c.resource_name, -1.0, c.source, 0.0, "missing_evidence"))
            elif c.resource_name in observed_resources:
                evs = [ev for ev in evidence_list if ev.resource_name == c.resource_name]
                for ev in evs:
                    uspec = compiled.uncertainty.get(c.resource_name)
                    if ev.missing and c.hard and c.missing_policy == "fail_closed":
                        violations.append(ViolationRecord(e.to_phase, e.transition_id, c.resource_name, -1.0, ev.source, ev.confidence, "missing_evidence"))
                    if uspec and uspec.min_confidence > 0 and ev.confidence < uspec.min_confidence and c.hard:
                        margin = (ev.confidence - uspec.min_confidence) / max(abs(uspec.min_confidence), 1e-9)
                        violations.append(ViolationRecord(e.to_phase, e.transition_id, c.resource_name if c.resource_name == "map_confidence" else "map_confidence", margin, ev.source, ev.confidence, "low_confidence" if uspec.missing_policy != "inconclusive_if_low_confidence" else "inconclusive_low_confidence"))
        if violations:
            return False, new_ledger, None, violations

        # 7. Hard resource and requirement-group satisfaction.
        ok, margins, failed = satisfy_all(new_ledger, active, active_groups_for_edge, self.registry)
        if not ok:
            for name in failed:
                c = next((x for x in active if x.resource_name == name or x.id == name), None)
                violations.append(ViolationRecord(e.to_phase, e.transition_id, name, signed_margin(new_ledger, c, self.registry) if c else -1.0, c.source if c else "capability_contract", c.confidence if c else e.map_confidence, "resource_or_interface"))
            return False, new_ledger, None, violations
        step = LedgerStep(e.transition_id, e.to_phase, e.action, dict(new_ledger), margins, [ev.__dict__ for ev in e.resource_evidence])
        return True, new_ledger, step, []

    @staticmethod
    def _ledger_signature(ledger: Mapping[str, Any]) -> Tuple[Any, ...]:
        """Hashable exact-enough signature for per-request viability memoization."""
        out: List[Any] = []
        for name in sorted(ledger):
            value = ledger[name]
            if isinstance(value, MissingEvidence):
                out.append((name, "missing", value.phase, value.reason, value.evidence_source, float(value.confidence)))
            elif hasattr(value, "ok") and hasattr(value, "observed"):
                out.append((
                    name, "predicate", bool(getattr(value, "ok", False)),
                    repr(getattr(value, "observed", None)), repr(getattr(value, "required", None)),
                    str(getattr(value, "operator", "")),
                ))
            elif isinstance(value, (int, float)):
                out.append((name, "numeric", float(value)))
            else:
                out.append((name, "other", repr(value)))
        return tuple(out)

    def _typed_suffix_viability(
        self,
        label: SearchLabel,
        compiled: CompiledContract,
        clauses: Sequence,
        groups: Sequence,
        predictions: Mapping[str, TransitionPrediction],
        kernel: CapabilityViabilityKernel,
    ) -> Tuple[bool, Optional[ViolationRecord], int]:
        """Replay concrete suffix witnesses under the label's exact typed ledger.

        Returns ``(exists_feasible_suffix, best_failure_witness, checked_paths)``.
        The replay calls the same ``_try_expand`` implementation as forward TSBS,
        so grouped categorical predicates, missing evidence, conservative margins,
        and probabilistic resource algebra remain identical.
        """
        state = (label.anchor, label.phase)
        suffixes = kernel.state_suffixes(state)
        if not suffixes:
            return False, kernel.failure_witness(state), 0
        best: Optional[ViolationRecord] = None
        checked = 0
        for suffix in suffixes:
            checked += 1
            temp = SearchLabel(label.anchor, label.phase, dict(label.resource_ledger), label.cost, list(label.history), list(label.steps))
            path_violations: List[ViolationRecord] = []
            path_ok = True
            for tid in suffix.transition_ids:
                edge = kernel.edge_by_id.get(tid)
                if edge is None:
                    path_ok = False
                    break
                ok, new_ledger, step, vios = self._try_expand(
                    temp, edge, compiled, clauses, groups, predictions.get(tid)
                )
                if not ok:
                    path_violations.extend(vios)
                    path_ok = False
                    break
                temp = SearchLabel(
                    edge.to_anchor, edge.to_phase, new_ledger, temp.cost + edge.cost,
                    temp.history + [edge], temp.steps + ([step] if step is not None else []),
                )
            if path_ok:
                final_ok, _, failed = satisfy_all(temp.resource_ledger, clauses, groups, self.registry)
                if self.automaton.accept(temp.phase) and final_ok:
                    return True, None, checked
                if not final_ok:
                    for name in failed:
                        c = next((x for x in clauses if x.resource_name == name or x.id == name), None)
                        path_violations.append(ViolationRecord(
                            temp.phase, suffix.transition_ids[-1] if suffix.transition_ids else "destination",
                            name, signed_margin(temp.resource_ledger, c, self.registry) if c else -1.0,
                            c.source if c else "capability_contract", c.confidence if c else 1.0,
                            "resource_or_interface",
                        ))
            for vio in path_violations:
                if best is None:
                    best = vio
                else:
                    a = (float(vio.signed_margin), -float(vio.confidence), self.automaton.phase_index(vio.phase), str(vio.transition_id))
                    b = (float(best.signed_margin), -float(best.confidence), self.automaton.phase_index(best.phase), str(best.transition_id))
                    if a < b:
                        best = vio
        return False, best, checked

    def _scalarized_clause_utilization(self, clause, ev: ResourceEvidence | None, compiled: CompiledContract, phase: str) -> float:
        """Map one typed clause to a dimensionless scalar utilization.

        This is used only by the ``no_typed_resource_ledger`` ablation.  A value
        of 1 is approximately the clause threshold; categorical mismatch and
        missing evidence cost 2 units.  The construction intentionally permits
        trade-offs across resource kinds, unlike the full typed ledger.
        """
        if ev is None or ev.missing or ev.value is None:
            return 2.0
        rt = self.registry.get(clause.resource_name)
        if rt.kind == "categorical":
            state = {clause.resource_name: update_value(None, ev.value, rt, evidence=ev, clause=clause)}
            return 0.0 if satisfy(state, clause, self.registry) else 2.0
        try:
            beta = self._beta_for(compiled, clause.resource_name)
            x = conservative_value(ev.value, ev.sigma, rt, beta=float(beta))
            if is_missing(x):
                return 2.0
            val = float(x)
            th = float(clause.risk_tolerance if (rt.kind == "probabilistic" and clause.risk_tolerance is not None) else clause.threshold)
        except Exception:
            return 2.0
        if rt.feasibility_order == "larger":
            return max(0.0, th) / max(val, 1e-6)
        return max(0.0, val) / max(abs(th), 1e-6)

    def _scalarized_edge_burden(self, active, groups, evidence_list, compiled: CompiledContract, phase: str) -> float:
        by_resource: Dict[str, ResourceEvidence] = {}
        for ev in evidence_list:
            by_resource.setdefault(ev.resource_name, ev)
        clause_by_id = {c.id: c for c in active}
        grouped_ids = {cid for g in groups for cid in g.clause_ids}
        utils: List[float] = []
        for c in active:
            if c.id in grouped_ids:
                continue
            utils.append(self._scalarized_clause_utilization(c, by_resource.get(c.resource_name), compiled, phase))
        for g in groups:
            vals = [
                self._scalarized_clause_utilization(c, by_resource.get(c.resource_name), compiled, phase)
                for cid in g.clause_ids if (c := clause_by_id.get(cid)) is not None
            ]
            if not vals:
                continue
            if g.logic == "any_of":
                utils.append(min(vals))
            elif g.logic == "not":
                utils.append(0.0 if all(v > 1.0 for v in vals) else 2.0)
            else:
                utils.append(sum(vals) / len(vals))
        return float(sum(utils) / len(utils)) if utils else 0.0

    def _beta_for(self, compiled: CompiledContract, resource_name: str) -> float:
        if self.config.no_conservative_margins:
            return 0.0
        spec: UncertaintySpec | None = compiled.uncertainty.get(resource_name)
        return float(spec.beta_tau if spec else self.config.beta)

    def _priority(
        self,
        label: SearchLabel,
        pred: Optional[TransitionPrediction],
        *,
        frontier_prior: float = 1.0,
        continuation: EnvelopeDecision | None = None,
    ) -> float:
        value = pred.completion_value if pred else 0.5
        value_term = 0.0 if self.config.no_completion_value_guidance else -self.config.lambda_value * math.log(max(value, 1e-6))
        # Passenger-independent edge validity is a learned *ordering* prior.  It
        # must not be folded into dynamic availability or used to override the
        # symbolic transition tests.
        edge_prior = pred.edge_validity if pred else 1.0
        edge_term = -self.config.lambda_edge_validity * math.log(max(float(edge_prior), 1e-6))
        learned_feasibility = pred.learned_feasibility_prior if pred else 1.0
        feasibility_term = -self.config.lambda_learned_feasibility * math.log(max(float(learned_feasibility), 1e-6))
        frontier_term = -self.config.lambda_frontier_ranker * math.log(max(float(frontier_prior), 1e-6))
        continuation_cost_term = 0.0
        continuation_margin_term = 0.0
        if continuation is not None and continuation.structural_reachable:
            if math.isfinite(float(continuation.cost_to_go)):
                continuation_cost_term = self.config.lambda_continuation_cost * math.log1p(max(0.0, float(continuation.cost_to_go)))
            # Feasible optimistic continuations have min_margin >= 0.  Prefer
            # states with greater residual headroom without turning the margin
            # into a hard gate.
            continuation_margin_term = self.config.lambda_continuation_margin * max(0.0, 1.0 - float(continuation.min_margin))
        service_remaining = max(0, 7 - len(label.history))
        budget_heuristic = 0.0
        for v in label.resource_ledger.values():
            if isinstance(v, (int, float)) and math.isfinite(float(v)):
                budget_heuristic += 0.001 * abs(float(v))
        return (
            label.cost + service_remaining + budget_heuristic
            + value_term + edge_term + feasibility_term + frontier_term
            + continuation_cost_term + continuation_margin_term
        )
