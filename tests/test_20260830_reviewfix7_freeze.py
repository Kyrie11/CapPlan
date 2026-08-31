from __future__ import annotations

import json
import random
from pathlib import Path

import pytest

from capplan.data.schemas import AccessibilityNode


def _node(node_id: str, x: float, y: float, kind: str = "entrance", source: str = "mapped") -> AccessibilityNode:
    return AccessibilityNode(node_id=node_id, x=x, y=y, kind=kind, confidence=1.0, source=source)


def _write_jsonl(root: Path, name: str, rows: list[dict]) -> None:
    (root / name).write_text("".join(json.dumps(x) + "\n" for x in rows), encoding="utf-8")


def test_far_frontage_is_not_used_as_route_local_destination():
    from scripts.build_service_layer import _choose_realistic_od

    nodes = [
        _node("o", 0.0, 0.0),
        _node("far_sidewalk", 1200.0, 0.0, kind="sidewalk", source="osm"),
    ]
    scene = {"route_corridor": {"polyline": [[0.0, 0.0], [20.0, 0.0]]}}
    with pytest.raises(RuntimeError, match="route-local destination"):
        _choose_realistic_od(
            nodes, scene, random.Random(13), allow_non_entrance_od=True,
            trusted_source_tokens=None, max_entrance_route_distance_m=250.0,
            min_od_separation_m=80.0,
        )


def test_distribution_audit_hard_fails_route_anchor_outlier(tmp_path: Path):
    from scripts.audit_passenger_complete_distribution import audit

    _write_jsonl(tmp_path, "service_requests.jsonl", [{
        "episode_id": "ep", "request_id": "ep:req", "passenger_profile_id": "base",
        "od_provenance": {
            "method": "nuplan_route_endpoint_to_mapped_entrance_or_frontage",
            "route_origin_distance_m": 10.0,
            "route_destination_distance_m": 900.0,
            "od_euclidean_separation_m": 100.0,
            "route_anchor_max_distance_m": 250.0,
        },
    }])
    _write_jsonl(tmp_path, "skeleton_labels.jsonl", [{"passenger_id": "ep:base"}])
    _write_jsonl(tmp_path, "certificate_labels.jsonl", [])
    _write_jsonl(tmp_path, "counterfactual_pairs.jsonl", [])
    report = audit(tmp_path, 250.0)
    assert report["status"] == "FAIL"
    assert report["od_route_anchoring"]["route_anchor_violation_count"] == 1
    assert any(x.startswith("nuplan_route_anchor_radius_violation:") for x in report["hard_errors"])


def test_hybrid_semantic_audit_hard_fails_route_anchor_outlier(tmp_path: Path):
    from scripts.audit_hybrid_benchmark import audit

    (tmp_path / "dataset_manifest.json").write_text(json.dumps({"source_policy": "hybrid"}), encoding="utf-8")
    _write_jsonl(tmp_path, "episodes.jsonl", [{"episode_id": "ep"}])
    _write_jsonl(tmp_path, "service_requests.jsonl", [{
        "episode_id": "ep", "request_id": "ep:req", "passenger_profile_id": "base",
        "counterfactual_group_id": "ep:cf", "origin_entrance_id": "o", "destination_entrance_id": "d",
        "request_time_s": 1.0, "request_time_source": "nuplan_scene_timestamp", "vehicle_id": "veh",
        "od_provenance": {
            "method": "nuplan_route_endpoint_to_mapped_entrance_or_frontage",
            "route_origin_distance_m": 10.0, "route_destination_distance_m": 900.0,
            "od_euclidean_separation_m": 100.0, "route_anchor_max_distance_m": 250.0,
        },
    }])
    _write_jsonl(tmp_path, "capability_contracts.jsonl", [{"passenger_id": "ep:base", "metadata": {"episode_id": "ep"}}])
    _write_jsonl(tmp_path, "skeleton_labels.jsonl", [])
    _write_jsonl(tmp_path, "certificate_labels.jsonl", [{
        "passenger_id": "ep:base", "phase": "access", "resource_type": "slope",
        "signed_margin": -0.1, "confidence": 1.0, "evidence_source": "test",
    }])
    _write_jsonl(tmp_path, "counterfactual_pairs.jsonl", [])
    _write_jsonl(tmp_path, "vehicle_interfaces.jsonl", [{"episode_id": "ep", "vehicle_id": "veh", "fleet_type": "test"}])
    _write_jsonl(tmp_path, "candidate_transitions.jsonl", [{"episode_id": "ep", "transition_id": "t"}])
    _write_jsonl(tmp_path, "passenger_edge_labels.jsonl", [{"transition_id": "t", "passenger_id": "ep:base", "y_e_p": False}])
    fp = {k: {"kind": "observed"} for k in ["curb_height_m", "sidewalk_width_m", "deployment_clearance_m", "curb_ramp", "legal_stop", "side", "blockage_risk"]}
    _write_jsonl(tmp_path, "pudo_anchors.jsonl", [
        {"episode_id": "ep", "pudo_id": "p0", "hybrid_eligible": True, "legal_stop": True, "side": "right", "field_provenance": fp},
        {"episode_id": "ep", "pudo_id": "p1", "hybrid_eligible": True, "legal_stop": True, "side": "right", "field_provenance": fp},
    ])
    report = audit(tmp_path, expected_requests_per_episode=1, max_route_anchor_distance_m=250.0)
    assert report["status"] == "FAIL"
    assert report["od_route_anchor_violation_count"] == 1
    assert any("exceed their allowed route-anchor radius" in x for x in report["errors"])


def test_reviewfix7_pipeline_has_final_freeze_stages():
    text = Path("scripts/build_abilitybench_data0_20260817.sh").read_text(encoding="utf-8")
    assert 'PIPELINE_VERSION="abilitybench_data0_passenger_complete_reviewfix8_20260831"' in text
    assert "reviewfix8-preflight) reviewfix8_preflight" in text
    assert "hybrid-dataset-resume-reviewfix8) hybrid_dataset_resume_reviewfix8" in text
    assert "passenger_complete_distribution_audit" in text


def test_relation_mlp_is_explicitly_not_true_hgt():
    text = Path("capplan/models/casa_torch.py").read_text(encoding="utf-8")
    train = Path("scripts/train_casa.py").read_text(encoding="utf-8")
    assert 'self.architecture_semantics = "relation_aware_transition_mlp_surrogate"' in text
    assert "self.true_heterogeneous_message_passing = False" in text
    assert 'choices=["relation_mlp", "hgt", "rgcn", "linear_smoke"]' in train
    assert "--allow_relation_surrogate_paper_mode" in train
    assert "--feature_policy" in train


def test_paper_closed_loop_guard_does_not_pretend_to_run_nuplan_hydra():
    closed = Path("scripts/run_closed_loop_eval.py").read_text(encoding="utf-8")
    abl = Path("scripts/run_ablations.py").read_text(encoding="utf-8")
    assert "--nuplan_sim_config is metadata only and is not sufficient" in closed
    assert "--allow_posthoc_episode_vehicle_metrics" in closed
    assert "does not run a method-specific integrated nuPlan simulation" in closed
    assert "--allow_posthoc_episode_vehicle_metrics" in abl
    assert "do not run method-specific integrated" in abl
