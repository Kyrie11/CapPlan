from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_contract_episode_id_prefers_explicit_metadata():
    from capplan.data.capability_contracts import contract_episode_id
    from capplan.data.schemas import CapabilityContract

    c = CapabilityContract("nuplan_episode:basic_service_complete", [], metadata={"episode_id": "nuplan_episode"})
    assert contract_episode_id(c) == "nuplan_episode"
    assert contract_episode_id("legacy_episode:p0") == "legacy_episode"
    assert contract_episode_id("new_episode:strict_width") == "new_episode"


def test_georeference_overlap_uses_containment_direction():
    from scripts.validate_georeference_alignment import fraction_covered

    map_extent = (4.0, 4.0, 6.0, 6.0)
    aoi_extent = (0.0, 0.0, 10.0, 10.0)
    assert fraction_covered(map_extent, aoi_extent) == 1.0
    assert fraction_covered(aoi_extent, map_extent) == 0.04


def test_boston_pwd_sidewalk_uses_explicit_source_units(tmp_path: Path):
    src = tmp_path / "pwd_sidewalk.geojson"
    src.write_text(json.dumps({
        "type": "FeatureCollection",
        "features": [{
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [[-71.05, 42.35], [-71.049, 42.35]]},
            "properties": {
                "OBJECTID": 1,
                "Width": 6.0,
                "Width_unit": "feet",
                "Slope": 4.0,
                "Slope_unit": "percent",
                "SurfaceType": "Concrete",
            },
        }],
    }), encoding="utf-8")
    out = tmp_path / "normalized.geojson"
    subprocess.check_call([
        sys.executable, "scripts/normalize_accessibility_evidence.py",
        "--input", str(src), "--output", str(out),
        "--profile", "boston_pwd_sidewalk", "--source", "City of Boston PWD",
    ])
    props = json.loads(out.read_text(encoding="utf-8"))["features"][0]["properties"]
    assert abs(props["sidewalk_width_m"] - 1.8288) < 1e-9
    assert abs(props["slope"] - 0.04) < 1e-12


def test_audit_shortlist_schema_includes_independent_entrance_coordinates():
    text = Path("scripts/export_pudo_audit_shortlist.py").read_text(encoding="utf-8")
    assert '"entrance_id", "entrance_lon", "entrance_lat"' in text


def test_provenance_manifest_rejects_review_placeholders():
    from scripts.build_provenance_manifest import _is_placeholder, _valid_retrieved_at

    assert _is_placeholder("REVIEW_AND_RECORD_PORTAL_TERMS")
    assert _is_placeholder("VERIFY_EXACT_LICENSE")
    assert not _is_placeholder("ODbL-1.0")
    assert not _valid_retrieved_at("REPLACE_WITH_UTC_TIMESTAMP")
    assert _valid_retrieved_at("2026-08-17T14:00:00+00:00")


def test_prepare_pipeline_mirrors_review_reports_under_external_reports():
    text = Path("scripts/prepare_abilitybench_external.py").read_text(encoding="utf-8")
    assert 'reports_root = external_root / "reports" / "build" / split_name' in text
    assert 'dataset_diagnostics.{city}.json' in text


def test_casa_paper_safe_features_mask_direct_supervision_targets():
    from capplan.data.accessibility_layer import synthetic_accessibility_graph
    from capplan.data.pudo_interface_layer import synthetic_pudo_anchors, synthetic_vehicle_interface
    from capplan.models.casa_features import FeatureVocab, encode_transition
    from capplan.planning.transition_generator import TransitionGenerator

    eid = "feature_leak_guard"
    graph = synthetic_accessibility_graph(eid)
    t = TransitionGenerator().generate(eid, graph, synthetic_pudo_anchors(eid, graph=graph), synthetic_vehicle_interface(eid))[0]
    legacy = encode_transition(t, FeatureVocab(), feature_policy="legacy")
    safe = encode_transition(t, FeatureVocab(), feature_policy="paper_safe")
    # Stable dimensionality; target-derived slots are masked in publication mode.
    assert len(legacy) == len(safe)
    for idx in [2, 3, 4, 5, 6, 7, 10]:
        assert safe[idx] in {0.0, -1.0}
    assert legacy[6] == float(t.completion_value)


def test_casa_explicit_offline_value_target_fails_closed_without_labels(tmp_path: Path):
    dataset = tmp_path / "dataset"
    subprocess.check_call([
        sys.executable, "scripts/build_dataset.py",
        "--scene_source", "synthetic",
        "--max_scenarios", "1",
        "--accessibility_source", "synthetic_local",
        "--num_contracts_per_scene", "1",
        "--output_dir", str(dataset),
        "--seed", "17",
    ])
    from capplan.models.casa_dataset import CASADataset
    try:
        CASADataset(dataset, "train", value_target="offline_tsbs")
    except RuntimeError as exc:
        assert "completion_value_labels.offline_tsbs.jsonl" in str(exc)
    else:
        raise AssertionError("offline_tsbs must fail closed when explicit target labels are absent")


def test_casa_skeleton_value_target_is_binary_and_not_transition_prior(tmp_path: Path):
    dataset = tmp_path / "dataset"
    subprocess.check_call([
        sys.executable, "scripts/build_dataset.py",
        "--scene_source", "synthetic",
        "--max_scenarios", "1",
        "--accessibility_source", "synthetic_local",
        "--num_contracts_per_scene", "1",
        "--output_dir", str(dataset),
        "--seed", "19",
    ])
    from capplan.models.casa_dataset import CASADataset
    ds = CASADataset(dataset, "train", value_target="skeleton")
    assert ds.samples
    assert {s.y_value for s in ds.samples}.issubset({0.0, 1.0})


def test_casa_balanced_sampler_flags_change_sampling_probabilities():
    import importlib.util
    import numpy as np
    from capplan.models.casa_dataset import CASASample

    spec = importlib.util.spec_from_file_location("train_casa_script", Path("scripts/train_casa.py"))
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    samples = [
        CASASample("t0", "e0", "e0:common", [0.0], 1, 1, 0, [], []),
        CASASample("t1", "e1", "e1:common", [0.0], 1, 1, 0, [], []),
        CASASample("t2", "e2", "e2:rare", [1.0], 1, 1, 0, [], []),
    ]
    probs, report = mod._balanced_sampling_probabilities(samples, profile_balanced=True, action_balanced=False)
    assert report["enabled"]
    assert np.isclose(float(probs.sum()), 1.0)
    assert probs[2] > probs[0]


def test_real_nuplan_zero_limit_is_documented_as_all_matching_scenarios():
    adapter_text = Path("capplan/data/nuplan_adapter.py").read_text(encoding="utf-8")
    prepare_text = Path("scripts/prepare_abilitybench_external.py").read_text(encoding="utf-8")
    assert "scenario_limit = None if max_scenarios <= 0" in adapter_text
    assert "0 means all matching scenarios" in prepare_text
