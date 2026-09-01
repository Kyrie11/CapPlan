#!/usr/bin/env python
"""Evaluate learned CASA heads directly against frozen benchmark labels.

This separates neural-head quality from TSBS/search behavior.  It is deliberately
read-only: no dataset files are modified.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from capplan.models.casa_dataset import CASADataset, demand_scale_vector
from capplan.models.casa_features import FeatureVocab
from capplan.models.casa_torch import CASAHetGraphNet
from capplan.utils.serialization import dump_json

# Reuse the exact metric implementation used during training.
from train_casa import _metrics_from_predictions, _sigmoid, _softmax, _torch_predict_batched


def _load_checkpoint(path: Path):
    import torch
    payload = torch.load(path, map_location="cpu")
    if not isinstance(payload, dict):
        raise RuntimeError(f"invalid CASA checkpoint payload: {path}")
    return payload


def main() -> None:
    p = argparse.ArgumentParser(description="Evaluate CASA checkpoint heads on a frozen split.")
    p.add_argument("--dataset_dir", required=True)
    p.add_argument("--checkpoint", required=True)
    p.add_argument("--split", choices=["train", "val", "test"], default="test")
    p.add_argument("--device", default="cuda:0")
    p.add_argument("--batch_size", type=int, default=8192)
    p.add_argument("--output", required=True)
    p.add_argument("--progress", action=argparse.BooleanOptionalAction, default=True)
    args = p.parse_args()

    import torch

    ckpt = _load_checkpoint(Path(args.checkpoint))
    vocab_payload = ckpt.get("vocab") or {}
    vocab = FeatureVocab(**vocab_payload) if vocab_payload else FeatureVocab()
    cfg = ckpt.get("config") or {}
    feature_policy = str(cfg.get("feature_policy", "paper_safe_v2"))
    value_target = str(cfg.get("value_target", "skeleton"))
    ds = CASADataset(
        args.dataset_dir, args.split, vocab,
        value_target=value_target, feature_policy=feature_policy,
        show_progress=args.progress,
    )
    x, y_edge, y_value, y_phase, y_demand, mask, y_avail, beta = ds.arrays_for_training()
    weights = ckpt.get("weights") or {}
    mean = np.asarray(weights.get("mean"), dtype=np.float32)
    std = np.asarray(weights.get("std"), dtype=np.float32)
    if mean.shape != (x.shape[1],) or std.shape != (x.shape[1],):
        raise RuntimeError(f"checkpoint normalization shape mismatch: mean={mean.shape} x={x.shape}")
    xn = np.ascontiguousarray((x - mean) / np.maximum(std, 1e-6), dtype=np.float32)

    model = CASAHetGraphNet(
        int(ckpt.get("input_dim", x.shape[1])),
        int(ckpt.get("num_phases", len(vocab.phases))),
        int(ckpt.get("num_resources", len(vocab.resources))),
        model_type=str(cfg.get("model_type", "relation_mlp")),
    )
    model.load_state_dict(ckpt["torch_state_dict"], strict=False)
    device = args.device
    if str(device).startswith("cuda") and not torch.cuda.is_available():
        raise RuntimeError(f"CUDA requested ({device}) but unavailable")
    model.to(device).eval()

    pred = _torch_predict_batched(
        model, xn, batch_size=args.batch_size, device=device,
        amp_mode="off", show_progress=args.progress,
        desc=f"CASA {args.split} checkpoint eval", preload_max_mb=2048,
    )
    edge_prob = _sigmoid(pred["edge_logits"])
    value_prob = pred["value"]
    phase_prob = _softmax(pred["phase_logits"])
    demand_pred = pred["typed_demand"]
    avail_pred = pred["availability"]
    unc_pred = pred["uncertainty"]
    pos = float(np.sum(y_edge > 0.5))
    neg = float(len(y_edge) - pos)
    edge_pos_weight = float(cfg.get("edge_pos_weight_resolved", neg / max(pos, 1.0)))
    scales = np.asarray(demand_scale_vector(vocab.resources), dtype=np.float32)
    metrics = _metrics_from_predictions(
        edge_prob, y_edge, value_prob, y_value, phase_prob, y_phase,
        demand_pred, y_demand, mask, edge_pos_weight,
        str(ckpt.get("mode", "learned")), device, len(x),
        uncertainty_pred=unc_pred, uncertainty_beta=beta,
        availability_pred=avail_pred, availability_target=y_avail,
        demand_scale=scales, resource_names=vocab.resources,
    )
    metrics.update({
        "split": args.split,
        "num_samples": int(len(x)),
        "edge_positive_rate": float(np.mean(y_edge > 0.5)) if len(y_edge) else 0.0,
        "value_positive_rate": float(np.mean(y_value > 0.5)) if len(y_value) else 0.0,
        "checkpoint": str(Path(args.checkpoint).resolve()),
        "feature_policy": feature_policy,
        "architecture_semantics": ckpt.get("architecture_semantics"),
        "true_heterogeneous_message_passing": bool(ckpt.get("true_heterogeneous_message_passing", False)),
    })
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    dump_json(output, metrics)
    print(json.dumps(metrics, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
