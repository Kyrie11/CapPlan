"""Typed safe-budget search over CASA service transitions."""
from __future__ import annotations

import heapq
import itertools
import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

from capplan.data.schemas import CandidateTransition, LedgerStep, PassengerCompleteSkeleton, ViolationRecord
from capplan.models.predictors import TransitionPrediction
from capplan.semantics.capability_compiler import CompiledContract
from capplan.semantics.resource_registry import DEFAULT_REGISTRY, ResourceRegistry
from capplan.semantics.service_automaton import ServiceAutomaton
from capplan.semantics.typed_resource_algebra import (
    active_clauses,
    all_margins,
    conservative_value,
    dominates,
    initial_value,
    satisfy,
    signed_margin,
    update_value,
)
from capplan.planning.certificates import select_certificate


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
        predictions: Dict[str, TransitionPrediction],
        initial_anchor: str = "origin",
        initial_phase: str = "origin",
    ):
        clauses = [] if (compiled.soft_only or self.config.soft_only_capability) else compiled.clauses
        init_resources = {c.resource_name for c in clauses}
        ledger = {name: initial_value(self.registry.get(name)) for name in init_resources}
        start = SearchLabel(initial_anchor, initial_phase, ledger, 0.0, [], [])
        pq: List[Tuple[float, int, SearchLabel]] = []
        counter = itertools.count()
        heapq.heappush(pq, (0.0, next(counter), start))
        labels: List[SearchLabel] = [start]
        violations: List[ViolationRecord] = []
        outgoing: Dict[Tuple[str, str], List[CandidateTransition]] = {}
        for e in transitions:
            outgoing.setdefault((e.from_anchor, e.from_phase), []).append(e)
            # Also allow phase-only matching for synthetic transitions whose anchor
            # names are replan placeholders.
            outgoing.setdefault(("*", e.from_phase), []).append(e)

        expansions = 0
        while pq and expansions < self.config.max_expansions:
            _, _, label = heapq.heappop(pq)
            expansions += 1
            if self.automaton.accept(label.phase) and self._all_satisfied(label.resource_ledger, clauses):
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
            # If anchor-specific transitions are absent but phase transitions exist,
            # permit matching by phase. This supports nuPlan/mock adapters whose
            # anchors are generated at runtime.
            if not candidates:
                candidates = [e for e in transitions if e.from_phase == label.phase]
            for e in candidates:
                ok, new_ledger, step, vios = self._try_expand(label, e, compiled, clauses, predictions.get(e.transition_id))
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

    def _try_expand(self, label: SearchLabel, e: CandidateTransition, compiled: CompiledContract, clauses, pred: Optional[TransitionPrediction]):
        violations: List[ViolationRecord] = []
        if not self.automaton.legal(label.phase, e.action, e.to_phase):
            return False, label.resource_ledger, None, [ViolationRecord(label.phase, e.transition_id, "lifecycle", -1.0, "service_automaton", 1.0, "illegal_lifecycle")]
        if e.availability < self.config.min_availability:
            return False, label.resource_ledger, None, [ViolationRecord(label.phase, e.transition_id, "availability", e.availability - self.config.min_availability, "prediction", e.map_confidence, "dynamic_unavailable")]
        if e.dynamic.get("blocked"):
            return False, label.resource_ledger, None, [ViolationRecord(label.phase, e.transition_id, "dynamic_blockage", -1.0, "perception", e.map_confidence, "blocked")]

        if self.config.no_typed_resource_ledger:
            # Targeted ablation: collapse numeric resources into one scalar burden.
            burden = float(label.resource_ledger.get("scalar_budget", 0.0))
            for ev in e.resource_evidence:
                if ev.kind != "categorical":
                    burden += abs(float(ev.value))
            new_ledger = {"scalar_budget": burden}
            step = LedgerStep(e.transition_id, e.to_phase, e.action, new_ledger, {}, [ev.__dict__ for ev in e.resource_evidence])
            return True, new_ledger, step, []

        new_ledger = dict(label.resource_ledger)
        # Apply categorical clauses using clause-specific compatibility before
        # storing predicate conjunction as a ledger value.
        for ev in (pred.typed_evidence if pred else e.resource_evidence):
            if not self.registry.has(ev.resource_name):
                continue
            rt = self.registry.get(ev.resource_name)
            if ev.resource_name not in new_ledger:
                new_ledger[ev.resource_name] = initial_value(rt)
            beta = 0.0 if self.config.no_conservative_margins else self.config.beta
            if rt.kind == "categorical":
                ok = True
                for c in [c for c in clauses if c.resource_name == ev.resource_name and (e.to_phase in c.phase_scope or e.from_phase in c.phase_scope or "all" in c.phase_scope)]:
                    from capplan.semantics.typed_resource_algebra import compatible
                    ok = ok and compatible(ev.value, c.threshold, c.operator)
                if not [c for c in clauses if c.resource_name == ev.resource_name]:
                    ok = bool(ev.value)
                new_ledger[ev.resource_name] = bool(new_ledger.get(ev.resource_name, True)) and ok
            else:
                xbar = conservative_value(ev.value, ev.sigma, rt, beta=beta)
                new_ledger[ev.resource_name] = update_value(new_ledger.get(ev.resource_name, initial_value(rt)), xbar, rt)

        check_phases = [e.from_phase, e.to_phase]
        active = active_clauses(clauses, check_phases)
        for c in active:
            if not satisfy(new_ledger, c, self.registry):
                violations.append(ViolationRecord(
                    phase=e.to_phase,
                    transition_id=e.transition_id,
                    resource_type=c.resource_name,
                    signed_margin=signed_margin(new_ledger, c, self.registry),
                    evidence_source=c.source,
                    confidence=c.confidence,
                    reason="resource_or_interface",
                ))
        if violations:
            return False, new_ledger, None, violations
        margins = all_margins(new_ledger, active, self.registry)
        step = LedgerStep(e.transition_id, e.to_phase, e.action, dict(new_ledger), margins, [ev.__dict__ for ev in e.resource_evidence])
        return True, new_ledger, step, []

    def _all_satisfied(self, ledger: Mapping[str, Any], clauses) -> bool:
        return all(satisfy(ledger, c, self.registry) for c in clauses)

    def _priority(self, label: SearchLabel, pred: Optional[TransitionPrediction]) -> float:
        value = pred.completion_value if pred else 0.5
        value_term = 0.0 if self.config.no_completion_value_guidance else -self.config.lambda_value * math.log(max(value, 1e-6))
        service_remaining = max(0, 7 - len(label.history))
        budget_heuristic = 0.0
        for v in label.resource_ledger.values():
            if isinstance(v, (int, float)) and math.isfinite(float(v)):
                budget_heuristic += 0.001 * abs(float(v))
        return label.cost + service_remaining + budget_heuristic + value_term
