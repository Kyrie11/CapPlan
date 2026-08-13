import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from capplan.data.external_validation import inspect_source
from capplan.data.nuplan_adapter import safe_call


def test_gpkg_extent_is_transformed_to_local_crs(tmp_path):
    from scripts.validate_georeference_alignment import gpkg_feature_bounds

    gpkg = tmp_path / "map.gpkg"
    conn = sqlite3.connect(gpkg)
    conn.execute("CREATE TABLE gpkg_contents (table_name TEXT, data_type TEXT, identifier TEXT, description TEXT, last_change TEXT, min_x REAL, min_y REAL, max_x REAL, max_y REAL, srs_id INTEGER)")
    conn.execute("CREATE TABLE gpkg_spatial_ref_sys (srs_name TEXT, srs_id INTEGER PRIMARY KEY, organization TEXT, organization_coordsys_id INTEGER, definition TEXT, description TEXT)")
    conn.execute("INSERT INTO gpkg_spatial_ref_sys VALUES ('WGS 84',4326,'EPSG',4326,'undefined','')")
    conn.execute("INSERT INTO gpkg_contents VALUES ('lane','features','','','',-71.06,42.33,-71.02,42.36,4326)")
    conn.commit(); conn.close()

    b = gpkg_feature_bounds(gpkg, "EPSG:32619")
    assert b[0] > 300_000 and b[2] < 400_000
    assert b[1] > 4_000_000


def test_osm_semantic_check_scans_past_first_500_features(tmp_path):
    p = tmp_path / "osm.geojson"
    features = [
        {"type": "Feature", "geometry": {"type": "Point", "coordinates": [-115.17, 36.10]}, "properties": {"kind": "kerb"}}
        for _ in range(600)
    ]
    features.append({
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": [[-115.17, 36.10], [-115.16, 36.10]]},
        "properties": {"kind": "sidewalk", "highway": "footway", "footway": "sidewalk"},
    })
    p.write_text(json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8")
    report = inspect_source(p, role="osm")
    assert report.valid
    assert report.role_stats["routable_pedestrian_lines"] == 1
    assert report.role_stats["sampled_records"] == 601


def test_dem_candidate_paths_include_prepared_geojson(tmp_path):
    from scripts.sample_dem_elevation_jsonl import _iter_candidate_paths

    osm = tmp_path / "osm"
    osm.mkdir(parents=True)
    f = osm / "singapore_sidewalks.geojson"
    f.write_text('{"type":"FeatureCollection","features":[]}', encoding="utf-8")
    assert f in list(_iter_candidate_paths(tmp_path, "singapore", False))


def test_safe_call_handles_property_that_raises():
    class BadProperty:
        @property
        def map_api(self):
            raise RuntimeError("missing maps db")

    assert safe_call(BadProperty(), ["map_api"], "fallback") == "fallback"


def test_map_crs_requires_official_manifest_layout(tmp_path):
    from scripts.inspect_nuplan_map_crs import find_gpkg, load_map_manifest

    manifest = {"us-ma-boston": {"version": "9.12.1817"}}
    (tmp_path / "nuplan-maps-v1.0.json").write_text(json.dumps(manifest), encoding="utf-8")
    good = tmp_path / "us-ma-boston" / "9.12.1817" / "map.gpkg"
    good.parent.mkdir(parents=True)
    good.write_bytes(b"x")
    loaded = load_map_manifest(tmp_path, "nuplan-maps-v1.0")
    assert find_gpkg(tmp_path, "nuplan-maps-v1.0", "us-ma-boston", loaded) == good


def test_service_layer_counterfactual_requests_share_od_and_time(tmp_path):
    graph_dir = tmp_path / "graphs"
    graph_dir.mkdir()
    nodes = [
        {"node_id": "a", "x": 0.0, "y": 0.0, "kind": "entrance", "confidence": 1.0, "source": "audited_entrance", "pose": {"x": 0.0, "y": 0.0, "heading": 0.0, "frame": "map"}},
        {"node_id": "b", "x": 100.0, "y": 0.0, "kind": "entrance", "confidence": 1.0, "source": "audited_entrance", "pose": {"x": 100.0, "y": 0.0, "heading": 0.0, "frame": "map"}},
        {"node_id": "c", "x": 0.0, "y": 100.0, "kind": "entrance", "confidence": 1.0, "source": "audited_entrance", "pose": {"x": 0.0, "y": 100.0, "heading": 0.0, "frame": "map"}},
    ]
    (graph_dir / "ep.nodes.jsonl").write_text("\n".join(json.dumps(x) for x in nodes) + "\n", encoding="utf-8")
    req = tmp_path / "requests.jsonl"
    prof = tmp_path / "profiles.jsonl"
    subprocess.check_call([
        sys.executable, "scripts/build_service_layer.py",
        "--accessibility_graph_dir", str(graph_dir),
        "--output_service_requests_jsonl", str(req),
        "--output_capability_profiles_jsonl", str(prof),
        "--num_requests_per_episode", "3",
    ])
    rows = [json.loads(x) for x in req.read_text(encoding="utf-8").splitlines() if x.strip()]
    assert len({r["passenger_profile_id"] for r in rows}) == 3
    assert len({(r["origin_entrance_id"], r["destination_entrance_id"], r["request_time_s"]) for r in rows}) == 1
    assert len({r["counterfactual_group_id"] for r in rows}) == 1
    assert sum(r["counterfactual_role"] == "base" for r in rows) == 1


def test_arcgis_long_query_uses_post(monkeypatch):
    import scripts.download_arcgis_layer as mod

    seen = {}

    class Response:
        headers = {"Content-Type": "application/json"}
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): return b'{"type":"FeatureCollection","features":[]}'

    def fake_urlopen(req, timeout=0):
        seen["method"] = req.get_method()
        seen["data"] = req.data
        seen["url"] = req.full_url
        return Response()

    monkeypatch.setattr(mod.urllib.request, "urlopen", fake_urlopen)
    payload = mod.request_json("https://example.test/FeatureServer/0/query", {"objectIds": ",".join(str(i) for i in range(1000)), "f": "geojson"}, retries=1)
    assert payload["type"] == "FeatureCollection"
    assert seen["method"] == "POST"
    assert seen["data"] is not None
    assert "objectIds" in seen["data"].decode("utf-8")


def test_lta_footpath_output_is_real_geojson(tmp_path):
    src = tmp_path / "footpath.geojson"
    src.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [[103.77, 1.29], [103.78, 1.29]]},
            "properties": {"OBJECTID": 1},
        }],
    }), encoding="utf-8")
    out = tmp_path / "normalized.geojson"
    subprocess.check_call([
        sys.executable, "scripts/normalize_accessibility_evidence.py",
        "--input", str(src), "--output", str(out),
        "--profile", "lta_footpath", "--source", "LTA Footpath",
    ])
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["type"] == "FeatureCollection"
    assert payload["features"][0]["properties"]["kind"] == "sidewalk"


def test_nuplan_motion_labels_are_derived_from_ego_history():
    from scripts.build_dataset import _motion_fields_from_ego_history
    rows = [
        {"t": 0.0, "v": 5.0, "a": 0.0, "heading": 0.0},
        {"t": 1.0, "v": 6.0, "a": 1.0, "heading": 0.1},
        {"t": 2.0, "v": 6.0, "a": 0.0, "heading": 0.1},
    ]
    out = _motion_fields_from_ego_history(rows)
    assert out["peak_accel_mps2"] == 1.0
    assert out["peak_jerk_mps3"] == 1.0
    assert out["motion_exposure"] > 0.0
    assert out["ride_motion_evidence_source"] == "nuplan_ego_history"


def test_counterfactual_pairs_carry_axis_and_group_metadata():
    from types import SimpleNamespace
    from scripts.build_dataset import _counterfactual_pairs_from_service_requests

    reqs = [
        {"request_id": "r0", "episode_id": "ep", "origin_entrance_id": "o", "destination_entrance_id": "d", "request_time_s": 10.0,
         "passenger_profile_id": "base", "counterfactual_group_id": "ep:cf", "counterfactual_role": "base", "counterfactual_axis": "base"},
        {"request_id": "r1", "episode_id": "ep", "origin_entrance_id": "o", "destination_entrance_id": "d", "request_time_s": 10.0,
         "passenger_profile_id": "strict", "counterfactual_group_id": "ep:cf", "counterfactual_role": "variant",
         "counterfactual_axis": "min_width", "counterfactual_relation": "stricter_or_equal", "expected_monotonic": True},
    ]
    contracts = [
        SimpleNamespace(passenger_id="ep:p0", metadata={"request_id": "r0"}),
        SimpleNamespace(passenger_id="ep:p1", metadata={"request_id": "r1"}),
    ]
    pairs = _counterfactual_pairs_from_service_requests("ep", reqs, contracts)
    assert len(pairs) == 1
    assert pairs[0].counterfactual_axis == "min_width"
    assert pairs[0].counterfactual_group_id == "ep:cf"
    assert pairs[0].weak_profile_id == "base"
    assert pairs[0].strict_profile_id == "strict"


def test_paper_mode_rejects_example_fleet():
    from argparse import Namespace
    import pytest
    from capplan.data.schemas import VehicleInterface
    from scripts.build_dataset import _enforce_paper_vehicle_quality

    v = VehicleInterface("v", "ep", metadata={"source": "abilitybench_example_fleet"})
    with pytest.raises(RuntimeError, match="example/synthetic/unverified"):
        _enforce_paper_vehicle_quality(Namespace(paper_mode=True), "ep", [v])


def test_pudo_audit_flags_survive_evidence_materialization():
    from scripts.build_dataset import _pudo_anchors_from_evidence_rows

    row = {
        "episode_id": "ep",
        "anchor_id": "p0",
        "curb_pose": {"x": 1.0, "y": 2.0, "heading": 0.0, "frame": "map"},
        "legal_stop": True,
        "legal_stop_source": "official_curb_regulation",
        "adjacent_ped_node_id": "ped0",
        "curb_height_m": 0.10,
        "sidewalk_width_m": 1.8,
        "deployment_clearance_m": 1.5,
        "source": "audited_city_curb_inventory",
        "paper_evidence_complete": True,
        "paper_eligible": True,
        "evidence_status": "paper_ready",
        "evidence_notes": "manual audit",
    }
    anchors = _pudo_anchors_from_evidence_rows({("ep", "p0"): row}, "ep")
    assert len(anchors) == 1
    assert anchors[0].paper_evidence_complete is True
    assert anchors[0].paper_eligible is True
    assert anchors[0].evidence_status == "paper_ready"


def test_paper_mode_does_not_accept_default_filled_vehicle_fields():
    from argparse import Namespace
    import pytest
    from capplan.data.schemas import VehicleInterface
    from scripts.build_dataset import _enforce_paper_vehicle_quality

    # Source provenance alone is insufficient: omitted interface fields would be
    # filled by dataclass defaults, which must not be treated as measurements.
    v = VehicleInterface("v", "ep", metadata={"source": "manufacturer_datasheet"})
    with pytest.raises(RuntimeError, match="explicitly present"):
        _enforce_paper_vehicle_quality(Namespace(paper_mode=True), "ep", [v])


def test_real_nuplan_split_is_preserved_without_resplitting(tmp_path):
    from scripts.build_dataset import _write_splits

    episodes = [
        {"episode_id": "e0", "log_name": "log0"},
        {"episode_id": "e1", "log_name": "log1"},
    ]
    _write_splits(tmp_path, episodes, dataset_split="val", preserve_official_split=True)
    assert (tmp_path / "splits" / "train_episodes.txt").read_text() == ""
    assert (tmp_path / "splits" / "val_episodes.txt").read_text().splitlines() == ["e0", "e1"]
    assert (tmp_path / "splits" / "test_episodes.txt").read_text() == ""
    manifest = json.loads((tmp_path / "splits" / "split_manifest.json").read_text())
    assert manifest["policy"] == "preserve_upstream_nuplan_db_split"
    assert manifest["upstream_split"] == "val"


def test_counterfactual_audit_requires_all_axes_per_episode(tmp_path):
    from scripts.audit_dataset_quality import audit_dataset

    (tmp_path / "episodes.jsonl").write_text(
        json.dumps({"episode_id": "e0"}) + "\n" + json.dumps({"episode_id": "e1"}) + "\n",
        encoding="utf-8",
    )
    required = ["access_distance", "step_free", "min_width", "ramp_lift", "door_side_clearance", "ride_motion", "confidence"]
    rows = []
    # e0 has all seven axes; e1 has seven pairs but repeats one axis. A count-only
    # audit would incorrectly pass e1.
    for i, axis in enumerate(required):
        rows.append({"pair_id": f"e0:{i}", "episode_id": "e0", "counterfactual_axis": axis})
    for i in range(7):
        rows.append({"pair_id": f"e1:{i}", "episode_id": "e1", "counterfactual_axis": "access_distance"})
    (tmp_path / "counterfactual_pairs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    report = audit_dataset(tmp_path, paper_mode=True)
    assert report["counterfactual_audit"]["episodes_with_full_counterfactual_set"] == 1
    assert "e1" in report["counterfactual_audit"]["missing_axes_by_episode"]
    assert "paper_mode_counterfactual_episode_axes_incomplete" in report["publication_readiness"]["issues"]


def test_bootstrap_audit_does_not_fail_only_because_it_is_not_paper(tmp_path):
    from scripts.audit_dataset_quality import audit_dataset

    (tmp_path / "dataset_manifest.json").write_text(json.dumps({"source_policy": "bootstrap", "paper_mode": False}), encoding="utf-8")
    report = audit_dataset(tmp_path, paper_mode=False)
    issues = set(report["publication_readiness"]["issues"])
    assert "source_policy_not_paper" not in issues
    assert "dataset_not_built_in_paper_mode" not in issues
