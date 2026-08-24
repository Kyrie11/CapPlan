from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

from capplan.semantics.typed_resource_algebra import compatible

ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_pudo_missing_dynamic_state_stays_unknown_until_overlay():
    mod = _load_script("hybrid_pudo_realism_norm", "scripts/build_hybrid_pudo_evidence.py")
    row = mod._normalize_base({"anchor_id": "a", "episode_id": "ep", "blockage_risk": None})
    assert row["blockage_risk"] is None


def test_pudo_static_site_truth_is_stable_across_episode_and_split_and_sg_defaults_left():
    mod = _load_script("hybrid_pudo_realism_site", "scripts/build_hybrid_pudo_evidence.py")
    base = {
        "anchor_id": "a", "episode_id": "ep1", "x": 100.2, "y": 200.1,
        "side": "unknown", "adjacent_ped_node_id": "ped:10:20", "lane_id": "lane7",
        "curb_height_m": None, "sidewalk_width_m": None, "deployment_clearance_m": None,
        "curb_ramp": None, "legal_stop": None, "legal_basis": None, "blockage_risk": None,
    }
    site_key = mod._site_key(base, "singapore")
    seed = mod._seed(11, "singapore", site_key, "static")
    profile = mod._profile("singapore")
    site_class = mod._static_site_class(__import__("random").Random(seed))
    outputs = []
    for split, eid in [("train", "ep1"), ("test", "ep2")]:
        row = dict(base); row["episode_id"] = eid
        prov = {}
        mod._fill_missing(
            row, prov, city="singapore", split=split, site_class=site_class,
            site_seed=seed, dynamic_seed=mod._seed(11, "singapore", eid, site_key, "dynamic"),
            site_key=site_key, profile=profile,
        )
        outputs.append(row)
    static_fields = ["curb_height_m", "sidewalk_width_m", "deployment_clearance_m", "curb_ramp", "legal_stop", "legal_basis", "side", "lighting", "shelter"]
    assert {k: outputs[0][k] for k in static_fields} == {k: outputs[1][k] for k in static_fields}
    assert outputs[0]["side"] == "left"


def test_pudo_observed_ramp_constrains_simulated_curb_height():
    mod = _load_script("hybrid_pudo_realism_observed", "scripts/build_hybrid_pudo_evidence.py")
    row = {
        "anchor_id": "a", "episode_id": "ep", "x": 0.0, "y": 0.0,
        "side": "right", "adjacent_ped_node_id": "ped:0:0",
        "curb_height_m": None, "sidewalk_width_m": None, "deployment_clearance_m": None,
        "curb_ramp": True, "legal_stop": True, "legal_basis": "observed",
        "blockage_risk": 0.08,
    }
    key = mod._site_key(row, "boston")
    prov = {"curb_ramp": {"kind": "observed"}, "legal_stop": {"kind": "observed"}, "legal_basis": {"kind": "observed"}}
    mod._fill_missing(
        row, prov, city="boston", split="train", site_class="high_curb",
        site_seed=mod._seed(3, "boston", key, "static"), dynamic_seed=4,
        site_key=key, profile=mod._profile("boston"),
    )
    assert row["curb_ramp"] is True
    assert 0.0 <= row["curb_height_m"] <= 0.035


def test_accessibility_feature_truth_is_stable_and_ramp_stepfree_coherent():
    mod = _load_script("hybrid_graph_realism", "scripts/build_hybrid_accessibility_overlay.py")
    base = {
        "edge_id": "osm_way_123:4:5", "from_node": "ped:1:1", "to_node": "ped:2:2",
        "crossing_type": "curb_connector", "source": "official_gis",
        "width_m": None, "slope": None, "cross_slope": None, "surface": None,
        "curb_ramp": False, "step_free": None, "lighting": None, "shelter": None,
    }
    a = dict(base); b = dict(base)
    mod._fill(a, city="vegas", split="train", episode_id="ep1", base_seed=17)
    mod._fill(b, city="vegas", split="test", episode_id="ep2", base_seed=17)
    for k in ["width_m", "slope", "cross_slope", "surface", "curb_ramp", "step_free", "lighting", "shelter"]:
        assert a[k] == b[k]
    assert a["curb_ramp"] is False and a["step_free"] is False
    assert mod._edge_group_key({**base, "edge_id": "osm_way_123:4:5:rev"}) == mod._edge_group_key(base)


def test_curb_side_contract_is_city_neutral():
    assert compatible({"vehicle_side": "left", "curb_side": "left"}, "curb_side", "compatible_side")
    assert compatible({"vehicle_side": "right", "curb_side": "right"}, "curb_side", "compatible_side")
    assert not compatible({"vehicle_side": "right", "curb_side": "left"}, "curb_side", "compatible_side")


def test_high_resolution_dem_is_evidence_not_pedestrian_node_and_derives_grade():
    from capplan.data.gis_fusion import AccessibilityFusionBuilder, CoordinateTransformer, GISFeature, SceneContext

    features = [
        GISFeature(
            "walkway", "sidewalk", [[0.0, 0.0], [10.0, 0.0]],
            tags={"highway": "footway"}, source="official_sidewalk", confidence=0.95,
        ),
        GISFeature(
            "dem0", "poi", [[0.0, 0.0]],
            tags={"elevation_m": 10.0, "nominal_resolution_m": 1.0}, source="USGS_3DEP", confidence=0.9,
        ),
        GISFeature(
            "dem1", "poi", [[10.0, 0.0]],
            tags={"elevation_m": 11.0, "nominal_resolution_m": 1.0}, source="USGS_3DEP", confidence=0.9,
        ),
    ]
    builder = AccessibilityFusionBuilder(CoordinateTransformer({}), snap_tolerance_m=1.0)
    graph = builder.build_for_scene(SceneContext("ep"), features, add_bidirectional=True)
    assert len(graph.nodes) == 2
    assert all(n.kind != "poi" for n in graph.nodes)
    assert len(graph.edges) == 2
    assert all(abs(float(e.slope) - 0.1) < 1e-9 for e in graph.edges)
    for e in graph.edges:
        prov = (e.metadata or {}).get("field_provenance", {}).get("slope", {})
        assert prov.get("kind") == "derived"
        assert prov.get("method") == "absolute_endpoint_grade"
    assert graph.metadata["dem_point_nodes_inserted"] == 0
    assert graph.metadata["high_resolution_elevation_samples_cropped"] == 2


def test_coarse_dem_is_not_promoted_to_sidewalk_scale_slope():
    from capplan.data.gis_fusion import AccessibilityFusionBuilder, CoordinateTransformer, GISFeature, SceneContext

    features = [
        GISFeature("walkway", "sidewalk", [[0.0, 0.0], [10.0, 0.0]], tags={"highway": "footway"}, source="official_sidewalk"),
        GISFeature("dem0", "poi", [[0.0, 0.0]], tags={"elevation_m": 10.0, "nominal_resolution_m": 30.0}, source="GLO30"),
        GISFeature("dem1", "poi", [[10.0, 0.0]], tags={"elevation_m": 30.0, "nominal_resolution_m": 30.0}, source="GLO30"),
    ]
    graph = AccessibilityFusionBuilder(CoordinateTransformer({}), snap_tolerance_m=1.0).build_for_scene(SceneContext("ep"), features)
    assert len(graph.nodes) == 2
    assert all(e.slope is None for e in graph.edges)
    assert graph.metadata["coarse_elevation_samples_ignored_for_local_grade"] == 2


def test_same_site_observed_static_evidence_is_reused_for_missing_snapshot():
    mod = _load_script("hybrid_pudo_site_transfer_20260824", "scripts/build_hybrid_pudo_evidence.py")
    observed = {
        "anchor_id": "a1", "episode_id": "ep1", "x": 10.0, "y": 20.0,
        "side": "right", "adjacent_ped_node_id": "ped:1", "lane_id": "lane:1",
        "curb_height_m": 0.03, "sidewalk_width_m": 1.75,
        "deployment_clearance_m": 1.62, "curb_ramp": True,
    }
    missing = {**observed, "anchor_id": "a2", "episode_id": "ep2",
               "curb_height_m": None, "sidewalk_width_m": None,
               "deployment_clearance_m": None, "curb_ramp": None}
    prepared = {
        "ep1": [(observed, {
            "curb_height_m": {"kind": "observed", "source": "site_survey"},
            "sidewalk_width_m": {"kind": "observed", "source": "site_survey"},
            "deployment_clearance_m": {"kind": "observed", "source": "site_survey"},
            "curb_ramp": {"kind": "observed", "source": "site_survey"},
            "side": {"kind": "derived", "source": "map_geometry"},
        })],
        "ep2": [(missing, {"side": {"kind": "derived", "source": "map_geometry"}})],
    }
    canonical, counts, conflicts = mod._canonical_site_static_evidence(prepared, "boston")
    assert conflicts == []
    prov2 = prepared["ep2"][0][1]
    key = mod._site_key(missing, "boston")
    mod._apply_site_static_evidence(missing, prov2, key, canonical, counts)
    assert missing["curb_height_m"] == 0.03
    assert missing["sidewalk_width_m"] == 1.75
    assert missing["deployment_clearance_m"] == 1.62
    assert missing["curb_ramp"] is True
    assert prov2["sidewalk_width_m"]["kind"] == "derived"
    assert prov2["sidewalk_width_m"]["method"] == "same_physical_site_static_evidence_transfer"


def test_dataset_materialization_preserves_hybrid_physical_site_identity():
    mod = _load_script("build_dataset_hybrid_site_identity_20260824", "scripts/build_dataset.py")
    row = {
        "episode_id": "ep", "anchor_id": "a", "kind": "pickup_dropoff",
        "x": 1.0, "y": 2.0, "side": "right", "legal_stop": True,
        "legal_stop_source": "sim", "adjacent_ped_node_id": "ped:1",
        "curb_height_m": 0.03, "sidewalk_width_m": 1.6,
        "deployment_clearance_m": 1.7, "curb_ramp": True,
        "blockage_risk": 0.05, "map_confidence": 0.85, "dynamic_confidence": 0.95,
        "hybrid_evidence_complete": True, "hybrid_eligible": True,
        "hybrid_scenario_class": "accessible_loading",
        "hybrid_site_prior_class": "accessible_loading",
        "hybrid_seed": 11, "hybrid_dynamic_seed": 12,
        "hybrid_physical_site_key": "boston|ped:1|lane:1|0:0",
        "hybrid_standard_profile": "US_prior",
        "hybrid_missing_fields": [],
        "paper_claim_allowed": False,
        "field_provenance": {"curb_height_m": {"kind": "simulated"}},
    }
    anchors = mod._pudo_anchors_from_evidence_rows({("ep", "a"): row}, "ep")
    assert len(anchors) == 1
    a = anchors[0]
    assert a.hybrid_physical_site_key == row["hybrid_physical_site_key"]
    assert a.hybrid_site_prior_class == "accessible_loading"
    assert a.hybrid_dynamic_seed == 12
    assert a.hybrid_standard_profile == "US_prior"
