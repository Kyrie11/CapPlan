from capplan.data.accessibility_layer import synthetic_accessibility_graph
from capplan.data.pudo_interface_layer import synthetic_pudo_anchors, synthetic_vehicle_interface
from capplan.planning.transition_generator import TransitionGenerator


def test_transition_generation_has_all_core_actions():
    eid = "e"
    ts = TransitionGenerator().generate(eid, synthetic_accessibility_graph(eid), synthetic_pudo_anchors(eid), synthetic_vehicle_interface(eid))
    actions = {t.action for t in ts}
    for action in ["access", "wait", "board", "ride", "alight", "egress", "replan"]:
        assert action in actions
    assert all(t.resource_evidence is not None for t in ts)
