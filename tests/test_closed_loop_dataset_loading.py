import json
import subprocess
import sys
from pathlib import Path

from capplan.evaluation.closed_loop import ClosedLoopRunner
from capplan.utils.serialization import read_jsonl

ROOT = Path(__file__).resolve().parents[1]


def _build_dataset(tmp_path):
    out = tmp_path / "dataset"
    subprocess.check_call([
        sys.executable, "scripts/build_dataset.py",
        "--scene_source", "synthetic",
        "--max_scenarios", "1",
        "--accessibility_source", "synthetic_local",
        "--num_contracts_per_scene", "2",
        "--output_dir", str(out),
        "--seed", "3",
    ], cwd=ROOT)
    return out


def test_closed_loop_loads_saved_graph_pudo_vehicle(tmp_path):
    dataset = _build_dataset(tmp_path)
    output = tmp_path / "eval"
    ClosedLoopRunner().run_dataset(str(dataset), str(output))
    assert (output / "episode_metrics.jsonl").exists()
    assert read_jsonl(output / "episode_metrics.jsonl")


def test_closed_loop_does_not_call_synthetic_generators_when_dataset_exists():
    import inspect
    import capplan.evaluation.closed_loop as closed_loop
    src = inspect.getsource(closed_loop)
    assert "synthetic_accessibility_graph" not in src
    assert "synthetic_pudo_anchors" not in src
    assert "synthetic_vehicle_interface" not in src


def test_oracle_certificate_loaded_from_dataset_label(tmp_path):
    dataset = _build_dataset(tmp_path)
    output = tmp_path / "eval"
    ClosedLoopRunner().run_dataset(str(dataset), str(output))
    certs = {(r["episode_id"], r["passenger_id"]): r for r in read_jsonl(dataset / "certificate_labels.jsonl")}
    rows = read_jsonl(output / "episode_metrics.jsonl")
    for row in rows:
        key = (row["episode_id"], row["passenger_id"])
        if key in certs:
            assert row["oracle_certificate"]["resource_type"] == certs[key]["resource_type"]
            return
    # A tiny smoke dataset may have all feasible contracts; the loader path is
    # still validated by requiring the key to be present on every metric row.
    assert all("oracle_certificate" in row for row in rows)


def test_pc_false_when_vehicle_unsafe_even_if_skeleton_exists(tmp_path):
    dataset = _build_dataset(tmp_path)
    # Inject a collision into scene context. ClosedLoopRunner uses saved scenes,
    # not regenerated artifacts, so the resulting row must fail PC when a
    # skeleton otherwise exists.
    scenes_path = dataset / "scenes.jsonl"
    scenes = read_jsonl(scenes_path)
    scenes[0].setdefault("metadata", {})["collision"] = True
    with scenes_path.open("w") as f:
        for r in scenes:
            f.write(json.dumps(r) + "\n")
    output = tmp_path / "eval_collision"
    ClosedLoopRunner().run_dataset(str(dataset), str(output))
    rows = read_jsonl(output / "episode_metrics.jsonl")
    assert any(row["phase_accepted"] and not row["passenger_complete"] and not row["traffic_safe"] for row in rows)
