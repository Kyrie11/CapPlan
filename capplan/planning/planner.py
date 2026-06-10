"""End-to-end CapPlan planner orchestration."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List

from capplan.data.schemas import AccessibilityGraph, CapabilityContract, CandidateTransition, PlannerResult, PUDOAnchor, VehicleInterface
from capplan.models.casa_net import CASAInput, CASANet
from capplan.planning.transition_generator import TransitionGenerator
from capplan.planning.typed_safe_budget_search import SearchConfig, TypedSafeBudgetSearch
from capplan.planning.trajectory_refinement import refine_trajectory
from capplan.semantics.capability_compiler import CapabilityCompiler
from capplan.semantics.resource_registry import DEFAULT_REGISTRY, ResourceRegistry
from capplan.semantics.service_automaton import ServiceAutomaton


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


class CapPlanPlanner:
    def __init__(self, config: PlannerConfig | None = None, registry: ResourceRegistry = DEFAULT_REGISTRY) -> None:
        self.config = config or PlannerConfig()
        self.registry = registry
        self.compiler = CapabilityCompiler(registry, disabled=self.config.no_capability_compiler, soft_only=self.config.soft_only_capability)
        self.automaton = ServiceAutomaton(disabled=self.config.no_service_automaton)
        self.casa = CASANet(disabled=self.config.no_casa_net_transitions)
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
        compiled = self.compiler.compile(contract, trip_context=trip_context)
        if transitions is None:
            transitions = self.generator.generate(episode_id, graph, pudo_anchors, vehicle)
        casa_out = self.casa(CASAInput(
            service_graph={"episode_id": episode_id, "n_anchors": len(pudo_anchors)},
            active_capability_tokens=compiled.tokens,
            phase_belief={"origin": 1.0},
            ego_agent_map_features=trip_context or {},
            transitions=transitions,
        ))
        skeleton, cert, diag = self.searcher.search(episode_id, compiled, transitions, casa_out.transition_predictions)
        traj = refine_trajectory(skeleton, route_length_m=float((trip_context or {}).get("route_length_m", 4000.0)))
        diag.update({"casa": casa_out.audit_history, "trajectory": traj, "config": self.config.__dict__})
        return PlannerResult(success=skeleton is not None, skeleton=skeleton, certificate=cert, diagnostics=diag)
