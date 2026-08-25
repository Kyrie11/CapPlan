import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

from capplan.models.losses import calibration_interval_loss, casa_loss

ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_typed_demand_calibration_uses_beta_and_mask_not_edge_residual():
    demand_pred = np.array([[3.0, 100.0]], dtype=np.float32)
    demand_target = np.array([[1.0, 0.0]], dtype=np.float32)
    mask = np.array([[1.0, 0.0]], dtype=np.float32)
    sigma = np.array([[1.0, 0.01]], dtype=np.float32)
    beta = np.array([[3.0, 1.0]], dtype=np.float32)
    loss = casa_loss(
        np.array([0.99]), np.array([0.0]), np.array([0.5]), np.array([0.5]),
        uncertainty=sigma, uncertainty_beta=beta,
        demand_pred=demand_pred, demand_target=demand_target, demand_mask=mask,
    )
    # 2.0 residual is covered by beta=3 * sigma=1; only tiny sigma regularizer remains.
    assert 0.0 <= loss["L_cal"] < 0.01
    uncovered = calibration_interval_loss(demand_pred - demand_target, sigma, beta=np.ones_like(beta), mask=mask)
    assert uncovered > 0.9


def test_review_bundle_assessment_rejects_wrong_fresh_version(tmp_path):
    mod = _load_script("hybrid_review_bundle_reviewfix", "scripts/build_hybrid_review_bundle.py")
    root = tmp_path / "reports"; (root / "commands").mkdir(parents=True)
    identity = root / "commands/pipeline_identity.realism_v4_resume.txt"
    identity.write_text("CAPPLAN_PIPELINE_VERSION=test\n")
    # A fresh but wrong graph version is a semantic failure, while most other final
    # artifacts are intentionally absent and make the snapshot incomplete too.
    graph = root / "hybrid_graph.train.boston.json"
    graph.write_text(json.dumps({"status": "PASS", "version": "old"}))
    now = identity.stat().st_mtime_ns
    os.utime(graph, ns=(now + 10_000_000, now + 10_000_000))
    report = mod._assess(root)
    assert report["status"] == "FAIL"
    assert report["version_mismatches"]
    assert report["missing_required"]


def test_cross_split_peer_static_evidence_transfers_observed_width(tmp_path):
    train = tmp_path / "train.jsonl"
    val = tmp_path / "val.jsonl"
    out = tmp_path / "out.jsonl"
    report = tmp_path / "report.json"
    common = {
        "anchor_id": "a", "x": 10.0, "y": 20.0,
        "adjacent_ped_node_id": "ped:1", "lane_id": "lane:1",
        "kind": "pickup_dropoff", "side": "right",
        "curb_height_m": 0.03, "deployment_clearance_m": 1.8,
        "curb_ramp": True, "legal_stop": True,
        "legal_basis": "observed_loading_zone", "blockage_risk": 0.05,
        "field_provenance": {
            "curb_height_m": {"kind": "observed", "source": "survey"},
            "deployment_clearance_m": {"kind": "observed", "source": "survey"},
            "curb_ramp": {"kind": "observed", "source": "survey"},
            "legal_stop": {"kind": "observed", "source": "regulation"},
            "legal_basis": {"kind": "observed", "source": "regulation"},
            "side": {"kind": "derived", "source": "map_geometry"},
        },
    }
    train_row = {**common, "episode_id": "ep_train", "sidewalk_width_m": 1.73}
    train_row["field_provenance"] = {**common["field_provenance"], "sidewalk_width_m": {"kind": "observed", "source": "survey"}}
    val_row = {**common, "episode_id": "ep_val", "sidewalk_width_m": None}
    for path, row in [(train, train_row), (val, val_row)]:
        path.write_text(json.dumps(row) + "\n")
    subprocess.check_call([
        sys.executable, "scripts/build_hybrid_pudo_evidence.py",
        "--input_pudo_jsonl", str(val), "--output_pudo_jsonl", str(out),
        "--city", "boston", "--split", "val", "--min_positive_per_episode", "1",
        "--site_evidence_peer_jsonl", f"train={train}",
        "--report_json", str(report),
    ], cwd=ROOT)
    row = json.loads(out.read_text().splitlines()[0])
    assert row["sidewalk_width_m"] == pytest.approx(1.73)
    prov = row["field_provenance"]["sidewalk_width_m"]
    assert prov["method"] == "same_physical_site_static_evidence_transfer"
    assert json.loads(report.read_text())["cross_split_site_evidence_peer_rows_loaded"] == 1


def test_site_consistency_audit_excludes_dynamic_and_route_relative_side(tmp_path):
    inputs = []
    for split, blockage, side in [("train", 0.02, "right"), ("val", 0.93, "left"), ("test", 0.07, "right")]:
        p = tmp_path / f"{split}.jsonl"
        p.write_text(json.dumps({
            "episode_id": f"{split}:ep", "anchor_id": "a",
            "hybrid_physical_site_key": "boston|ped:1|lane:1|2:4",
            "curb_height_m": 0.03, "sidewalk_width_m": 1.6,
            "deployment_clearance_m": 1.7, "curb_ramp": True,
            "side": side, "lighting": "lit", "shelter": False,
            "legal_stop": True, "blockage_risk": blockage,
        }) + "\n")
        inputs.extend(["--input", f"{split}={p}"])
    report = tmp_path / "site.json"
    subprocess.check_call([
        sys.executable, "scripts/audit_hybrid_site_consistency.py",
        *inputs, "--output", str(report), "--fail_on_error",
    ], cwd=ROOT)
    d = json.loads(report.read_text())
    assert d["status"] == "PASS"
    assert d["version"] == "abilitybench_hybrid_site_consistency_v2_20260825"
    assert d["cross_split_physical_site_count"] == 1
    assert d["static_conflict_count"] == 0
    assert d["relational_variation_site_counts"]["side"] == 1
    assert "side" in d["relational_fields_excluded_from_static_consistency"]


def test_site_consistency_still_fails_true_static_geometry_conflict(tmp_path):
    inputs = []
    for split, width in [("train", 1.60), ("val", 2.20)]:
        p = tmp_path / f"{split}.jsonl"
        p.write_text(json.dumps({
            "episode_id": f"{split}:ep", "anchor_id": "a",
            "hybrid_physical_site_key": "boston|ped:1|lane:1|2:4",
            "curb_height_m": 0.03, "sidewalk_width_m": width,
            "deployment_clearance_m": 1.7, "curb_ramp": True,
            "side": "right", "lighting": "lit", "shelter": False,
            "legal_stop": True, "blockage_risk": 0.05,
        }) + "\n")
        inputs.extend(["--input", f"{split}={p}"])
    report = tmp_path / "site.json"
    proc = subprocess.run([
        sys.executable, "scripts/audit_hybrid_site_consistency.py",
        *inputs, "--output", str(report), "--fail_on_error",
    ], cwd=ROOT, check=False)
    assert proc.returncode == 2
    d = json.loads(report.read_text())
    assert d["status"] == "FAIL"
    assert d["static_conflict_count"] == 1
    assert d["static_conflict_examples"][0]["field"] == "sidewalk_width_m"


def test_route_relative_side_is_not_canonicalized_as_static_site_evidence():
    mod = _load_script("hybrid_pudo_route_side_reviewfix2", "scripts/build_hybrid_pudo_evidence.py")
    a = {
        "anchor_id": "a1", "episode_id": "ep1", "x": 10.0, "y": 20.0,
        "side": "right", "adjacent_ped_node_id": "ped:1", "lane_id": "lane:1",
        "curb_height_m": 0.03, "sidewalk_width_m": 1.75,
        "deployment_clearance_m": 1.62, "curb_ramp": True,
    }
    b = {**a, "anchor_id": "a2", "episode_id": "ep2", "side": "left"}
    prov = {
        "curb_height_m": {"kind": "observed", "source": "survey"},
        "sidewalk_width_m": {"kind": "observed", "source": "survey"},
        "deployment_clearance_m": {"kind": "observed", "source": "survey"},
        "curb_ramp": {"kind": "observed", "source": "survey"},
        "side": {"kind": "derived", "source": "episode_route_geometry"},
    }
    prepared = {"ep1": [(a, dict(prov))], "ep2": [(b, dict(prov))]}
    canonical, counts, conflicts = mod._canonical_site_static_evidence(prepared, "boston")
    assert conflicts == []
    assert "side" not in canonical[mod._site_key(a, "boston")]
    assert counts.get("conflict:side", 0) == 0


def test_reviewfix2_pipeline_exposes_post_pudo_resume_stage():
    script = (ROOT / "scripts/build_abilitybench_data0_20260817.sh").read_text()
    assert 'PIPELINE_VERSION="abilitybench_data0_realism_v4_reviewfix2_20260825"' in script
    assert "hybrid-realism-resume-post-pudo) hybrid_realism_resume_post_pudo" in script
    assert "CAPPLAN_SITE_AUDIT_V2=present" in script

def test_reviewfix2_pipeline_exposes_recommended_resume_stage():
    script = (ROOT / "scripts/build_abilitybench_data0_20260817.sh").read_text()
    assert "hybrid-realism-resume-reviewfix2) hybrid_realism_resume_reviewfix2" in script
    assert "CAPPLAN_REVIEWFIX2_RESUME_DISPATCH=present" in script


def test_review_bundle_rejects_fresh_hybrid_pudo_report_with_static_conflicts(tmp_path):
    mod = _load_script("hybrid_review_bundle_reviewfix2", "scripts/build_hybrid_review_bundle.py")
    root = tmp_path / "reports"; (root / "commands").mkdir(parents=True)
    identity = root / "commands/pipeline_identity.realism_v4_resume.txt"
    identity.write_text("CAPPLAN_PIPELINE_VERSION=test\n")
    pudo = root / "hybrid_pudo.train.boston.json"
    pudo.write_text(json.dumps({
        "status": "PASS", "version": "abilitybench_hybrid_pudo_v4_20260824",
        "same_site_static_evidence_conflict_count": 3,
    }))
    now = identity.stat().st_mtime_ns
    os.utime(pudo, ns=(now + 10_000_000, now + 10_000_000))
    report = mod._assess(root)
    assert report["status"] == "FAIL"
    assert any(x["path"] == "hybrid_pudo.train.boston.json" for x in report["failed_required"])
