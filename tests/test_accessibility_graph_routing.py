from capplan.data.accessibility_layer import shortest_accessible_path_stats
from capplan.data.schemas import AccessibilityGraph, AccessibilityNode, AccessibilityEdge


def test_path_stats_use_shortest_path_not_single_edge():
    g = AccessibilityGraph("e", [AccessibilityNode("a",0,0,"entrance"), AccessibilityNode("b",1,0,"sidewalk"), AccessibilityNode("c",2,0,"pudo")], [
        AccessibilityEdge("long", "a", "c", 10, 1.0, 0.01, 0.01, "paved", True, True),
        AccessibilityEdge("short1", "a", "b", 2, 1.0, 0.01, 0.01, "paved", True, True),
        AccessibilityEdge("short2", "b", "c", 2, 1.0, 0.01, 0.01, "paved", True, True),
    ])
    stats = shortest_accessible_path_stats(g, "a", "c")
    assert stats["distance"] == 4
    assert stats["path_edge_ids"] == ["short1", "short2"]


def test_missing_width_generates_missing_or_low_confidence_evidence():
    g = AccessibilityGraph("e", [AccessibilityNode("a",0,0,"entrance"), AccessibilityNode("c",1,0,"pudo")], [AccessibilityEdge("e", "a", "c", 1, None, 0.01, 0.01, "paved", True, True)])
    stats = shortest_accessible_path_stats(g, "a", "c")
    assert "path_width_m" in stats["missing_fields"]
    assert stats["confidence"] < 0.5


def test_partially_missing_path_evidence_has_null_supervised_value():
    from capplan.planning.transition_generator import TransitionGenerator

    g = AccessibilityGraph(
        "partial",
        [AccessibilityNode("a", 0, 0, "entrance"), AccessibilityNode("b", 1, 0, "sidewalk"), AccessibilityNode("c", 2, 0, "pudo")],
        [
            AccessibilityEdge("observed", "a", "b", 1, 1.2, 0.02, 0.01, "paved", True, True),
            AccessibilityEdge("missing_connector", "b", "c", 1, None, None, None, "unknown", None, None),
        ],
    )
    stats = shortest_accessible_path_stats(g, "a", "c")
    assert stats["width"] == 1.2
    assert "path_width_m" in stats["missing_fields"]
    ev = next(e for e in TransitionGenerator()._path_evidence("access", stats) if e.resource_name == "path_width_m")
    assert ev.missing is True
    assert ev.value is None
    assert ev.observed == 1.2
