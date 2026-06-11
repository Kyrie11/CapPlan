import pytest

from capplan.data.accessibility_layer import synthetic_accessibility_graph
from capplan.data.capability_contracts import default_contract
from capplan.data.pudo_interface_layer import synthetic_pudo_anchors, synthetic_vehicle_interface
from capplan.planning.planner import CapPlanPlanner, PlannerConfig
from capplan.planning.trajectory_refinement import refine_trajectory


def test_passenger_complete_requires_vehicle_safe_false_on_collision():
    eid = "collision"
    graph = synthetic_accessibility_graph(eid)
    anchors = synthetic_pudo_anchors(eid, graph=graph)
    vehicle = synthetic_vehicle_interface(eid)
    res = CapPlanPlanner().plan(eid, default_contract("collision:p0"), graph, anchors, vehicle, trip_context={"collision": True, "route_length_m": 400})
    assert res.skeleton is not None
    assert not res.success
    assert res.diagnostics["phase_accepted"] is True
    assert res.diagnostics["vehicle_safe"] is False
    assert res.certificate.resource_type == "vehicle_safety"


def test_placeholder_mode_cannot_be_used_for_paper_metrics():
    with pytest.raises(RuntimeError):
        refine_trajectory(None, mode="placeholder")


def test_nuplan_closed_loop_clearly_errors_when_unavailable():
    with pytest.raises(RuntimeError):
        refine_trajectory(None, mode="nuplan_closed_loop")


def test_soft_only_capability_can_return_capability_violating_plan_and_cvr_detects_it():
    eid = "soft"
    graph = synthetic_accessibility_graph(eid)
    anchors = synthetic_pudo_anchors(eid, graph=graph)
    vehicle = synthetic_vehicle_interface(eid)
    contract = default_contract("soft:p0")
    strict = []
    for c in contract.clauses:
        if c.resource_name == "access_distance_m":
            strict.append(type(c)(c.resource_name, c.phase_scope, c.operator, 5.0, c.kind, c.confidence, c.risk_tolerance, c.source, c.consent_scope, c.clause_id, c.hard, c.beta_tau, c.missing_policy, c.metadata))
        else:
            strict.append(c)
    contract = type(contract)(contract.passenger_id, strict, contract.metadata, contract.groups, contract.profile)
    hard = CapPlanPlanner().plan(eid, contract, graph, anchors, vehicle)
    soft = CapPlanPlanner(PlannerConfig(soft_only_capability=True)).plan(eid, contract, graph, anchors, vehicle)
    assert not hard.success
    assert soft.skeleton is not None
