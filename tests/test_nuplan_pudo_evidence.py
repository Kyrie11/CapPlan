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
    assert "no_auditable_interface_evidence" in status

    audited = dict(base, curb_inventory_source="manual_interface_audit")
    complete, eligible, status = mod._paper_evidence_flags(audited)
    assert complete and eligible and status == "paper_ready"
