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

def test_oracle_starts_from_real_origin_anchor_not_literal_origin():
    eid = "oracle_real_origin"
    graph = synthetic_accessibility_graph(eid)
    for node in graph.nodes:
        if node.node_id == "origin":
            node.node_id = "entrance_A"
        elif node.node_id == "destination":
            node.node_id = "entrance_B"
    for edge in graph.edges:
        if edge.from_node == "origin":
            edge.from_node = "entrance_A"
        if edge.to_node == "origin":
            edge.to_node = "entrance_A"
        if edge.from_node == "destination":
            edge.from_node = "entrance_B"
        if edge.to_node == "destination":
            edge.to_node = "entrance_B"
        edge.edge_id = edge.edge_id.replace("origin", "entrance_A").replace("destination", "entrance_B")

    anchors = synthetic_pudo_anchors(eid, graph=graph)
    vehicle = synthetic_vehicle_interface(eid)
    transitions = TransitionGenerator().generate(
        eid,
        graph,
        anchors,
        vehicle,
        origin_anchor="entrance_A",
        destination_anchor="entrance_B",
    )

    assert not any(t.from_anchor == "origin" for t in transitions)
    skeleton, cert = IndependentLabelOracle().exhaustive_search(eid, default_contract("oracle:p0"), transitions)
    assert cert is None
    assert skeleton is not None
    assert skeleton.transitions[0].startswith(f"{eid}:access:entrance_A->")

