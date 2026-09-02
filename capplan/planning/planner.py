"""End-to-end CapPlan planner orchestration."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List

from capplan.data.schemas import AccessibilityGraph, CapabilityContract, CandidateTransition, FailureCertificate, PlannerResult, PUDOAnchor, VehicleInterface, ViolationRecord
from capplan.models.casa_net import CASAInput, CASANet
from capplan.models.frontier_ranker import FrontierRanker
from capplan.planning.transition_generator import TransitionGenerator
from capplan.planning.typed_safe_budget_search import SearchConfig, TypedSafeBudgetSearch
from capplan.planning.trajectory_refinement import refine_trajectory
from capplan.semantics.capability_compiler import CapabilityCompiler
from capplan.semantics.resource_registry import DEFAULT_REGISTRY, ResourceRegistry
from capplan.semantics.service_automaton import ServiceAutomaton
from capplan.semantics.typed_resource_algebra import satisfy_all


@dataclass
class PlannerConfig:
    algorithm_version: str = "V1"
    no_capability_compiler: bool = False
    no_service_automaton: bool = False
    no_casa_net_transitions: bool = False
    no_typed_resource_ledger: bool = False
    no_conservative_margins: bool = False
    no_completion_value_guidance: bool = False
    soft_only_capability: bool = False
    # Diagnostic head-isolation flags. These do not change dataset labels; they
    # selectively replace one learned CASA head with saved symbolic evidence to
    # localize a collapsed runtime pipeline before proposing a new algorithm.
    no_learned_demand: bool = False
    no_learned_uncertainty: bool = False
    no_learned_availability: bool = False
    # V2: learned demand/uncertainty are guidance signals; explicit typed evidence
    # remains authoritative for hard feasibility.
    evidence_grounded_runtime: bool = False
    no_learned_feasibility_guidance: bool = False
    # V3 Executable Capability Frontier (ECF) guidance.  The ranker scores only
    # successors that have already passed symbolic hard feasibility.
    frontier_ranker_checkpoint: str | Path | Dict[str, Any] | None = None
    frontier_ranker_device: str = "auto"
    no_frontier_ranker: bool = False
    frontier_ranker_weight: float = 0.35
    # Mechanism-control ablation: recover the exact V2 static learned-feasibility
    # + completion-value ordering while keeping V3 code/evaluation infrastructure.
    v2_reference_runtime: bool = False
    # V4 Capability Continuation Envelope (CCE).  The envelope uses explicit
    # typed evidence to summarize an optimistic suffix-to-destination and can
    # provide sound impossibility pruning plus continuation-aware ordering.
    no_continuation_envelope: bool = False
    no_continuation_pruning: bool = False
    no_continuation_priority: bool = False
    continuation_cost_weight: float = 0.20
    continuation_margin_weight: float = 0.35
    beta: float = 1.0
    trajectory_mode: str = "mock_strict"
    casa_mode: str = "heuristic_oracle_baseline"
    casa_checkpoint: str | Path | Dict[str, Any] | None = None
    casa_device: str = "auto"


class CapPlanPlanner:
    def __init__(self, config: PlannerConfig | None = None, registry: ResourceRegistry = DEFAULT_REGISTRY) -> None:
        self.config = config or PlannerConfig()
        self.registry = registry
        self.compiler = CapabilityCompiler(registry, disabled=self.config.no_capability_compiler, soft_only=self.config.soft_only_capability)
        self.automaton = ServiceAutomaton(disabled=self.config.no_service_automaton)
        self.casa = CASANet(
            mode=self.config.casa_mode, disabled=self.config.no_casa_net_transitions,
            checkpoint=self.config.casa_checkpoint, device=self.config.casa_device,
            no_learned_demand=self.config.no_learned_demand,
            no_learned_uncertainty=self.config.no_learned_uncertainty,
            no_learned_availability=self.config.no_learned_availability,
            evidence_grounded_runtime=self.config.evidence_grounded_runtime,
        )
        self.generator = TransitionGenerator()
        version = str(self.config.algorithm_version).upper()
        is_v3 = version.startswith("V3")
        is_v4 = version.startswith("V4")
        # V3 removes the empirically redundant completion-value head and replaces
        # V2's transition-static typed-feasibility prior with a learned local
        # frontier ranker.  V4 is intentionally different: it retires the V3
        # ranker after the full-test NO-GO result, restores the useful V2 static
        # prior, and adds a symbolic typed continuation envelope.
        use_v2_reference = bool((is_v3 or is_v4) and self.config.v2_reference_runtime)
        frontier_ranker = None
        if is_v3 and (not use_v2_reference) and (not self.config.no_frontier_ranker) and self.config.frontier_ranker_checkpoint:
            frontier_ranker = FrontierRanker(self.config.frontier_ranker_checkpoint, device=self.config.frontier_ranker_device)
        no_value = self.config.no_completion_value_guidance or ((is_v3 or is_v4) and not use_v2_reference)
        if is_v3 and not use_v2_reference:
            lambda_static = 0.0
        else:
            lambda_static = 0.0 if self.config.no_learned_feasibility_guidance else 0.20
        use_continuation = bool(is_v4 and (not use_v2_reference) and (not self.config.no_continuation_envelope))
        self.searcher = TypedSafeBudgetSearch(
            self.automaton,
            registry,
            SearchConfig(
                beta=self.config.beta,
                no_typed_resource_ledger=self.config.no_typed_resource_ledger,
                no_conservative_margins=self.config.no_conservative_margins,
                no_completion_value_guidance=no_value,
                soft_only_capability=self.config.soft_only_capability,
                lambda_learned_feasibility=lambda_static,
                lambda_frontier_ranker=(0.0 if (frontier_ranker is None or self.config.no_frontier_ranker) else float(self.config.frontier_ranker_weight)),
                use_continuation_envelope=use_continuation,
                continuation_pruning=bool(use_continuation and (not self.config.no_continuation_pruning)),
                lambda_continuation_cost=(0.0 if (not use_continuation or self.config.no_continuation_priority) else float(self.config.continuation_cost_weight)),
                lambda_continuation_margin=(0.0 if (not use_continuation or self.config.no_continuation_priority) else float(self.config.continuation_margin_weight)),
            ),
            frontier_ranker=frontier_ranker,
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
        service_request = trip_context.get("service_request") if isinstance(trip_context.get("service_request"), dict) else {}
        initial_anchor = str(
            trip_context.get("origin_entrance_id")
            or service_request.get("origin_entrance_id")
            or "origin"
        )
        destination_anchor = str(
            trip_context.get("destination_entrance_id")
            or service_request.get("destination_entrance_id")
            or "destination"
        )
        if transitions is None:
            transitions = self.generator.generate(
                episode_id, graph, pudo_anchors, vehicle,
                origin_anchor=initial_anchor, destination_anchor=destination_anchor,
                scene_context=trip_context,
            )
        casa_out = self.casa(CASAInput(
            service_graph={"episode_id": episode_id, "n_anchors": len(pudo_anchors)},
            active_capability_tokens=compiled.tokens,
            phase_belief={"origin": 1.0},
            ego_agent_map_features=trip_context,
            transitions=transitions,
        ))
        skeleton, cert, diag = self.searcher.search(
            episode_id, compiled, transitions, casa_out.transition_predictions,
            initial_anchor=initial_anchor, initial_phase="origin",
        )
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
            "failed_resources": failed,
            "passenger_complete_semantics": "PC=Accept(sigma) AND Safe(tau_v) AND Sat(sigma,tau_v,Psi_p)",
        })
        return PlannerResult(success=passenger_complete, skeleton=skeleton, certificate=cert, diagnostics=diag)
