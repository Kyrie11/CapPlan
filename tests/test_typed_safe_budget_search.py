from capplan.data.accessibility_layer import synthetic_accessibility_graph
from capplan.data.capability_contracts import default_contract
from capplan.data.pudo_interface_layer import synthetic_pudo_anchors, synthetic_vehicle_interface
from capplan.planning.planner import CapPlanPlanner, PlannerConfig
from capplan.semantics.capability_compiler import CapabilityCompiler
from capplan.semantics.typed_resource_algebra import satisfy


def test_typed_safe_budget_search_never_returns_violating_skeleton():
    eid = "ok"
    contract = default_contract("p")
    result = CapPlanPlanner().plan(eid, contract, synthetic_accessibility_graph(eid), synthetic_pudo_anchors(eid), synthetic_vehicle_interface(eid))
    assert result.success
    compiled = CapabilityCompiler().compile(contract)
    assert all(satisfy(result.skeleton.final_ledger, c) for c in compiled.clauses)


def test_stricter_contract_does_not_admit_infeasible_plan_accepted_by_weaker():
    eid = "strict"
    contract = default_contract("p")
    strict_clauses = []
    for c in contract.clauses:
        if c.resource_name == "access_distance_m":
            strict_clauses.append(type(c)(c.resource_name, c.phase_scope, c.operator, 10.0, c.kind, c.confidence, c.risk_tolerance, c.source, c.consent_scope))
        else:
            strict_clauses.append(c)
    from capplan.data.schemas import CapabilityContract
    strict = CapabilityContract("p_strict", strict_clauses)
    graph = synthetic_accessibility_graph(eid)
    pudo = synthetic_pudo_anchors(eid)
    veh = synthetic_vehicle_interface(eid)
    weak_res = CapPlanPlanner().plan(eid, contract, graph, pudo, veh)
    strict_res = CapPlanPlanner().plan(eid, strict, graph, pudo, veh)
    assert weak_res.success
    assert not strict_res.success


def test_ablation_flags_disable_intended_components():
    cfg = PlannerConfig(no_service_automaton=True, no_typed_resource_ledger=True, soft_only_capability=True)
    planner = CapPlanPlanner(cfg)
    assert planner.automaton.disabled
    assert planner.searcher.config.no_typed_resource_ledger
    assert planner.config.soft_only_capability
