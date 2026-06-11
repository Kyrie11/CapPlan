"""Typed safe-budget search over passenger-service transitions."""
from __future__ import annotations

import heapq
import itertools
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from capplan.data.schemas import CandidateTransition, LedgerStep, PassengerCompleteSkeleton, ResourceEvidence, ViolationRecord
from capplan.models.predictors import TransitionPrediction
from capplan.planning.certificates import select_certificate
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
    min_availability: float = 0.05
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
    def __init__(self, automaton: ServiceAutomaton, registry: ResourceRegistry = DEFAULT_REGISTRY, config: SearchConfig | None = None) -> None:
        self.automaton = automaton
        self.registry = registry
        self.config = config or SearchConfig()

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
        ledger = init_ledger(init_resources, self.registry)
        start = SearchLabel(initial_anchor, initial_phase, ledger, 0.0, [], [])
        pq: List[Tuple[float, int, SearchLabel]] = []
        counter = itertools.count()
        heapq.heappush(pq, (0.0, next(counter), start))
        labels: List[SearchLabel] = [start]
        violations: List[ViolationRecord] = []
        outgoing: Dict[Tuple[str, str], List[CandidateTransition]] = {}
        for e in transitions:
            outgoing.setdefault((e.from_anchor, e.from_phase), []).append(e)

        expansions = 0
        while pq and expansions < self.config.max_expansions:
            _, _, label = heapq.heappop(pq)
            expansions += 1
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
                ), None, {"expansions": expansions, "violations": len(violations)}

            candidates = list(outgoing.get((label.anchor, label.phase), []))
            if not candidates:
                # Phase-only fallback is intentionally restricted to the disabled
                # automaton ablation and to transitions whose source anchor is a
                # real current anchor or a replan edge.
                if self.automaton.disabled:
                    candidates = [e for e in transitions if e.from_phase == label.phase]
            for e in candidates:
                ok, new_ledger, step, vios = self._try_expand(label, e, compiled, clauses, groups, predictions.get(e.transition_id))
                if not ok:
                    violations.extend(vios)
                    continue
                new_label = SearchLabel(e.to_anchor, e.to_phase, new_ledger, label.cost + e.cost, label.history + [e], label.steps + [step])
                d_new = new_label.as_dominance_dict()
                if any(dominates(existing.as_dominance_dict(), d_new, self.registry) for existing in labels):
                    continue
                labels = [l for l in labels if not dominates(d_new, l.as_dominance_dict(), self.registry)]
                labels.append(new_label)
                heapq.heappush(pq, (self._priority(new_label, predictions.get(e.transition_id)), next(counter), new_label))

        cert = select_certificate(episode_id, compiled.passenger_id, violations)
        return None, cert, {"expansions": expansions, "violations": len(violations), "frontier_exhausted": True}

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
            burden = float(label.resource_ledger.get("scalar_budget", 0.0))
            for ev in (pred.typed_evidence if pred else e.resource_evidence):
                if ev.kind != "categorical" and ev.value is not None:
                    try:
                        burden += abs(float(ev.value))
                    except Exception:
                        pass
            new_ledger = {"scalar_budget": burden}
            step = LedgerStep(e.transition_id, e.to_phase, e.action, new_ledger, {}, [ev.__dict__ for ev in e.resource_evidence])
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
                # Group logic, especially any_of, decides whether missing one
                # alternative is fatal.
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

    def _beta_for(self, compiled: CompiledContract, resource_name: str) -> float:
        if self.config.no_conservative_margins:
            return 0.0
        spec: UncertaintySpec | None = compiled.uncertainty.get(resource_name)
        return float(spec.beta_tau if spec else self.config.beta)

    def _priority(self, label: SearchLabel, pred: Optional[TransitionPrediction]) -> float:
        value = pred.completion_value if pred else 0.5
        value_term = 0.0 if self.config.no_completion_value_guidance else -self.config.lambda_value * math.log(max(value, 1e-6))
        service_remaining = max(0, 7 - len(label.history))
        budget_heuristic = 0.0
        for v in label.resource_ledger.values():
            if isinstance(v, (int, float)) and math.isfinite(float(v)):
                budget_heuristic += 0.001 * abs(float(v))
        return label.cost + service_remaining + budget_heuristic + value_term
