from types import SimpleNamespace

import capplan.data.pudo_interface_layer as pil
from capplan.data.schemas import AccessibilityGraph, AccessibilityNode, Pose2D


class _FakeLane:
    id = "lane0"
    roadblock_id = "rb0"
    baseline_path = SimpleNamespace(discrete_path=[
        SimpleNamespace(x=0.0, y=0.0, heading=0.0),
        SimpleNamespace(x=20.0, y=0.0, heading=0.0),
    ])

    def get_roadblock_id(self):
        return self.roadblock_id


def _graph():
    return AccessibilityGraph(
        "nuplan_test",
        [
            AccessibilityNode("origin", 0.0, 0.0, "entrance", pose=Pose2D(0.0, 0.0, 0.0, "map")),
            AccessibilityNode("destination", 20.0, 0.0, "entrance", pose=Pose2D(20.0, 0.0, 0.0, "map")),
        ],
        [],
    )


def test_route_pudo_does_not_fabricate_clearance_from_walkway_presence(monkeypatch):
    monkeypatch.setattr(pil, "_collect_route_lane_objects", lambda *args, **kwargs: [_FakeLane()])
    monkeypatch.setattr(pil, "_walkway_context", lambda *args, **kwargs: (True, None, 0.5))

    anchors = pil.nuplan_route_pudo_anchors("nuplan_test", object(), ["rb0"], _graph(), n=1)

    assert anchors
    assert anchors[0].sidewalk_width_m is None
    assert anchors[0].deployment_clearance_m is None
    assert anchors[0].source == "nuplan_route_map_walkway_unmeasured"


def test_route_pudo_keeps_polygon_width_but_does_not_infer_deployment_clearance(monkeypatch):
    monkeypatch.setattr(pil, "_collect_route_lane_objects", lambda *args, **kwargs: [_FakeLane()])
    monkeypatch.setattr(pil, "_walkway_context", lambda *args, **kwargs: (True, 1.8, 0.0))

    anchors = pil.nuplan_route_pudo_anchors("nuplan_test", object(), ["rb0"], _graph(), n=1)

    assert anchors[0].sidewalk_width_m == 1.8
    assert anchors[0].deployment_clearance_m is None
    assert anchors[0].legal_stop is False
    assert anchors[0].legal_stop_source == "nuplan_route_geometry_candidate_no_legality_evidence"
    assert anchors[0].source == "nuplan_route_map_walkway_width_proxy_candidate"


def test_paper_evidence_requires_independent_interface_inventory():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("build_pudo_evidence", Path("scripts/build_pudo_evidence.py"))
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)

    base = {
        "source": "official_candidate",
        "legal_stop": True,
        "legal_stop_source": "manual_posted_sign_audit",
        "adjacent_ped_node_id": "ped0",
        "curb_height_m": 0.02,
        "sidewalk_width_m": 1.8,
        "deployment_clearance_m": 1.4,
    }
    complete, eligible, status = mod._paper_evidence_flags(base)
    assert not complete and not eligible
    assert "non_tier_a:curb_height_m" in status
    assert "no_independent_tier_a_legality" in status

    audited = dict(
        base,
        source="official_candidate",
        curb_ramp=True, running_slope=0.02, cross_slope=0.01, surface="paved",
        legal_basis="audited posted regulation permits passenger loading",
        legal_stop_tier="A_manual_audit", legal_stop_audited=True,
        field_provenance={
            k: {"source": "manual_interface_audit", "evidence_tier": "A_manual_audit", "audited": True}
            for k in mod.PAPER_PHYSICAL
        },
    )
    complete, eligible, status = mod._paper_evidence_flags(audited)
    assert complete and eligible and status == "paper_ready"


def test_dynamic_blockage_input_is_causal_and_future_is_label_only():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("build_pudo_evidence_dynamic", Path("scripts/build_pudo_evidence.py"))
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)

    scene = {
        "agent_history": [
            {"iteration": 0, "observation_available": True, "objects": []},
            {"iteration": 1, "observation_available": True, "objects": [{"x": 0.5, "y": 0.0}]},
            {"iteration": 2, "observation_available": True, "objects": [{"x": 20.0, "y": 0.0}]},
        ]
    }
    ev = mod._blockage_from_agents(0.0, 0.0, scene, radius=2.0)
    assert ev["blockage_risk"] == 0.0  # current frame only; no future leakage
    assert ev["dynamic_confidence"] == 1.0
    assert ev["future_blockage_rate_label"] == 0.5
    assert ev["dynamic_input_causal"] is True


def test_dynamic_blockage_missing_observation_fails_closed():
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("build_pudo_evidence_dynamic_missing", Path("scripts/build_pudo_evidence.py"))
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)

    ev = mod._blockage_from_agents(0.0, 0.0, {"agent_history": []})
    assert ev["blockage_risk"] == 1.0
    assert ev["dynamic_confidence"] == 0.0
    assert ev["future_blockage_rate_label"] is None
