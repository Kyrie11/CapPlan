"""End-to-end CapPlan planner orchestration."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from capplan.data.schemas import AccessibilityGraph, CapabilityContract, CandidateTransition, FailureCertificate, PlannerResult, PUDOAnchor, VehicleInterface, ViolationRecord
from capplan.models.casa_net import CASAInput, CASANet
from capplan.planning.transition_generator import TransitionGenerator
from capplan.planning.typed_safe_budget_search import SearchConfig, TypedSafeBudgetSearch
from capplan.planning.trajectory_refinement import refine_trajectory
from capplan.semantics.capability_compiler import CapabilityCompiler
from capplan.semantics.resource_registry import DEFAULT_REGISTRY, ResourceRegistry
from capplan.semantics.service_automaton import ServiceAutomaton
from capplan.semantics.typed_resource_algebra import satisfy_all


@dataclass
class PlannerConfig:
    no_capability_compiler: bool = False
    no_service_automaton: bool = False
    no_casa_net_transitions: bool = False
    no_typed_resource_ledger: bool = False
    no_conservative_margins: bool = False
    no_completion_value_guidance: bool = False
    soft_only_capability: bool = False
    beta: float = 1.0
    trajectory_mode: str = "mock_strict"
    casa_mode: str = "heuristic_oracle_baseline"
    casa_checkpoint: str | Path | Dict[str, Any] | None = None


class CapPlanPlanner:
    def __init__(self, config: PlannerConfig | None = None, registry: ResourceRegistry = DEFAULT_REGISTRY) -> None:
        self.config = config or PlannerConfig()
        self.registry = registry
        self.compiler = CapabilityCompiler(registry, disabled=self.config.no_capability_compiler, soft_only=self.config.soft_only_capability)
        self.automaton = ServiceAutomaton(disabled=self.config.no_service_automaton)
        self.casa = CASANet(mode=self.config.casa_mode, disabled=self.config.no_casa_net_transitions, checkpoint=self.config.casa_checkpoint)
        self.generator = TransitionGenerator()
        self.searcher = TypedSafeBudgetSearch(
            self.automaton,
            registry,
            SearchConfig(
                beta=self.config.beta,
                no_typed_resource_ledger=self.config.no_typed_resource_ledger,
                no_conservative_margins=self.config.no_conservative_margins,
                no_completion_value_guidance=self.config.no_completion_value_guidance,
                soft_only_capability=self.config.soft_only_capability,
            ),
        )

    def plan(
        self,
        episode_id: str,
        contract: CapabilityContract,
        graph: AccessibilityGraph,
        pudo_anchors: List[PUDOAnchor],
        vehicle: VehicleInterface,
        transitions: List[CandidateTransition] | None = None,
        trip_context: Dict[str, Any] | None = None,
    ) -> PlannerResult:
        trip_context = trip_context or {}
        compiled = self.compiler.compile(contract, trip_context=trip_context)
        if transitions is None:
            transitions = self.generator.generate(episode_id, graph, pudo_anchors, vehicle, scene_context=trip_context)
        casa_out = self.casa(CASAInput(
            service_graph={"episode_id": episode_id, "n_anchors": len(pudo_anchors)},
            active_capability_tokens=compiled.tokens,
            phase_belief={"origin": 1.0},
            ego_agent_map_features=trip_context,
            transitions=transitions,
        ))
        skeleton, cert, diag = self.searcher.search(episode_id, compiled, transitions, casa_out.transition_predictions)
        traj = refine_trajectory(skeleton, route_length_m=float(trip_context.get("route_length_m", trip_context.get("route_corridor", {}).get("length_m", 4000.0) if isinstance(trip_context.get("route_corridor"), dict) else 4000.0)), mode=self.config.trajectory_mode, scene_context=trip_context)
        phase_accepted = bool(skeleton and skeleton.accepted and self.automaton.accept("destination"))
        vehicle_safe = bool(traj.get("vehicle_evaluated", False) and not traj.get("collision", False) and traj.get("drivable_area", True) and traj.get("rule_compliance", not traj.get("rule_violation", False)))
        capability_satisfied = False
        margins = {}
        failed = []
        if skeleton:
            capability_satisfied, margins, failed = satisfy_all(skeleton.final_ledger, [] if compiled.soft_only else compiled.clauses, [] if compiled.soft_only else compiled.groups, self.registry)
        passenger_complete = bool(phase_accepted and vehicle_safe and capability_satisfied)
        if skeleton is not None and not vehicle_safe and cert is None:
            v = ViolationRecord("ride", skeleton.transitions[-1] if skeleton.transitions else "trajectory", "vehicle_safety", -1.0, "trajectory_refinement", 1.0, "vehicle_unsafe")
            cert = FailureCertificate(episode_id, contract.passenger_id, v.phase, v.transition_id, v.resource_type, v.signed_margin, v.evidence_source, v.confidence, v.reason, [v])
        if skeleton is not None and not capability_satisfied and cert is None and failed:
            v = ViolationRecord("destination", skeleton.transitions[-1] if skeleton.transitions else "capability", failed[0], margins.get(failed[0], -1.0), "capability_contract", 1.0, "capability_not_satisfied")
            cert = FailureCertificate(episode_id, contract.passenger_id, v.phase, v.transition_id, v.resource_type, v.signed_margin, v.evidence_source, v.confidence, v.reason, [v])
        diag.update({
            "casa": casa_out.audit_history,
            "trajectory": traj,
            "config": self.config.__dict__,
            "phase_accepted": phase_accepted,
            "vehicle_safe": vehicle_safe,
            "capability_satisfied": capability_satisfied,
            "capability_margins": margins,
            "passenger_complete_semantics": "PC=Accept(sigma) AND Safe(tau_v) AND Sat(sigma,tau_v,Psi_p)",
        })
        return PlannerResult(success=passenger_complete, skeleton=skeleton, certificate=cert, diagnostics=diag)
