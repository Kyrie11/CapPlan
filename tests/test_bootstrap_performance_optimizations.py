from __future__ import annotations

import importlib.util
import math
from pathlib import Path

from capplan.data.schemas import AccessibilityEdge, AccessibilityGraph, AccessibilityNode


def _load_script(name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, Path(path))
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_train_city_db_dir_resolution_does_not_rescan_all_city_dirs():
    mod = _load_script("prepare_external_perf", "scripts/prepare_abilitybench_external.py")
    split = {
        "db_dirs": ["train_boston", "train_pittsburgh", "train_vegas", "train_singapore"],
        "db_dirs_by_city": {"boston": ["train_boston"], "pittsburgh": ["train_pittsburgh"]},
    }
    assert mod._resolve_city_db_dirs(split, {}, "boston") == ["train_boston"]
    assert mod._resolve_city_db_dirs(split, {}, "pittsburgh") == ["train_pittsburgh"]
    # Backward-compatible filename inference still avoids the four-way scan.
    no_mapping = {"db_dirs": split["db_dirs"]}
    assert mod._resolve_city_db_dirs(no_mapping, {}, "vegas") == ["train_vegas"]


def test_shared_val_db_dir_remains_shared():
    mod = _load_script("prepare_external_perf_val", "scripts/prepare_abilitybench_external.py")
    assert mod._resolve_city_db_dirs({"db_dirs": ["val"]}, {}, "boston") == ["val"]


def test_blockage_is_timestep_occupancy_not_detection_count():
    mod = _load_script("pudo_perf_blockage", "scripts/build_pudo_evidence.py")
    scene = {
        "agent_history": [
            {"objects": [{"x": 0.0, "y": 0.0}, {"x": 0.1, "y": 0.1}]},
            {"objects": [{"x": 50.0, "y": 50.0}]},
            {"objects": [{"x": 0.0, "y": 0.0}]},
            {"objects": []},
        ]
    }
    assert mod._blockage_from_agents(0.0, 0.0, scene, radius=1.0) == 0.5


def test_fallback_candidates_are_spatially_thinned_and_capped():
    mod = _load_script("pudo_perf_candidates", "scripts/build_pudo_evidence.py")
    nodes = [AccessibilityNode(f"n{i}", float(i), 0.0, "sidewalk", source="official") for i in range(100)]
    graph = AccessibilityGraph("ep", nodes, [], {"node_attributes": {}})
    selected = mod._candidate_nodes(graph, [[0.0, 0.0], [100.0, 0.0]], 10.0, max_fallback=8, fallback_spacing_m=5.0)
    assert 1 <= len(selected) <= 8
    assert all(item[2] == "fallback" for item in selected)


def test_explicit_curb_candidates_are_not_capped():
    mod = _load_script("pudo_perf_explicit", "scripts/build_pudo_evidence.py")
    nodes = [AccessibilityNode(f"c{i}", float(i), 0.0, "curb_ramp", source="official") for i in range(20)]
    graph = AccessibilityGraph("ep", nodes, [], {"node_attributes": {}})
    selected = mod._candidate_nodes(graph, [[0.0, 0.0], [30.0, 0.0]], 10.0, max_fallback=2, fallback_spacing_m=10.0)
    assert len(selected) == 20
    assert all(item[2] == "explicit" for item in selected)


def test_vectorized_graph_index_matches_bruteforce_nearest_geometry():
    mod = _load_script("pudo_perf_index", "scripts/build_pudo_evidence.py")
    nodes = [
        AccessibilityNode("a", 0.0, 0.0, "sidewalk", source="official"),
        AccessibilityNode("b", 10.0, 0.0, "entrance", source="official"),
        AccessibilityNode("c", 10.0, 10.0, "curb_ramp", source="official"),
    ]
    edges = [
        AccessibilityEdge("ab", "a", "b", 10.0, width_m=1.4, source="official"),
        AccessibilityEdge("bc", "b", "c", 10.0, width_m=2.0, source="official"),
    ]
    graph = AccessibilityGraph("ep", nodes, edges, {})
    index = mod.GraphSpatialIndex(graph)
    fast_node, fast_dist = index.nearest_node(9.0, 1.0, {"sidewalk", "entrance"})
    slow_node, slow_dist = mod._nearest_node(9.0, 1.0, nodes, {"sidewalk", "entrance"})
    assert fast_node.node_id == slow_node.node_id
    assert math.isclose(fast_dist, slow_dist, rel_tol=1e-12)
    fast_edge = index.nearest_edge_attrs(5.0, 2.0)
    slow_edge = mod._nearest_edge_attrs(5.0, 2.0, graph)
    assert math.isclose(fast_edge["distance_to_ped_edge_m"], slow_edge["distance_to_ped_edge_m"], rel_tol=1e-12)
    assert fast_edge["sidewalk_width_m"] == slow_edge["sidewalk_width_m"]


def test_gis_crop_keeps_line_crossing_bbox_with_endpoints_outside():
    from capplan.data.gis_fusion import _geometry_intersects_bbox
    assert _geometry_intersects_bbox([[-10.0, 0.0], [10.0, 0.0]], (-1.0, -1.0, 1.0, 1.0))
    assert not _geometry_intersects_bbox([[-10.0, 5.0], [10.0, 5.0]], (-1.0, -1.0, 1.0, 1.0))
