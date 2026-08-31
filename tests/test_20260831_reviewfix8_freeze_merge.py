from __future__ import annotations

import json
from pathlib import Path

import yaml


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(x, sort_keys=True) + "\n" for x in rows), encoding="utf-8")


def test_counterfactual_profile_v2_thresholds_are_informative():
    cfg = yaml.safe_load(Path("configs/capability_profiles.counterfactual.yaml").read_text(encoding="utf-8"))
    by_id = {p["profile_id"]: p for p in cfg["profiles"]}
    access = by_id["cf_access_distance_strict"]
    door = by_id["cf_door_side_clearance_strict"]
    assert access["mobility"]["max_access_distance_m"] == 150.0
    assert access["mobility"]["max_egress_distance_m"] == 150.0
    assert door["interface"]["min_deployment_clearance_m"] == 1.8
    assert all(p["capability_version"] == "abilitybench_av_v2_reviewfix8_20260831" for p in cfg["profiles"])


def test_distribution_freeze_gate_rejects_zero_binding_axes(tmp_path: Path):
    from scripts.audit_passenger_complete_distribution import audit

    profiles = [
        "basic_service_complete", "cf_access_distance_strict", "cf_step_free_required",
        "cf_min_width_strict", "cf_ramp_or_lift_required", "cf_door_side_clearance_strict",
        "cf_ride_motion_strict", "cf_confidence_strict",
    ]
    axis_by_profile = {
        "cf_access_distance_strict": "access_distance",
        "cf_step_free_required": "step_free",
        "cf_min_width_strict": "min_width",
        "cf_ramp_or_lift_required": "ramp_lift",
        "cf_door_side_clearance_strict": "door_side_clearance",
        "cf_ride_motion_strict": "ride_motion",
        "cf_confidence_strict": "confidence",
    }
    requests = []
    for prof in profiles:
        requests.append({
            "episode_id": "ep", "passenger_profile_id": prof,
            "od_provenance": {
                "method": "nuplan_route_endpoint_to_mapped_entrance_or_frontage",
                "route_origin_distance_m": 10.0, "route_destination_distance_m": 20.0,
                "od_euclidean_separation_m": 100.0, "route_anchor_max_distance_m": 250.0,
            },
        })
    _write_jsonl(tmp_path / "service_requests.jsonl", requests)
    # Base and every strict profile succeed: monotonic, but no axis binds.
    _write_jsonl(tmp_path / "skeleton_labels.jsonl", [{"passenger_id": f"ep:{p}"} for p in profiles])
    _write_jsonl(tmp_path / "certificate_labels.jsonl", [])
    _write_jsonl(tmp_path / "counterfactual_pairs.jsonl", [
        {
            "counterfactual_axis": axis,
            "weak_passenger_id": "ep:basic_service_complete",
            "strict_passenger_id": f"ep:{prof}",
        }
        for prof, axis in axis_by_profile.items()
    ])
    report = audit(tmp_path, freeze_gate=True, min_binding_rate_given_base_success=0.05)
    assert report["status"] == "FAIL"
    assert any(x.startswith("freeze_gate_zero_binding:") for x in report["hard_errors"])


def test_streaming_merge_fast_path_dedupes_only_global_profiles(tmp_path: Path, monkeypatch):
    import scripts.merge_datasets as mod

    inputs = []
    profile = {"profile_id": "base", "value": 1}
    for idx, eid in enumerate(("e0", "e1")):
        d = tmp_path / f"in{idx}"
        d.mkdir()
        (d / "dataset_manifest.json").write_text(json.dumps({"scene_source": "nuplan", "source_policy": "hybrid"}), encoding="utf-8")
        (d / "validation_report.json").write_text(json.dumps({"ok": True}), encoding="utf-8")
        for name in mod.JSONL_FILES:
            rows = []
            if name == "episodes.jsonl":
                rows = [{"episode_id": eid}]
            elif name == "scenes.jsonl":
                rows = [{"episode_id": eid, "source": "nuplan"}]
            elif name == "capability_profiles.jsonl":
                rows = [profile]
            _write_jsonl(d / name, rows)
        splits = d / "splits"; splits.mkdir()
        (splits / "train_episodes.txt").write_text(eid + "\n", encoding="utf-8")
        (splits / "val_episodes.txt").write_text("", encoding="utf-8")
        (splits / "test_episodes.txt").write_text("", encoding="utf-8")
        inputs.append(d)

    monkeypatch.setattr(mod, "validate_dataset", lambda *a, **k: {"ok": True, "errors": [], "warnings": []})
    out = tmp_path / "out"
    report = mod.merge_datasets(inputs, out, strict=True, clean_output=True, progress=False)
    assert report["merge_strategy"] == "stream_raw_concat_disjoint_episodes"
    assert len(list(mod.iter_jsonl(out / "episodes.jsonl"))) == 2
    assert len(list(mod.iter_jsonl(out / "capability_profiles.jsonl"))) == 1
    manifest = json.loads((out / "dataset_manifest.json").read_text(encoding="utf-8"))
    assert manifest["graph_membership_preserved_from_validated_inputs"] is True


def test_reviewfix8_pipeline_and_merge_validation_markers_present():
    shell = Path("scripts/build_abilitybench_data0_20260817.sh").read_text(encoding="utf-8")
    merge = Path("scripts/merge_datasets.py").read_text(encoding="utf-8")
    validate = Path("capplan/data/validate_dataset.py").read_text(encoding="utf-8")
    assert 'PIPELINE_VERSION="abilitybench_data0_passenger_complete_reviewfix8_20260831"' in shell
    assert "reviewfix8-preflight) reviewfix8_preflight" in shell
    assert "hybrid-dataset-resume-reviewfix8) hybrid_dataset_resume_reviewfix8" in shell
    assert 'VERSION = "capplan_merge_datasets_v2_streaming_20260831"' in merge
    assert 'VALIDATION_VERSION = "capplan_dataset_validation_v2_linear_20260831"' in validate
    assert "expected_edge_label_count" in validate
