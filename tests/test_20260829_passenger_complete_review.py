from __future__ import annotations

import json
import time
from pathlib import Path

from capplan.data.schemas import AccessibilityNode


def _node(node_id: str, x: float, y: float, kind: str = "entrance", source: str = "mapped") -> AccessibilityNode:
    return AccessibilityNode(node_id=node_id, x=x, y=y, kind=kind, confidence=1.0, source=source)


def test_short_od_reselection_stays_near_route_destination():
    from scripts.build_service_layer import _choose_realistic_od
    import random

    nodes = [
        _node("o", 0.0, 0.0),
        _node("d_near", 20.0, 0.0),
        _node("d_good", 90.0, 0.0),
        _node("global_far", 1000.0, 1000.0),
    ]
    scene = {"route_corridor": {"polyline": [[0.0, 0.0], [20.0, 0.0]]}}
    o, d, prov = _choose_realistic_od(
        nodes, scene, random.Random(13), allow_non_entrance_od=True,
        trusted_source_tokens=None, max_entrance_route_distance_m=250.0,
        min_od_separation_m=80.0,
    )
    assert o.node_id == "o"
    assert d.node_id == "d_good"
    assert prov["route_destination_distance_m"] == 70.0
    assert prov["od_separation_target_met"] is True
    assert prov["od_separation_adjustment"] == "route_local_destination_reselection"


def test_short_od_without_route_local_alternative_is_kept_not_globalized():
    from scripts.build_service_layer import _choose_realistic_od
    import random

    nodes = [
        _node("o", 0.0, 0.0),
        _node("d_near", 20.0, 0.0),
        _node("global_far", 1000.0, 1000.0),
    ]
    scene = {"route_corridor": {"polyline": [[0.0, 0.0], [20.0, 0.0]]}}
    o, d, prov = _choose_realistic_od(
        nodes, scene, random.Random(13), allow_non_entrance_od=True,
        trusted_source_tokens=None, max_entrance_route_distance_m=250.0,
        min_od_separation_m=80.0,
    )
    assert o.node_id == "o"
    assert d.node_id == "d_near"
    assert prov["route_destination_distance_m"] == 0.0
    assert prov["od_separation_target_met"] is False
    assert prov["od_separation_adjustment"] == "kept_route_anchored_short_od"


def test_nested_merge_manifest_preserves_consensus_source_semantics():
    from scripts.merge_datasets import _consensus_manifest_field

    manifests = [
        {"input_manifests": [{"scene_source": "nuplan", "source_policy": "hybrid"}]},
        {"input_manifests": [{"scene_source": "nuplan", "source_policy": "hybrid"}]},
    ]
    assert _consensus_manifest_field(manifests, "scene_source") == "nuplan"
    assert _consensus_manifest_field(manifests, "source_policy") == "hybrid"
    manifests[1]["input_manifests"][0]["scene_source"] = "synthetic"
    assert _consensus_manifest_field(manifests, "scene_source") is None


def test_review_bundle_does_not_mark_run_context_anchor_stale(tmp_path: Path, monkeypatch):
    import scripts.build_hybrid_review_bundle as mod

    commands = tmp_path / "commands"
    commands.mkdir()
    identity = commands / "pipeline_identity.reviewfix7_dataset.txt"
    identity.write_text(f"CAPPLAN_PIPELINE_VERSION={mod.EXPECTED_PIPELINE_VERSION}\n", encoding="utf-8")
    context = commands / "hybrid_run_context.reviewfix8_dataset.json"
    # Deliberately place start_time_ns in the future: the context file is the
    # anchor, so its own mtime must not be judged against itself.
    context.write_text(json.dumps({
        "run_id": "test",
        "pipeline_version": mod.EXPECTED_PIPELINE_VERSION,
        "start_time_ns": time.time_ns() + 10_000_000_000,
        "critical_file_sha256": {},
        "reused_artifacts": [],
    }), encoding="utf-8")
    monkeypatch.setattr(mod, "_required_paths", lambda root: [(context, None)])
    report = mod._assess(tmp_path)
    assert report["stale_required"] == []
    assert report["status"] == "PASS"


def test_distribution_audit_reports_counterfactual_binding(tmp_path: Path):
    from scripts.audit_passenger_complete_distribution import audit

    def write(name: str, rows: list[dict]):
        (tmp_path / name).write_text("".join(json.dumps(x) + "\n" for x in rows), encoding="utf-8")

    write("service_requests.jsonl", [
        {"episode_id": "ep", "passenger_profile_id": "base", "od_provenance": {"route_origin_distance_m": 10, "route_destination_distance_m": 20, "od_euclidean_separation_m": 100}},
        {"episode_id": "ep", "passenger_profile_id": "strict", "od_provenance": {"route_origin_distance_m": 10, "route_destination_distance_m": 20, "od_euclidean_separation_m": 100}},
    ])
    write("skeleton_labels.jsonl", [{"passenger_id": "ep:base"}])
    write("certificate_labels.jsonl", [{"passenger_id": "ep:strict", "phase": "access", "resource_type": "slope"}])
    write("counterfactual_pairs.jsonl", [{
        "counterfactual_axis": "max_slope", "weak_passenger_id": "ep:base", "strict_passenger_id": "ep:strict"
    }])
    report = audit(tmp_path)
    axis = report["counterfactual_axis_outcome_summary"]["max_slope"]
    assert axis["base_success_strict_fail"] == 1
    assert axis["binding_rate_given_base_success"] == 1.0
    assert report["profile_outcome_summary"]["base"]["success_rate"] == 1.0
