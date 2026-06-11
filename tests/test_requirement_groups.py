from capplan.data.accessibility_layer import synthetic_accessibility_graph
from capplan.data.capability_contracts import default_contract
from capplan.data.pudo_interface_layer import synthetic_pudo_anchors, synthetic_vehicle_interface
from capplan.planning.planner import CapPlanPlanner
from capplan.semantics.typed_resource_algebra import satisfy_all
from capplan.semantics.capability_compiler import CapabilityCompiler


def test_ramp_or_lift_any_of_group_passes_with_lift_only_vehicle():
    eid = "lift"
    graph = synthetic_accessibility_graph(eid)
    pudo = synthetic_pudo_anchors(eid, graph=graph)
    vehicle = synthetic_vehicle_interface(eid, vehicle_id="lift_van_right")
    contract = default_contract("p")
    result = CapPlanPlanner().plan(eid, contract, graph, pudo, vehicle)
    assert result.success
    compiled = CapabilityCompiler().compile(contract)
    ok, _, _ = satisfy_all(result.skeleton.final_ledger, compiled.clauses, compiled.groups)
    assert ok


def test_missing_required_evidence_fails_or_inconclusive():
    contract = default_contract("p")
    compiled = CapabilityCompiler().compile(contract)
    assert compiled.uncertainty["map_confidence"].missing_policy in {"fail_closed", "inconclusive_if_low_confidence"}
