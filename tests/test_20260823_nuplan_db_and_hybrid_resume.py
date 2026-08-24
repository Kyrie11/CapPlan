from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from capplan.utils.build_fingerprint import file_inventory_fingerprint

ROOT = Path(__file__).resolve().parents[1]


def _load_script(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_jsonl(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_build_dataset_accepts_concrete_db_manifest(tmp_path: Path):
    mod = _load_script("build_dataset_db_manifest_20260823", "scripts/build_dataset.py")
    root = tmp_path / "splits"
    root.mkdir()
    a = root / "a.db"; a.write_bytes(b"a")
    b = root / "b.db"; b.write_bytes(b"b")
    manifest = tmp_path / "dbs.txt"
    manifest.write_text(f"# audited val/boston DBs\n{a}\n{b.name}\n", encoding="utf-8")
    args = argparse.Namespace(
        nuplan_db_manifest=str(manifest), nuplan_db_files=None, nuplan_db_dirs=None,
        nuplan_db_root=str(root), nuplan_data_root=str(root), nuplan_root=None,
    )
    assert mod._resolve_db_inputs(args) == [str(a), str(root / b.name)]


def test_allowlist_scan_limit_distinguishes_hybrid_first_n_from_paper_full_scan():
    mod = _load_script("build_dataset_limit_20260823", "scripts/build_dataset.py")
    base = dict(max_scenarios=250)
    assert mod._adapter_scenario_limit(argparse.Namespace(**base, episode_allowlist_within_max_scenarios=False), None) == 250
    allow = {"ep0", "ep1"}
    assert mod._adapter_scenario_limit(argparse.Namespace(**base, episode_allowlist_within_max_scenarios=False), allow) == 0
    assert mod._adapter_scenario_limit(argparse.Namespace(**base, episode_allowlist_within_max_scenarios=True), allow) == 250


def test_prepare_uses_city_specific_train_dirs_and_audited_mixed_split_manifest(tmp_path: Path):
    mod = _load_script("prepare_db_selection_20260823", "scripts/prepare_abilitybench_external.py")
    db_root = tmp_path / "splits"
    (db_root / "train_boston").mkdir(parents=True)
    external = tmp_path / "external"; reports = external / "reports"; reports.mkdir(parents=True)

    train_args, train_desc = mod._city_db_cli_args(
        split_name="train", city="boston",
        split_cfg={"cities": ["boston", "vegas"], "db_dirs": ["train_boston", "train_vegas"], "db_dirs_by_city": {"boston": ["train_boston"], "vegas": ["train_vegas"]}},
        city_cfg={"map_names": ["us-ma-boston"]}, db_root=db_root,
        external_root=external, reports_root=reports, dry_run=False,
    )
    assert train_args == ["--nuplan_db_dirs", "train_boston"]
    assert train_desc.startswith("dirs:")

    val = db_root / "val"; val.mkdir()
    boston = val / "boston.db"; boston.write_bytes(b"bos")
    vegas = val / "vegas.db"; vegas.write_bytes(b"lv")
    report = {
        "status": "PASS", "split": "val", "db_dirs": ["val"],
        "db_inventory_fingerprint": file_inventory_fingerprint([boston, vegas]),
        "dbs": [
            {"db": str(boston), "mapped_cities": ["boston"]},
            {"db": str(vegas), "mapped_cities": ["vegas"]},
        ],
    }
    (reports / "nuplan_db_cities.val.json").write_text(json.dumps(report), encoding="utf-8")
    val_args, val_desc = mod._city_db_cli_args(
        split_name="val", city="boston",
        split_cfg={"cities": ["boston", "vegas"], "db_dirs": ["val"]},
        city_cfg={"map_names": ["us-ma-boston"]}, db_root=db_root,
        external_root=external, reports_root=reports, dry_run=False,
    )
    assert val_args[:1] == ["--nuplan_db_manifest"]
    manifest = Path(val_args[1])
    assert manifest.read_text(encoding="utf-8").splitlines() == [str(boston.resolve())]
    assert val_desc == "inspection_manifest:1db"


def test_hybrid_forced_positive_prefers_preexisting_unblocked_candidates(tmp_path: Path):
    inp = tmp_path / "in.jsonl"; out = tmp_path / "out.jsonl"; report = tmp_path / "report.json"
    rows = []
    for i, risk in enumerate([0.95, 0.01, 0.02]):
        rows.append({
            "anchor_id": f"a{i}", "episode_id": "ep", "kind": "pickup_dropoff", "side": "right",
            "legal_stop": False, "legal_stop_source": "no_legality_evidence",
            "adjacent_ped_node_id": f"n{i}", "curb_height_m": None, "sidewalk_width_m": None,
            "deployment_clearance_m": None, "blockage_risk": risk, "source": "nuplan_route_candidate",
        })
    _write_jsonl(inp, rows)
    subprocess.check_call([
        sys.executable, str(ROOT / "scripts/build_hybrid_pudo_evidence.py"),
        "--input_pudo_jsonl", str(inp), "--output_pudo_jsonl", str(out),
        "--city", "vegas", "--split", "train", "--seed", "7",
        "--min_positive_per_episode", "2", "--report_json", str(report),
    ], cwd=ROOT)
    got = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines() if x.strip()]
    by_id = {r["anchor_id"]: r for r in got}
    assert by_id["a1"]["hybrid_scenario_class"] == "accessible_loading"
    assert by_id["a2"]["hybrid_scenario_class"] == "accessible_loading"
    assert by_id["a1"]["hybrid_eligible"] and by_id["a2"]["hybrid_eligible"]
    assert json.loads(report.read_text(encoding="utf-8"))["episodes_with_min_hybrid_eligible_pudos"] == 1


def test_hybrid_ready_allowlist_rejects_without_synthesizing_geometry(tmp_path: Path):
    inp = tmp_path / "hybrid.jsonl"
    rows = [
        {"episode_id": "good", "hybrid_evidence_complete": True, "hybrid_eligible": True, "legal_stop": True, "blockage_risk": 0.01},
        {"episode_id": "good", "hybrid_evidence_complete": True, "hybrid_eligible": True, "legal_stop": True, "blockage_risk": 0.02},
        {"episode_id": "one_candidate", "hybrid_evidence_complete": True, "hybrid_eligible": True, "legal_stop": True, "blockage_risk": 0.01},
        {"episode_id": "blocked", "hybrid_evidence_complete": True, "hybrid_eligible": False, "legal_stop": True, "blockage_risk": 0.95},
        {"episode_id": "blocked", "hybrid_evidence_complete": True, "hybrid_eligible": True, "legal_stop": True, "blockage_risk": 0.01},
    ]
    _write_jsonl(inp, rows)
    out = tmp_path / "ready.txt"; rejected = tmp_path / "rejected.txt"; report = tmp_path / "report.json"
    subprocess.check_call([
        sys.executable, str(ROOT / "scripts/build_hybrid_ready_allowlist.py"),
        "--input_pudo_jsonl", str(inp), "--output_allowlist", str(out),
        "--output_rejected", str(rejected), "--min_hybrid_eligible_pudos", "2",
        "--city", "vegas", "--split", "test", "--report_json", str(report),
    ], cwd=ROOT)
    assert out.read_text(encoding="utf-8").splitlines() == ["good"]
    assert rejected.read_text(encoding="utf-8").splitlines() == ["blocked", "one_candidate"]
    rep = json.loads(report.read_text(encoding="utf-8"))
    assert rep["allowed_episode_count"] == 1
    assert rep["rejected_episode_count"] == 2
    assert rep["rejection_reason_counts"]["insufficient_geometry_anchored_candidates"] == 1


def test_prepare_internal_artifact_gate_accepts_text_allowlist_and_is_lightweight(tmp_path: Path):
    mod = _load_script("prepare_internal_artifact_gate_20260824", "scripts/prepare_abilitybench_external.py")
    allow = tmp_path / "ready.txt"
    allow.write_text("# hybrid-ready episodes\nep_a\nep_b\n", encoding="utf-8")
    # The internal gate must not call the external source inspector.  In
    # particular, plain-text allowlists are valid pipeline artifacts.
    mod.inspect_source = lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("inspect_source must not be called"))
    mod._require_artifact(allow, "hybrid-ready allowlist", False)

    graph_dir = tmp_path / "graphs"
    graph_dir.mkdir()
    (graph_dir / "ep.edges.jsonl").write_text('{"edge_id":"e"}\n', encoding="utf-8")
    mod._require_artifact(graph_dir, "accessibility graphs", False)


def test_prepare_internal_artifact_gate_rejects_empty_text_allowlist(tmp_path: Path):
    import pytest
    mod = _load_script("prepare_internal_artifact_gate_empty_20260824", "scripts/prepare_abilitybench_external.py")
    allow = tmp_path / "empty.txt"
    allow.write_text("# comments only\n\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="no usable entries"):
        mod._require_artifact(allow, "hybrid-ready allowlist", False)
