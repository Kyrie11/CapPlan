from dataclasses import replace

from capplan.data.accessibility_layer import attach_pudo_nodes_to_graph, shortest_accessible_path_stats, synthetic_accessibility_graph
from capplan.data.pudo_interface_layer import synthetic_pudo_anchors


def test_synthetic_overlay_connector_inherits_proxy_accessibility_for_nuplan_pudo():
    graph = synthetic_accessibility_graph("proxy", origin=None, destination=None)
    # Make a nuPlan-route PUDO near the synthetic spine but without audited curbside
    # path attributes. In synthetic overlay mode, connector evidence should be an
    # explicit proxy rather than all-missing.
    base = synthetic_pudo_anchors("proxy", n=1, graph=graph)[0]
    anchor = replace(
        base,
        anchor_id="nuplan_pickup_0",
        adjacent_ped_node_id="nuplan_pickup_0",
        source="nuplan_route_map_walkway_width_proxy",
        lighting=None,
        shelter=None,
    )

    graph, anchors = attach_pudo_nodes_to_graph(graph, [anchor])
    connector = next(e for e in graph.edges if e.to_node == "nuplan_pickup_0" or e.from_node == "nuplan_pickup_0")

    assert connector.source == "synthetic_accessibility_proxy_to_nuplan_pudo"
    assert connector.width_m is not None
    assert connector.slope is not None
    assert connector.cross_slope is not None
    assert connector.curb_ramp is not None
    assert connector.step_free is not None
    assert anchors[0].lighting is not None
    stats = shortest_accessible_path_stats(graph, "origin", "nuplan_pickup_0")
    assert "slope" not in stats["missing_fields"]
    assert "cross_slope" not in stats["missing_fields"]
    assert "path_width_m" not in stats["missing_fields"]


def test_real_graph_connector_stays_fail_closed_for_nuplan_pudo():
    graph = synthetic_accessibility_graph("realish", origin=None, destination=None)
    graph.metadata["source"] = "geojson"
    base = synthetic_pudo_anchors("realish", n=1, graph=graph)[0]
    anchor = replace(
        base,
        anchor_id="nuplan_pickup_0",
        adjacent_ped_node_id="nuplan_pickup_0",
        source="nuplan_route_map_walkway_width_proxy",
        lighting=None,
        shelter=None,
    )

    graph, _ = attach_pudo_nodes_to_graph(graph, [anchor])
    connector = next(e for e in graph.edges if e.to_node == "nuplan_pickup_0" or e.from_node == "nuplan_pickup_0")

    assert connector.source == "nuplan_route_map_walkway_width_proxy"
    assert connector.slope is None
    assert connector.cross_slope is None
    assert connector.curb_ramp is None
    stats = shortest_accessible_path_stats(graph, "origin", "nuplan_pickup_0")
    assert "slope" in stats["missing_fields"]
