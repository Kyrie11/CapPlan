import inspect

import capplan.data.label_oracle as label_oracle
from capplan.data.accessibility_layer import synthetic_accessibility_graph
from capplan.data.capability_contracts import default_contract
from capplan.data.label_oracle import IndependentLabelOracle
from capplan.data.pudo_interface_layer import synthetic_pudo_anchors, synthetic_vehicle_interface
from capplan.planning.transition_generator import TransitionGenerator


def test_oracle_does_not_instantiate_capplan_planner():
    src = inspect.getsource(label_oracle.IndependentLabelOracle)
    assert "CapPlanPlanner" not in src


def test_certificate_label_independent_from_planner_prediction():
    eid = "oracle"
    graph = synthetic_accessibility_graph(eid)
    anchors = synthetic_pudo_anchors(eid, graph=graph)
    vehicle = synthetic_vehicle_interface(eid)
    transitions = TransitionGenerator().generate(eid, graph, anchors, vehicle)
    # Make all board transitions unavailable in the verifier input; no planner or CASA
    # prediction is consulted to create the certificate.
    for t in transitions:
        if t.action == "board":
            t.availability = 0.0
            t.tests = type(t.tests)(t.tests.legal_lifecycle, t.tests.spatially_anchored, t.tests.topologically_valid, t.tests.physically_valid, t.tests.interface_valid, False, ["blocked"])
    skeleton, cert = IndependentLabelOracle().exhaustive_search(eid, default_contract("oracle:p0"), transitions)
    assert skeleton is None
    assert cert is not None
    assert cert.resource_type in {"availability", "interface", "door_side", "cross_slope", "slope", "curb_ramp"}


def test_oracle_starts_from_real_request_bound_origin_anchor():
    """Regression: real service requests do not use a literal ``origin`` anchor."""
    eid = "oracle_real_origin_anchor"
    graph = synthetic_accessibility_graph(eid)
    anchors = synthetic_pudo_anchors(eid, graph=graph)
    vehicle = synthetic_vehicle_interface(eid)
    transitions = TransitionGenerator().generate(eid, graph, anchors, vehicle)

    # Rename only the origin-phase source anchor to mimic a bound real entrance.
    # The pre-fix oracle starts at ("origin", "origin") and therefore cannot
    # expand a single edge from this otherwise identical service graph.
    real_origin_anchor = "entrance_real_42"
    for t in transitions:
        if t.from_phase == "origin":
            t.from_anchor = real_origin_anchor

    skeleton, cert = IndependentLabelOracle().exhaustive_search(
        eid, default_contract("oracle:p0"), transitions
    )
    # This fixture may fail later on a passenger resource, but it must never
    # fail simply because no transition was reachable from a hard-coded anchor.
    assert skeleton is not None or (cert is not None and cert.phase != "origin")
