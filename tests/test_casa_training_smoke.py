import json
import subprocess
import sys
from pathlib import Path

from capplan.data.accessibility_layer import synthetic_accessibility_graph
from capplan.data.capability_contracts import default_contract
from capplan.data.pudo_interface_layer import synthetic_pudo_anchors, synthetic_vehicle_interface
from capplan.models.casa_net import CASAInput, CASANet
from capplan.planning.transition_generator import TransitionGenerator

ROOT = Path(__file__).resolve().parents[1]


def _dataset(tmp_path):
    out = tmp_path / "dataset"
    subprocess.check_call([
        sys.executable, "scripts/build_dataset.py",
        "--scene_source", "synthetic",
        "--max_scenarios", "1",
        "--accessibility_source", "synthetic_local",
        "--num_contracts_per_scene", "1",
        "--output_dir", str(out),
        "--seed", "5",
    ], cwd=ROOT)
    return out


def test_train_casa_smoke_writes_checkpoint_with_synthetic_dataset(tmp_path):
    dataset = _dataset(tmp_path)
    model = tmp_path / "model"
    subprocess.check_call([
        sys.executable, "scripts/train_casa.py",
        "--dataset_dir", str(dataset),
        "--output_dir", str(model),
        "--epochs", "1",
        "--batch_size", "2",
        "--device", "cpu",
    ], cwd=ROOT)
    for name in ["checkpoint.pt", "vocab.json", "config.json", "train_metrics.jsonl", "val_metrics.json"]:
        assert (model / name).exists()
    assert json.loads((model / "config.json").read_text())["mode"] == "learned"


def test_casa_prediction_has_all_transition_ids():
    eid = "casa"
    graph = synthetic_accessibility_graph(eid)
    anchors = synthetic_pudo_anchors(eid, graph=graph)
    vehicle = synthetic_vehicle_interface(eid)
    transitions = TransitionGenerator().generate(eid, graph, anchors, vehicle)
    out = CASANet(mode="heuristic_oracle_baseline")(CASAInput({}, [], {"origin": 1.0}, {}, transitions))
    assert set(out.transition_predictions) == {t.transition_id for t in transitions}
    assert out.audit_history[0]["mode"] == "heuristic_oracle_baseline"


def test_learned_mode_separate_from_heuristic_baseline():
    eid = "learned"
    graph = synthetic_accessibility_graph(eid)
    anchors = synthetic_pudo_anchors(eid, graph=graph)
    vehicle = synthetic_vehicle_interface(eid)
    transitions = TransitionGenerator().generate(eid, graph, anchors, vehicle)
    out = CASANet(mode="learned")(CASAInput({}, [], {"origin": 1.0}, {}, transitions))
    assert out.audit_history[0]["mode"] == "learned"
