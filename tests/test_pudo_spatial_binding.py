from dataclasses import replace

from capplan.data.accessibility_layer import synthetic_accessibility_graph
from capplan.data.pudo_interface_layer import synthetic_pudo_anchors, synthetic_vehicle_interface
from capplan.planning.transition_generator import TransitionGenerator
from capplan.data.schemas import VehicleInterface


def _generated(eid="bind", vehicle=None, anchors=None):
    graph = synthetic_accessibility_graph(eid)
    anchors = anchors or synthetic_pudo_anchors(eid, graph=graph)
    vehicle = vehicle or synthetic_vehicle_interface(eid)
    return TransitionGenerator().generate(eid, graph, anchors, vehicle), anchors, vehicle


def test_pudo_anchor_adjacent_to_ped_node():
    graph = synthetic_accessibility_graph("bind")
    anchors = synthetic_pudo_anchors("bind", graph=graph)
    node_ids = {n.node_id for n in graph.nodes}
    assert all(a.adjacent_ped_node_id in node_ids for a in anchors)
    assert all(a.roadblock_id and a.lane_id for a in anchors)


def test_pudo_legal_stop_false_blocks_board_and_alight():
    graph = synthetic_accessibility_graph("illegal")
    anchors = synthetic_pudo_anchors("illegal", graph=graph)
    anchors = [replace(anchors[0], legal_stop=False), *anchors[1:]]
    transitions = TransitionGenerator().generate("illegal", graph, anchors, synthetic_vehicle_interface("illegal"))
    bad = [t for t in transitions if t.action in {"board", "alight"} and ":pudo_0" in t.transition_id]
    assert bad
    assert all(not t.tests.physically_valid or "illegal_stop" in t.tests.reasons for t in bad)


def test_vehicle_right_door_cannot_board_left_curb_when_side_required():
    transitions, _, vehicle = _generated("side", synthetic_vehicle_interface("side"))
    left_board = next(t for t in transitions if t.action == "board" and ":pudo_1:" in t.transition_id)
    assert vehicle.door_side == "right"
    assert not left_board.tests.interface_valid
    assert "vehicle_door_side_incompatible_with_curb" in left_board.tests.reasons


def test_ramp_clearance_depends_on_vehicle_and_pudo_clearance():
    graph = synthetic_accessibility_graph("clear")
    anchors = synthetic_pudo_anchors("clear", graph=graph)
    anchors = [replace(anchors[0], deployment_clearance_m=0.0), *anchors[1:]]
    vehicle = VehicleInterface("ramp_test", "clear", door_side="right", ramp=True, lift=False, low_floor=True, door_width_m=1.0, deployment_clearance_m=2.0, notification_modes=["visual", "audio", "app", "haptic"], dwell_time_s=60, kneeling=True)
    transitions = TransitionGenerator().generate("clear", graph, anchors, vehicle)
    board = next(t for t in transitions if t.action == "board" and ":pudo_0:" in t.transition_id)
    ev = next(e for e in board.resource_evidence if e.resource_name == "deployment_clearance_m")
    assert ev.value == 0.0
    assert not board.tests.interface_valid


def test_dynamic_blockage_changes_availability_label():
    graph = synthetic_accessibility_graph("blocked")
    anchors = synthetic_pudo_anchors("blocked", graph=graph)
    anchors = [replace(anchors[0], blockage_risk=0.95), *anchors[1:]]
    transitions = TransitionGenerator().generate("blocked", graph, anchors, synthetic_vehicle_interface("blocked"))
    board = next(t for t in transitions if t.action == "board" and ":pudo_0:" in t.transition_id)
    assert board.availability < 0.1
    assert not board.tests.dynamically_available
