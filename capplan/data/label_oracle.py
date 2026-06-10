"""Offline verifier/oracle for labels."""
from __future__ import annotations

from typing import Dict, List, Tuple

from capplan.data.schemas import AccessibilityGraph, CapabilityContract, CandidateTransition, FailureCertificate, PassengerCompleteSkeleton, PUDOAnchor, VehicleInterface
from capplan.planning.planner import CapPlanPlanner, PlannerConfig


class LabelOracle:
    """Exhaustive/high-quality verifier using the same typed algebra as inference.

    The oracle is allowed to use the symbolic transition set directly.  It returns
    transition validity, passenger-specific edge feasibility, skeleton labels, and
    failure certificates.
    """

    def __init__(self) -> None:
        self.planner = CapPlanPlanner(PlannerConfig(no_completion_value_guidance=True))

    def verify_episode(
        self,
        episode_id: str,
        contract: CapabilityContract,
        graph: AccessibilityGraph,
        pudo: List[PUDOAnchor],
        vehicle: VehicleInterface,
        transitions: List[CandidateTransition],
    ) -> Dict[str, object]:
        result = self.planner.plan(episode_id, contract, graph, pudo, vehicle, transitions=transitions)
        validity = {e.transition_id: bool(e.availability > 0.05 and not e.dynamic.get("blocked", False)) for e in transitions}
        feasible_edges = {e.transition_id: e.transition_id in (result.skeleton.transitions if result.skeleton else []) for e in transitions}
        return {
            "transition_validity": validity,
            "passenger_edge_feasibility": feasible_edges,
            "skeleton": result.skeleton,
            "certificate": result.certificate,
        }
