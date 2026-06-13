"""Independent offline verifier and label oracle."""
from __future__ import annotations

from collections import deque
from dataclasses import asdict
from typing import Any, Dict, List, Sequence, Tuple

from capplan.data.schemas import (
    AccessibilityGraph,
    CandidateTransition,
    CapabilityContract,
    FailureCertificate,
    LedgerStep,
    PassengerCompleteSkeleton,
    PassengerEdgeLabel,
    PUDOAnchor,
    TransitionLabel,
    VehicleInterface,
    ViolationRecord,
    transition_label_from_transition,
)
from capplan.planning.certificates import select_certificate
from capplan.semantics.capability_compiler import CapabilityCompiler
from capplan.semantics.resource_registry import DEFAULT_REGISTRY, ResourceRegistry
from capplan.semantics.service_automaton import ServiceAutomaton
from capplan.semantics.typed_resource_algebra import (
    MissingEvidence,
    active_clauses,
    active_groups,
    conservative_value,
    init_ledger,
    satisfy_all,
    signed_margin,
    update_value,
)


class IndependentLabelOracle:
    """Verifier that never instantiates or calls the production planner."""

    def __init__(self, registry: ResourceRegistry = DEFAULT_REGISTRY, max_depth: int = 16) -> None:
        self.registry = registry
        self.compiler = CapabilityCompiler(registry)
        self.automaton = ServiceAutomaton()
        self.max_depth = max_depth

    def verify_transition(self, e: CandidateTransition) -> TransitionLabel:
        tests = e.tests
        z = bool(tests.legal_lifecycle and tests.spatially_anchored and tests.topologically_valid and tests.physically_valid and tests.interface_valid and tests.dynamically_available and e.availability > 0.0)
        return TransitionLabel(e.episode_id, e.transition_id, tests.legal_lifecycle, tests.spatially_anchored, tests.physically_valid, tests.topologically_valid, tests.interface_valid, tests.dynamically_available, z, {"availability": e.availability, "map_confidence": e.map_confidence, "reasons": tests.reasons})

    def verify_passenger_edge(self, e: CandidateTransition, contract: CapabilityContract) -> PassengerEdgeLabel:
        compiled = self.compiler.compile(contract, trip_context=contract.metadata.get("trip_modifiers", {}))
        z = self.verify_transition(e).z_e and self.automaton.legal(e.from_phase, e.action, e.to_phase)
        ledger = init_ledger({c.resource_name for c in compiled.clauses}, self.registry)
        violations: List[ViolationRecord] = []
        if z:
            ledger = self._update_ledger_for_edge(ledger, e, compiled, [e.from_phase, e.to_phase], violations)
        active = active_clauses(compiled.clauses, [e.from_phase, e.to_phase])
        groups = active_groups(compiled.groups, [e.from_phase, e.to_phase])
        ok, margins, failed = satisfy_all(ledger, active, groups, self.registry)
        uncertainty_ok = not any(v.reason in ("missing_evidence", "low_confidence", "inconclusive_low_confidence") for v in violations)
        if violations:
            ok = False
        return PassengerEdgeLabel(e.episode_id, compiled.passenger_id, e.transition_id, z, ok, uncertainty_ok, bool(z and ok and uncertainty_ok), margins, sorted(set(failed + [v.resource_type for v in violations])))

    def exhaustive_search(self, episode_id: str, contract: CapabilityContract, transitions: List[CandidateTransition]) -> Tuple[PassengerCompleteSkeleton | None, FailureCertificate | None]:
        compiled = self.compiler.compile(contract, trip_context=contract.metadata.get("trip_modifiers", {}))
        clauses = compiled.clauses
        groups = compiled.groups
        ledger0 = init_ledger({c.resource_name for c in clauses}, self.registry)
        q = deque([("origin", "origin", ledger0, [], [], 0.0)])
        visited = set()
        violations: List[ViolationRecord] = []
        outgoing: Dict[Tuple[str, str], List[CandidateTransition]] = {}
        for e in transitions:
            outgoing.setdefault((e.from_anchor, e.from_phase), []).append(e)
        while q:
            anchor, phase, ledger, hist, steps, cost = q.popleft()
            key = (anchor, phase, tuple(e.transition_id for e in hist))
            if key in visited:
                continue
            visited.add(key)
            ok_final, _, _ = satisfy_all(ledger, clauses, groups, self.registry)
            if self.automaton.accept(phase) and ok_final:
                return PassengerCompleteSkeleton(episode_id, compiled.passenger_id, True, [e.transition_id for e in hist], steps, ledger, cost), None
            if len(hist) >= self.max_depth:
                continue
            for e in outgoing.get((anchor, phase), []):
                edge_vios: List[ViolationRecord] = []
                if not self._edge_expandable(e, phase, edge_vios):
                    violations.extend(edge_vios)
                    continue
                ledger2 = dict(ledger)
                ledger2 = self._update_ledger_for_edge(ledger2, e, compiled, [e.from_phase, e.to_phase], edge_vios)
                active = active_clauses(clauses, [e.from_phase, e.to_phase])
                active_g = active_groups(groups, [e.from_phase, e.to_phase])
                ok, margins, failed = satisfy_all(ledger2, active, active_g, self.registry)
                if edge_vios or not ok:
                    for name in failed:
                        c = next((x for x in active if x.resource_name == name or x.id == name), None)
                        edge_vios.append(ViolationRecord(e.to_phase, e.transition_id, name, signed_margin(ledger2, c, self.registry) if c else -1.0, c.source if c else "capability_contract", c.confidence if c else e.map_confidence, "resource_or_interface"))
                    violations.extend(edge_vios)
                    continue
                step = LedgerStep(e.transition_id, e.to_phase, e.action, ledger2, margins, [ev.__dict__ for ev in e.resource_evidence])
                q.append((e.to_anchor, e.to_phase, ledger2, hist + [e], steps + [step], cost + e.cost))
        return None, select_certificate(episode_id, contract.passenger_id, violations)

    def verify_episode(
        self,
        episode_id: str,
        contract: CapabilityContract,
        graph: AccessibilityGraph | None,
        pudo: List[PUDOAnchor] | None,
        vehicle: VehicleInterface | None,
        transitions: List[CandidateTransition],
    ) -> Dict[str, object]:
        transition_labels = {e.transition_id: self.verify_transition(e) for e in transitions}
        passenger_edge_labels = {e.transition_id: self.verify_passenger_edge(e, contract) for e in transitions}
        skeleton, cert = self.exhaustive_search(episode_id, contract, transitions)
        return {
            "transition_labels": transition_labels,
            "passenger_edge_labels": passenger_edge_labels,
            "transition_validity": {k: v.z_e for k, v in transition_labels.items()},
            "passenger_edge_feasibility": {k: v.y_e_p for k, v in passenger_edge_labels.items()},
            "skeleton": skeleton,
            "certificate": cert,
        }

    def _edge_expandable(self, e: CandidateTransition, current_phase: str, violations: List[ViolationRecord]) -> bool:
        if not self.automaton.legal(current_phase, e.action, e.to_phase) or not e.tests.legal_lifecycle:
            violations.append(ViolationRecord(current_phase, e.transition_id, "lifecycle", -1.0, "service_automaton", 1.0, "illegal_lifecycle")); return False
        checks = [(e.tests.spatially_anchored, "anchor", "not_spatially_anchored"), (e.tests.topologically_valid, "topology", "not_topologically_valid"), (e.tests.physically_valid, "physical", "not_physically_valid"), (e.tests.interface_valid, "interface", "interface_invalid"), (e.tests.dynamically_available and e.availability > 0.0, "availability", "dynamic_unavailable")]
        for ok, res, reason in checks:
            if not ok:
                violations.append(ViolationRecord(e.to_phase, e.transition_id, res, -1.0, "transition_tests", e.map_confidence, reason)); return False
        return True

    def _update_ledger_for_edge(self, ledger: Dict[str, Any], e: CandidateTransition, compiled, phases: Sequence[str], violations: List[ViolationRecord]) -> Dict[str, Any]:
        active = active_clauses(compiled.clauses, phases)
        active_grouped_clause_ids = {cid for g in active_groups(compiled.groups, phases) for cid in g.clause_ids}
        active_by_resource: Dict[str, List[Any]] = {}
        for c in active:
            active_by_resource.setdefault(c.resource_name, []).append(c)
        observed = set()
        for ev in e.resource_evidence:
            if not self.registry.has(ev.resource_name):
                continue
            observed.add(ev.resource_name)
            rt = self.registry.get(ev.resource_name)
            if ev.resource_name not in ledger:
                ledger[ev.resource_name] = MissingEvidence(ev.resource_name, e.to_phase)
            clauses_for = active_by_resource.get(ev.resource_name, [])
            if rt.kind == "categorical" and clauses_for:
                for c in clauses_for:
                    val = MissingEvidence(ev.resource_name, e.to_phase, ev.reason or "not_observed", ev.source, ev.confidence) if ev.missing or ev.value is None else ev.value
                    ledger[ev.resource_name] = update_value(ledger.get(ev.resource_name), val, rt, evidence=ev, clause=c)
            else:
                beta = compiled.uncertainty.get(ev.resource_name).beta_tau if ev.resource_name in compiled.uncertainty else 1.0
                val = MissingEvidence(ev.resource_name, e.to_phase, ev.reason or "not_observed", ev.source, ev.confidence) if ev.missing or ev.value is None else conservative_value(ev.value, ev.sigma, rt, beta=beta)
                ledger[ev.resource_name] = update_value(ledger.get(ev.resource_name), val, rt, evidence=ev)
            non_grouped_hard = [c for c in clauses_for if c.hard and c.id not in active_grouped_clause_ids]
            uspec = compiled.uncertainty.get(ev.resource_name)
            if ev.missing and any(c.missing_policy == "fail_closed" for c in non_grouped_hard):
                violations.append(ViolationRecord(e.to_phase, e.transition_id, ev.resource_name, -1.0, ev.source, ev.confidence, "missing_evidence"))
            if non_grouped_hard and uspec and uspec.min_confidence > 0 and ev.confidence < uspec.min_confidence:
                margin = (ev.confidence - uspec.min_confidence) / max(uspec.min_confidence, 1e-9)
                violations.append(ViolationRecord(e.to_phase, e.transition_id, ev.resource_name if ev.resource_name == "map_confidence" else "map_confidence", margin, ev.source, ev.confidence, "low_confidence"))
        grouped_clause_ids = active_grouped_clause_ids
        for c in active:
            if c.id in grouped_clause_ids:
                continue
            if c.resource_name not in observed and c.hard and c.missing_policy == "fail_closed" and isinstance(ledger.get(c.resource_name), MissingEvidence):
                violations.append(ViolationRecord(e.to_phase, e.transition_id, c.resource_name, -1.0, c.source, 0.0, "missing_evidence"))
        return ledger


# Backward-compatible class name used by the scaffold.
LabelOracle = IndependentLabelOracle
