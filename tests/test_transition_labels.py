from capplan.data.accessibility_layer import synthetic_accessibility_graph
from capplan.data.capability_contracts import default_contract
from capplan.data.label_oracle import IndependentLabelOracle
from capplan.data.pudo_interface_layer import synthetic_pudo_anchors, synthetic_vehicle_interface
from capplan.planning.transition_generator import TransitionGenerator


def _sample():
    eid = "labels"
    graph = synthetic_accessibility_graph(eid)
    anchors = synthetic_pudo_anchors(eid, graph=graph)
    vehicle = synthetic_vehicle_interface(eid)
    transitions = TransitionGenerator().generate(eid, graph, anchors, vehicle, scene_context={"route_length_m": 1234.0})
    return eid, graph, anchors, vehicle, transitions


def test_transition_set_contains_all_service_phases_for_feasible_scene():
    _, _, _, _, transitions = _sample()
    phases = {(t.from_phase, t.to_phase) for t in transitions}
    for pair in [("origin", "access"), ("access", "wait"), ("wait", "board"), ("board", "ride"), ("ride", "alight"), ("alight", "egress"), ("egress", "destination")]:
        assert pair in phases


def test_access_transition_uses_saved_graph_path():
    _, _, _, _, transitions = _sample()
    access = next(t for t in transitions if t.action == "access")
    assert access.metadata["path_edge_ids"]
    assert next(e for e in access.resource_evidence if e.resource_name == "access_distance_m").source == "pedestrian_graph"


def test_board_transition_has_explicit_interface_test():
    _, _, _, _, transitions = _sample()
    board = next(t for t in transitions if t.action == "board")
    assert hasattr(board.tests, "interface_valid")
    assert "vehicle_id" in board.interface


def test_ride_transition_uses_scene_route_length_not_constant_600s():
    _, _, _, _, transitions = _sample()
    ride = next(t for t in transitions if t.action == "ride")
    ride_time = next(e.value for e in ride.resource_evidence if e.resource_name == "ride_time_s")
    assert ride.metadata["route_length_m"] == 1234.0
    assert ride_time != 600.0


def test_replan_transition_connects_real_alternative_anchor():
    _, _, _, _, transitions = _sample()
    replans = [t for t in transitions if t.action == "replan"]
    assert replans
    assert all(t.metadata["from_real_anchor"].startswith("pudo_") and t.metadata["to_real_anchor"].startswith("pudo_") for t in replans)


def test_transition_label_z_e_matches_legal_anchor_interface_available():
    _, _, _, _, transitions = _sample()
    oracle = IndependentLabelOracle()
    for t in transitions[:12]:
        label = oracle.verify_transition(t)
        assert label.z_e == bool(t.tests.z_e and t.availability > 0.0)


def test_y_e_p_computed_for_every_transition_not_only_skeleton_edges():
    eid, graph, anchors, vehicle, transitions = _sample()
    out = IndependentLabelOracle().verify_episode(eid, default_contract("labels:p0"), graph, anchors, vehicle, transitions)
    assert set(out["passenger_edge_labels"]) == {t.transition_id for t in transitions}
