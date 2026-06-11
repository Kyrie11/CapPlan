#!/usr/bin/env python
from __future__ import annotations

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

import argparse
import json
import random
from pathlib import Path
from typing import Any, Dict

import numpy as np

from capplan.models.casa_dataset import CASADataset
from capplan.models.casa_features import FeatureVocab
from capplan.models.losses import casa_loss
from capplan.utils.serialization import dump_json, write_jsonl


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def _device_auto(device: str) -> str:
    if device != "auto":
        return device
    try:
        import torch  # type: ignore
        return "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        return "cpu"


def _save_checkpoint(path: Path, payload: Dict[str, Any]) -> None:
    try:
        import torch  # type: ignore
        torch.save(payload, path)
    except Exception:
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main() -> None:
    p = argparse.ArgumentParser(description="Train CASA-Net learned edge/value predictors or the deterministic baseline metadata.")
    p.add_argument("--dataset_dir", required=True)
    p.add_argument("--output_dir", required=True)
    p.add_argument("--epochs", type=int, default=20)
    p.add_argument("--batch_size", type=int, default=16)
    p.add_argument("--lr", type=float, default=1e-3)
    p.add_argument("--seed", type=int, default=13)
    p.add_argument("--device", default="auto")
    p.add_argument("--casa_mode", choices=["learned", "heuristic_oracle_baseline"], default="learned")
    args = p.parse_args()

    random.seed(args.seed)
    np.random.seed(args.seed)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    vocab = FeatureVocab()
    train = CASADataset(args.dataset_dir, "train", vocab)
    val = CASADataset(args.dataset_dir, "val", vocab)
    if not train.samples:
        raise RuntimeError(f"no CASA training samples found in {args.dataset_dir}")
    x, y_edge, y_value, _ = train.arrays()
    xv, yv_edge, yv_value, _ = val.arrays() if val.samples else train.arrays()
    input_dim = x.shape[1]
    device = _device_auto(args.device)

    # Lightweight trainable NumPy model: shared linear features with edge and
    # value sigmoid heads.  This is a real supervised optimization path and keeps
    # torch optional for smoke tests.
    W_edge = np.zeros(input_dim, dtype=np.float32)
    b_edge = np.float32(0.0)
    W_value = np.zeros(input_dim, dtype=np.float32)
    b_value = np.float32(0.0)
    # Normalize features for stable small-data training.
    mean = x.mean(axis=0)
    std = x.std(axis=0) + 1e-6
    xn = (x - mean) / std
    xvn = (xv - mean) / std
    metrics_rows = []
    for epoch in range(1, args.epochs + 1):
        idx = np.arange(len(xn))
        np.random.shuffle(idx)
        for start in range(0, len(idx), max(1, args.batch_size)):
            batch = idx[start:start + max(1, args.batch_size)]
            xb = xn[batch]
            ye = y_edge[batch]
            yv = y_value[batch]
            pe = _sigmoid(xb @ W_edge + b_edge)
            pv = _sigmoid(xb @ W_value + b_value)
            ge = (pe - ye) / len(batch)
            gv = (pv - yv) / len(batch)
            W_edge -= args.lr * (xb.T @ ge)
            b_edge -= args.lr * ge.sum()
            W_value -= args.lr * (xb.T @ gv)
            b_value -= args.lr * gv.sum()
        train_edge = _sigmoid(xn @ W_edge + b_edge)
        train_value = _sigmoid(xn @ W_value + b_value)
        losses = casa_loss(train_edge, y_edge, train_value, y_value)
        row = {"epoch": epoch, **losses}
        metrics_rows.append(row)
    val_edge = _sigmoid(xvn @ W_edge + b_edge)
    val_value = _sigmoid(xvn @ W_value + b_value)
    val_losses = casa_loss(val_edge, yv_edge, val_value, yv_value)
    val_metrics = {
        **val_losses,
        "edge_accuracy": float(np.mean((val_edge >= 0.5) == (yv_edge >= 0.5))),
        "num_val_samples": int(len(xv)),
        "mode": args.casa_mode,
        "device": device,
    }
    checkpoint = {
        "mode": args.casa_mode,
        "model_type": "numpy_logistic_casa_smoke",
        "weights": {"W_edge": W_edge.tolist(), "b_edge": float(b_edge), "W_value": W_value.tolist(), "b_value": float(b_value), "mean": mean.tolist(), "std": std.tolist()},
        "input_dim": int(input_dim),
        "vocab": vocab.to_dict(),
        "config": vars(args),
    }
    _save_checkpoint(out / "checkpoint.pt", checkpoint)
    dump_json(out / "vocab.json", vocab.to_dict())
    dump_json(out / "config.json", {**vars(args), "mode": args.casa_mode, "device_resolved": device, "input_dim": int(input_dim), "num_train_samples": len(train.samples)})
    write_jsonl(out / "train_metrics.jsonl", metrics_rows)
    dump_json(out / "val_metrics.json", val_metrics)
    print(f"wrote CASA checkpoint and metrics to {out}")
    print(val_metrics)


if __name__ == "__main__":
    main()
