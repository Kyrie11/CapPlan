from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import torch

ROOT = Path(__file__).resolve().parents[1]


def _dataset(tmp_path: Path) -> Path:
    out = tmp_path / "dataset"
    subprocess.check_call([
        sys.executable, "scripts/build_dataset.py",
        "--scene_source", "synthetic",
        "--max_scenarios", "2",
        "--accessibility_source", "synthetic_local",
        "--num_contracts_per_scene", "2",
        "--output_dir", str(out),
        "--seed", "23",
    ], cwd=ROOT)
    return out


def test_casa_loader_compiles_each_contract_once_not_once_per_transition(tmp_path, monkeypatch):
    from capplan.models import casa_dataset as cd

    dataset = _dataset(tmp_path)
    calls = {"n": 0}
    original = cd.CapabilityCompiler.compile

    def wrapped(self, *args, **kwargs):
        calls["n"] += 1
        return original(self, *args, **kwargs)

    monkeypatch.setattr(cd.CapabilityCompiler, "compile", wrapped)
    ds = cd.CASADataset(dataset, "train", feature_policy="legacy")
    assert ds.samples
    # 2 episodes x 2 passenger contracts. The old loader compiled once for
    # every (transition, passenger) sample and would be orders of magnitude larger.
    assert calls["n"] == 4


def test_learned_torch_predictor_batches_all_transitions_in_one_forward():
    from capplan.data.accessibility_layer import synthetic_accessibility_graph
    from capplan.data.pudo_interface_layer import synthetic_pudo_anchors, synthetic_vehicle_interface
    from capplan.models.casa_features import FeatureVocab, encode_transition_with_capability
    from capplan.models.casa_torch import CASAHetGraphNet
    from capplan.models.predictors import LearnedLinearTransitionPredictor
    from capplan.planning.transition_generator import TransitionGenerator

    eid = "rf10_batch"
    graph = synthetic_accessibility_graph(eid)
    transitions = TransitionGenerator().generate(
        eid, graph, synthetic_pudo_anchors(eid, graph=graph), synthetic_vehicle_interface(eid)
    )[:10]
    vocab = FeatureVocab()
    input_dim = len(encode_transition_with_capability(transitions[0], [], vocab, feature_policy="paper_safe_v2"))
    model = CASAHetGraphNet(input_dim, len(vocab.phases), len(vocab.resources), model_type="relation_mlp")
    ckpt = {
        "vocab": vocab.to_dict(),
        "input_dim": input_dim,
        "num_phases": len(vocab.phases),
        "num_resources": len(vocab.resources),
        "torch_state_dict": model.state_dict(),
        "weights": {"mean": [0.0] * input_dim, "std": [1.0] * input_dim},
        "config": {"model_type": "relation_mlp", "feature_policy": "paper_safe_v2"},
    }
    predictor = LearnedLinearTransitionPredictor(ckpt, device="cpu")
    count = {"n": 0}
    original_forward = predictor._torch_model.forward

    def counted(*args, **kwargs):
        count["n"] += 1
        return original_forward(*args, **kwargs)

    predictor._torch_model.forward = counted
    out = predictor.predict(transitions, context={"tokens": []})
    assert len(out) == len(transitions)
    assert count["n"] == 1


def test_training_and_eval_cli_expose_cuda_progress_controls():
    train = (ROOT / "scripts/train_casa.py").read_text(encoding="utf-8")
    eval_py = (ROOT / "scripts/run_closed_loop_eval.py").read_text(encoding="utf-8")
    for token in ["--amp", "--tf32", "--fused_adamw", "--torch_compile", "--progress", "--eval_preload_max_mb"]:
        assert token in train
    for token in ["--casa_device", "--progress", "--progress_update_interval"]:
        assert token in eval_py
