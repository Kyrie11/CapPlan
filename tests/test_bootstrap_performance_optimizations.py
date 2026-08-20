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


def test_polyline_distance_index_matches_scalar_random_points():
    import numpy as np
    from capplan.data.gis_fusion import distance_to_polyline
    mod = _load_script("pudo_perf_route_index", "scripts/build_pudo_evidence.py")
    route = [[0.0, 0.0], [13.0, 7.0], [25.0, -4.0], [40.0, 12.0]]
    rng = np.random.default_rng(7)
    pts = rng.normal(size=(200, 2)) * 25.0
    fast = mod.PolylineDistanceIndex(route).distances(pts)
    slow = np.asarray([distance_to_polyline(p, route) for p in pts])
    assert np.allclose(fast, slow, rtol=1e-12, atol=1e-12)


def test_grid_nearest_edge_matches_bruteforce_random_queries():
    import numpy as np
    mod = _load_script("pudo_perf_edge_grid", "scripts/build_pudo_evidence.py")
    nodes = []
    edges = []
    # A mixture of short local edges and a long edge exercises both grid and
    # global-long-edge paths.
    for i in range(60):
        nodes.append(AccessibilityNode(f"n{i}", float(i * 7), float((i % 5) * 9), "sidewalk", source="official"))
    for i in range(59):
        edges.append(AccessibilityEdge(f"e{i}", f"n{i}", f"n{i+1}", 10.0, width_m=1.0 + i / 100.0, source="official"))
    nodes += [
        AccessibilityNode("long_a", -200.0, 75.0, "sidewalk", source="official"),
        AccessibilityNode("long_b", 800.0, 75.0, "sidewalk", source="official"),
    ]
    edges.append(AccessibilityEdge("long", "long_a", "long_b", 1000.0, width_m=3.0, source="official"))
    graph = AccessibilityGraph("ep", nodes, edges, {})
    index = mod.GraphSpatialIndex(graph, cell_size_m=25.0)
    rng = np.random.default_rng(11)
    for x, y in rng.uniform([-250, -100], [850, 180], size=(150, 2)):
        fast = index.nearest_edge_attrs(float(x), float(y))
        slow = mod._nearest_edge_attrs(float(x), float(y), graph)
        assert math.isclose(fast["distance_to_ped_edge_m"], slow["distance_to_ped_edge_m"], rel_tol=1e-10, abs_tol=1e-10)
        assert fast["sidewalk_width_m"] == slow["sidewalk_width_m"]


def test_nearest_node_within_matches_global_nearest_when_accepted():
    mod = _load_script("pudo_perf_node_grid", "scripts/build_pudo_evidence.py")
    nodes = [
        AccessibilityNode("a", 0.0, 0.0, "sidewalk", source="official"),
        AccessibilityNode("b", 8.0, 3.0, "entrance", source="official"),
        AccessibilityNode("c", 30.0, 0.0, "curb", source="official"),
    ]
    graph = AccessibilityGraph("ep", nodes, [], {})
    index = mod.GraphSpatialIndex(graph, cell_size_m=10.0)
    fast, fd = index.nearest_node_within(7.5, 2.5, {"sidewalk", "entrance"}, 10.0)
    slow, sd = mod._nearest_node(7.5, 2.5, nodes, {"sidewalk", "entrance"})
    assert fast is not None and slow is not None
    assert fast.node_id == slow.node_id
    assert math.isclose(fd, sd, rel_tol=1e-12)
    rejected, rd = index.nearest_node_within(100.0, 100.0, {"sidewalk", "entrance"}, 5.0)
    assert rejected is None and math.isinf(rd)


def test_temporal_occupancy_batch_matches_scalar_metric():
    import numpy as np
    mod = _load_script("pudo_perf_occupancy", "scripts/build_pudo_evidence.py")
    scene = {
        "agent_history": [
            {"objects": [{"x": 0.0, "y": 0.0}, {"x": 20.0, "y": 0.0}]},
            {"objects": [{"x": 1.0, "y": 0.0}]},
            {"objects": []},
            {"objects": [{"x": 20.0, "y": 1.0}]},
        ]
    }
    points = [(0.0, 0.0), (20.0, 0.0), (100.0, 100.0)]
    fast = mod.TemporalOccupancyIndex(scene).query_many(points, radius=2.0)
    slow = np.asarray([mod._blockage_from_agents(x, y, scene, radius=2.0) for x, y in points])
    assert np.allclose(fast, slow, rtol=0.0, atol=0.0)


def test_route_chunk_crop_is_conservative_but_removes_global_bbox_corner():
    from capplan.data.gis_fusion import GISFeature, GISFeatureSpatialIndex, route_corridor_boxes
    # L-shaped route. The global bbox corner around (100, 100) is far from the
    # route, while points/segments within radius 10 must survive.
    route = [[0.0, 0.0], [100.0, 0.0], [100.0, 100.0]]
    boxes = route_corridor_boxes(route, radius_m=10.0, chunk_length_m=30.0)
    features = [
        GISFeature("near_h", "sidewalk", [[40.0, 8.0], [60.0, 8.0]], source="official"),
        GISFeature("near_v", "sidewalk", [[92.0, 40.0], [92.0, 60.0]], source="official"),
        GISFeature("cross", "sidewalk", [[20.0, -20.0], [20.0, 20.0]], source="official"),
        GISFeature("far_corner", "sidewalk", [[10.0, 90.0], [20.0, 90.0]], source="official"),
    ]
    kept = {f.feature_id for f in GISFeatureSpatialIndex(features).query_many(boxes)}
    assert {"near_h", "near_v", "cross"}.issubset(kept)
    assert "far_corner" not in kept


def test_compact_jsonl_roundtrip_preserves_values(tmp_path):
    from capplan.utils.serialization import read_jsonl, write_jsonl
    rows = [{"z": 2, "a": [1, {"x": True}]}, {"unicode": "坡道", "v": 1.25}]
    path = tmp_path / "rows.jsonl"
    write_jsonl(path, rows)
    assert read_jsonl(path) == rows


def test_resume_build_fingerprint_changes_when_input_file_changes(tmp_path):
    from capplan.utils.build_fingerprint import fingerprint
    p = tmp_path / "input.jsonl"
    p.write_text("a\n", encoding="utf-8")
    a = fingerprint({"version": 1}, [p])
    p.write_text("different-size\n", encoding="utf-8")
    b = fingerprint({"version": 1}, [p])
    assert a != b


def test_graph_resume_rejects_old_or_mismatched_build_fingerprint(tmp_path):
    import json
    mod = _load_script("graph_perf_resume_fp", "scripts/build_accessibility_graphs.py")
    eid = "ep"
    for suffix in ["nodes.jsonl", "edges.jsonl", "meta.json"]:
        (tmp_path / f"{eid}.{suffix}").write_text("{}\n", encoding="utf-8")
    marker = tmp_path / f"{eid}.build.json"
    marker.write_text(json.dumps({"status": "PASS", "build_version": mod.GRAPH_BUILD_VERSION, "build_fingerprint": "a"}), encoding="utf-8")
    assert mod._resume_graph_stats(tmp_path, eid, True, "a") is not None
    assert mod._resume_graph_stats(tmp_path, eid, True, "b") is None
    marker.write_text(json.dumps({"status": "PASS"}), encoding="utf-8")
    assert mod._resume_graph_stats(tmp_path, eid, True, "a") is None


def test_runtime_profile_summary_excludes_idle_tail(tmp_path):
    mod = _load_script("runtime_profile_summary", "scripts/summarize_bootstrap_runtime_profile.py")
    log = tmp_path / "runtime.log"
    log.write_text("""# start\n===== SAMPLE 2026-08-20T00:00:00+08:00 =====\n--- CapPlan processes ---\n    PID PPID PSR %CPU %MEM RSS ELAPSED STAT CMD\n123 1 2 101.0 0.1 1000 00:10 R python scripts/build_pudo_evidence.py --output_pudo_evidence_jsonl /x/pudo/boston.jsonl\n--- vmstat ---\n1 0 0 0 0 0 0 0 0 0 0 0 90 1 9 0 0\n===== SAMPLE 2026-08-20T00:00:30+08:00 =====\n--- CapPlan processes ---\n--- vmstat ---\n1 0 0 0 0 0 0 0 0 0 0 0 10 1 89 0 0\n===== SAMPLE 2026-08-20T01:00:00+08:00 =====\n--- CapPlan processes ---\n--- vmstat ---\n1 0 0 0 0 0 0 0 0 0 0 0 10 1 89 0 0\n""", encoding="utf-8")
    summary = mod.summarize(log)
    assert summary["samples_total"] == 3
    assert summary["samples_active_direct_stage"] == 1
    assert summary["samples_excluded_idle_or_orchestrator_only"] == 2
    assert summary["stages"]["boston"]["pudo"]["process_cpu_percent"]["mean"] == 101.0


def test_nuplan_route_geometry_cache_preserves_points_and_avoids_repeat_lookup():
    from capplan.data.nuplan_adapter import NuPlanAdapter
    from capplan.data.schemas import Pose2D

    class Pt:
        def __init__(self, x, y): self.x, self.y = x, y
    class Baseline:
        def __init__(self, pts): self.discrete_path = [Pt(*p) for p in pts]
    class Obj:
        def __init__(self, pts): self.baseline_path = Baseline(pts)
    class Map:
        map_name = "test-map"
        def __init__(self): self.calls = 0
        def get_map_object(self, object_id, layer=None):
            self.calls += 1
            return Obj([(0.0, 0.0), (10.0, 0.0)]) if object_id == "r0" else None

    adapter = NuPlanAdapter(scene_source="synthetic")
    api = Map()
    pose = Pose2D(0.0, 0.0, 0.0, "map")
    first = adapter._extract_route_polyline(["r0"], api, pose, None)
    calls_after_first = api.calls
    second = adapter._extract_route_polyline(["r0"], api, pose, None)
    assert first == second == [[0.0, 0.0], [10.0, 0.0]]
    assert api.calls == calls_after_first
    assert adapter.route_geometry_cache_stats["hits"] >= 1


def test_graph_builder_parallel_episode_workers_produce_all_outputs(tmp_path):
    import json
    import subprocess
    import sys
    georef = tmp_path / "geo.json"
    georef.write_text(json.dumps({"origin_lat": 42.0, "origin_lon": -71.0}), encoding="utf-8")
    scene_dir = tmp_path / "scene"; scene_dir.mkdir()
    rows = [
        {"episode_id": "ep0", "map_name": "test_map", "route_corridor": {"polyline": [[0, 0], [120, 0]]}},
        {"episode_id": "ep1", "map_name": "test_map", "route_corridor": {"polyline": [[0, 0], [120, 0]]}},
    ]
    (scene_dir / "scenes.jsonl").write_text("\n".join(json.dumps(x) for x in rows) + "\n", encoding="utf-8")
    osm = tmp_path / "osm.json"
    osm.write_text(json.dumps({"elements": [
        {"type": "node", "id": 1, "lat": 42.0, "lon": -71.0},
        {"type": "node", "id": 2, "lat": 42.0, "lon": -70.999, "tags": {"kerb": "lowered", "source": "osm_survey"}},
        {"type": "way", "id": 10, "nodes": [1, 2], "tags": {"highway": "footway", "footway": "sidewalk", "width": "1.5", "source": "osm_survey"}},
        {"type": "node", "id": 3, "lat": 42.00002, "lon": -71.0, "tags": {"entrance": "main", "source": "city_building_entrances"}},
    ]}), encoding="utf-8")
    out = tmp_path / "graphs"
    subprocess.check_call([
        sys.executable, "scripts/build_accessibility_graphs.py",
        "--scene_dataset_dir", str(scene_dir), "--georeference_json", str(georef),
        "--osm_source", str(osm), "--output_graph_dir", str(out),
        "--min_nodes_per_episode", "2", "--min_edges_per_episode", "1",
        "--compact_storage", "--num_workers", "2", "--disable_tqdm",
    ])
    assert (out / "ep0.nodes.jsonl").exists() and (out / "ep1.nodes.jsonl").exists()
    for eid in ["ep0", "ep1"]:
        marker = json.loads((out / f"{eid}.build.json").read_text(encoding="utf-8"))
        assert marker["status"] == "PASS"
        assert marker["build_fingerprint"]


def test_nuplan_extraction_resume_skips_devkit_when_fingerprint_matches(tmp_path):
    import json
    import subprocess
    import sys
    from capplan.utils.build_fingerprint import fingerprint

    db_root = tmp_path / "dbroot"; db_root.mkdir()
    db_dir = db_root / "train_boston"; db_dir.mkdir()
    (db_dir / "one.db").write_bytes(b"sqlite-placeholder")
    map_root = tmp_path / "maps"; map_root.mkdir()
    (map_root / "nuplan-maps-v1.0.json").write_text("{}", encoding="utf-8")
    out = tmp_path / "scene"; out.mkdir()
    (out / "scenes.jsonl").write_text("{}\n", encoding="utf-8")
    (out / "episodes.jsonl").write_text("{}\n", encoding="utf-8")
    mod = _load_script("extract_resume_module", "scripts/extract_nuplan_scenes.py")
    fp = fingerprint({
        "version": mod.NUPLAN_EXTRACT_VERSION,
        "split": "train", "max_scenarios": 100, "seed": 13,
        "map_version": "nuplan-maps-v1.0", "map_names": "us-ma-boston",
        "scenario_types": None, "log_names": None,
    }, [db_dir, map_root])
    (out / "scene_context_manifest.json").write_text(json.dumps({
        "status": "PASS", "extract_fingerprint": fp, "num_scenes": 1,
        "scenes_sha256": "stable",
    }), encoding="utf-8")
    proc = subprocess.run([
        sys.executable, "scripts/extract_nuplan_scenes.py",
        "--nuplan_data_root", str(tmp_path), "--nuplan_map_root", str(map_root),
        "--nuplan_db_root", str(db_root), "--nuplan_db_dirs", "train_boston",
        "--nuplan_map_version", "nuplan-maps-v1.0", "--nuplan_map_names", "us-ma-boston",
        "--split", "train", "--max_scenarios", "100", "--seed", "13",
        "--output_dir", str(out), "--resume", "--disable_tqdm",
    ], capture_output=True, text=True, check=True)
    assert '"resumed": true' in proc.stdout.lower()


def test_mixed_split_uses_fresh_inspection_to_select_only_city_db_files(tmp_path):
    import json
    from capplan.utils.build_fingerprint import file_inventory_fingerprint
    mod = _load_script("prepare_external_city_db_inspection", "scripts/prepare_abilitybench_external.py")
    db_root = tmp_path / "nuplan" / "nuplan-v1.1" / "splits"
    val = db_root / "val"
    val.mkdir(parents=True)
    boston = val / "boston.db"; boston.write_bytes(b"boston")
    vegas = val / "vegas.db"; vegas.write_bytes(b"vegas")
    external = tmp_path / "external"
    reports = external / "reports"; reports.mkdir(parents=True)
    report = {
        "status": "PASS", "split": "val", "db_dirs": ["val"],
        "db_inventory_fingerprint": file_inventory_fingerprint([boston, vegas]),
        "dbs": [
            {"db": str(boston), "mapped_cities": ["boston"]},
            {"db": str(vegas), "mapped_cities": ["vegas"]},
        ],
    }
    (reports / "nuplan_db_cities.val.json").write_text(json.dumps(report), encoding="utf-8")
    selected, reason = mod._trusted_city_db_files_from_inspection(
        split_name="val", city="boston",
        split_cfg={"db_dirs": ["val"], "cities": ["boston", "vegas"]},
        db_root=db_root, external_root=external,
    )
    assert reason == "inspection_inventory_match"
    assert selected == [str(boston.resolve())]


def test_mixed_split_rejects_stale_inspection_inventory(tmp_path):
    import json
    from capplan.utils.build_fingerprint import file_inventory_fingerprint
    mod = _load_script("prepare_external_city_db_stale", "scripts/prepare_abilitybench_external.py")
    db_root = tmp_path / "splits"; val = db_root / "val"; val.mkdir(parents=True)
    boston = val / "boston.db"; boston.write_bytes(b"old")
    external = tmp_path / "external"; reports = external / "reports"; reports.mkdir(parents=True)
    report = {
        "status": "PASS", "split": "val", "db_dirs": ["val"],
        "db_inventory_fingerprint": file_inventory_fingerprint([boston]),
        "dbs": [{"db": str(boston), "mapped_cities": ["boston"]}],
    }
    (reports / "nuplan_db_cities.val.json").write_text(json.dumps(report), encoding="utf-8")
    # Any split inventory change invalidates the reused mapping and forces the
    # original safe map-name-filtering path.
    (val / "new.db").write_bytes(b"new")
    selected, reason = mod._trusted_city_db_files_from_inspection(
        split_name="val", city="boston",
        split_cfg={"db_dirs": ["val"], "cities": ["boston", "vegas"]},
        db_root=db_root, external_root=external,
    )
    assert selected is None
    assert reason == "inspection_inventory_changed"


def test_extract_db_manifest_resolves_concrete_files(tmp_path):
    import argparse
    mod = _load_script("extract_db_manifest", "scripts/extract_nuplan_scenes.py")
    root = tmp_path / "root"; root.mkdir()
    a = root / "a.db"; a.write_bytes(b"a")
    b = root / "b.db"; b.write_bytes(b"b")
    manifest = tmp_path / "dbs.txt"
    manifest.write_text(f"{a}\n{b}\n", encoding="utf-8")
    args = argparse.Namespace(
        nuplan_db_manifest=str(manifest), nuplan_db_files=None, nuplan_db_dirs=None,
        nuplan_db_root=str(root), nuplan_data_root=str(root), nuplan_root=None,
    )
    assert mod._resolve_db_inputs(args) == [str(a), str(b)]


def test_graph_fast_records_are_value_identical_to_schema_to_dict():
    from capplan.data.schemas import AccessibilityNode, AccessibilityEdge, Pose2D, to_dict
    mod = _load_script("graph_fast_record_serialization", "scripts/build_accessibility_graphs.py")
    node = AccessibilityNode("n", 1.25, -2.5, "sidewalk", confidence=0.9, timestamp_s=3.0, source="official", pose=Pose2D(1.25, -2.5, 0.3, "map"))
    edge = AccessibilityEdge("e", "n", "m", 4.5, width_m=1.7, slope=0.02, geometry=[[1.0, 2.0], [3.0, 4.0]], source="official")
    assert mod._graph_node_record(node) == to_dict(node)
    assert mod._graph_edge_record(edge) == to_dict(edge)
