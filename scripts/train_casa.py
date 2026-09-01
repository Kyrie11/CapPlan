#!/usr/bin/env python
from __future__ import annotations

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

import argparse
import contextlib
import gc
import json
import math
import random
import time
from pathlib import Path
from typing import Any, Dict

import numpy as np

from capplan.models.casa_dataset import CASADataset, demand_scale_vector
from capplan.models.casa_features import FeatureVocab
from capplan.models.losses import casa_loss
from capplan.utils.serialization import dump_json, write_jsonl

CASA_TRAINING_RUNTIME_VERSION = "capplan_casa_cuda_perf_v1_20260901"


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def _softmax(z):
    z = z - np.max(z, axis=1, keepdims=True)
    e = np.exp(z)
    return e / np.maximum(np.sum(e, axis=1, keepdims=True), 1e-9)




def _balanced_sampling_probabilities(samples, *, profile_balanced: bool, action_balanced: bool):
    """Return inverse-frequency sampling probabilities for enabled strata.

    Profile is derived from the passenger binding suffix and action from the first
    stable feature slot (the action vocabulary index).  Enabling both samplers
    multiplies the two inverse-frequency weights before normalization.
    """
    if not samples or not (profile_balanced or action_balanced):
        return None, {"enabled": False}
    from collections import Counter
    profile_keys = [str(s.passenger_id).rsplit(":", 1)[-1] for s in samples]
    action_keys = [int(round(float(s.x[0]))) if s.x else -1 for s in samples]
    pc = Counter(profile_keys); ac = Counter(action_keys)
    weights = np.ones(len(samples), dtype=np.float64)
    if profile_balanced:
        weights *= np.array([1.0 / max(pc[k], 1) for k in profile_keys], dtype=np.float64)
    if action_balanced:
        weights *= np.array([1.0 / max(ac[k], 1) for k in action_keys], dtype=np.float64)
    probs = weights / np.maximum(weights.sum(), 1e-12)
    return probs.astype(np.float64), {
        "enabled": True,
        "profile_balanced": bool(profile_balanced),
        "action_balanced": bool(action_balanced),
        "num_profile_strata": len(pc),
        "num_action_strata": len(ac),
        "max_to_min_probability_ratio": float(probs.max() / max(probs.min(), 1e-12)),
    }

def _normalization_stats(x: np.ndarray, categorical_prefix: int = 3):
    """Feature normalization that preserves categorical relation ids.

    The first three CASA slots are action/source-phase/target-phase integer IDs
    consumed by relation embeddings. Normalizing them destroys their categorical
    semantics, so keep them in raw index space while standardizing continuous
    slots.
    """
    mean = x.mean(axis=0).astype(np.float32)
    std = (x.std(axis=0) + 1e-6).astype(np.float32)
    n = min(int(categorical_prefix), int(x.shape[1]))
    if n > 0:
        mean[:n] = 0.0
        std[:n] = 1.0
    return mean, std


def _average_precision(y_true: np.ndarray, y_score: np.ndarray) -> float:
    """Dependency-free average precision / PR-AUC summary."""
    y = np.asarray(y_true, dtype=bool)
    score = np.asarray(y_score, dtype=float)
    positives = int(np.sum(y))
    if positives <= 0:
        return 0.0
    order = np.argsort(-score, kind="mergesort")
    ys = y[order]
    tp = np.cumsum(ys.astype(np.int64))
    ranks = np.arange(1, len(ys) + 1, dtype=np.float64)
    precision = tp / ranks
    return float(np.sum(precision[ys]) / positives)


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


def _metrics_from_predictions(edge_prob, y_edge, value_prob, y_value, phase_prob, y_phase, demand_pred, y_demand, demand_mask, edge_pos_weight: float, mode: str, device: str, num_val: int, uncertainty_pred=None, uncertainty_beta=None, availability_pred=None, availability_target=None, demand_scale=None, resource_names=None) -> Dict[str, Any]:
    if uncertainty_pred is None:
        uncertainty_pred = np.ones_like(demand_pred, dtype=np.float32) * 0.1
    losses = casa_loss(
        edge_prob, y_edge, value_prob, y_value,
        uncertainty=uncertainty_pred,
        uncertainty_beta=uncertainty_beta,
        phase_pred=phase_prob, phase_target=y_phase,
        demand_pred=demand_pred, demand_target=y_demand, demand_mask=demand_mask,
        demand_scale=demand_scale,
    )
    pred_edge_binary = edge_prob >= 0.5
    true_edge_binary = y_edge >= 0.5
    tp = int(np.sum(pred_edge_binary & true_edge_binary))
    fp = int(np.sum(pred_edge_binary & ~true_edge_binary))
    tn = int(np.sum(~pred_edge_binary & ~true_edge_binary))
    fn = int(np.sum(~pred_edge_binary & true_edge_binary))
    precision = tp / max(tp + fp, 1)
    recall = tp / max(tp + fn, 1)
    specificity = tn / max(tn + fp, 1)
    f1 = (2 * precision * recall / max(precision + recall, 1e-9))
    if availability_pred is not None and availability_target is not None:
        losses["L_availability"] = float(np.mean((availability_pred - availability_target) ** 2))
        losses["availability_mae"] = float(np.mean(np.abs(availability_pred - availability_target)))

    value_true = np.asarray(y_value) >= 0.5
    value_pred_binary = np.asarray(value_prob) >= 0.5
    value_tp = int(np.sum(value_pred_binary & value_true)); value_fp = int(np.sum(value_pred_binary & ~value_true)); value_fn = int(np.sum(~value_pred_binary & value_true))
    value_precision = value_tp / max(value_tp + value_fp, 1); value_recall = value_tp / max(value_tp + value_fn, 1)
    value_f1 = 2 * value_precision * value_recall / max(value_precision + value_recall, 1e-9)

    extra: Dict[str, Any] = {}
    if demand_scale is not None and resource_names is not None:
        sc = np.asarray(demand_scale, dtype=float).reshape(-1)
        for j, name in enumerate(list(resource_names)):
            if j >= demand_mask.shape[1] or j >= len(sc):
                break
            observed = demand_mask[:, j] > 0.5
            if not np.any(observed):
                continue
            extra[f"demand_nmae::{name}"] = float(np.mean(np.abs(demand_pred[observed, j] - y_demand[observed, j]) / max(abs(float(sc[j])), 1e-9)))
        if uncertainty_pred is not None and uncertainty_beta is not None:
            sc2 = np.asarray(demand_scale, dtype=float).reshape(1, -1)
            residual_n = np.abs(demand_pred - y_demand) / np.maximum(np.abs(sc2), 1e-9)
            sigma_n = np.asarray(uncertainty_pred, dtype=float) / np.maximum(np.abs(sc2), 1e-9)
            covered = residual_n <= np.maximum(np.asarray(uncertainty_beta, dtype=float), 0.0) * sigma_n
            observed = demand_mask > 0.5
            extra["uncertainty_empirical_coverage"] = float(np.mean(covered[observed])) if np.any(observed) else 0.0

    return {
        **losses,
        "edge_accuracy": float(np.mean(pred_edge_binary == true_edge_binary)),
        "edge_balanced_accuracy": float(0.5 * (recall + specificity)),
        "edge_precision": float(precision),
        "edge_recall": float(recall),
        "edge_f1": float(f1),
        "edge_auprc": _average_precision(true_edge_binary, edge_prob),
        "edge_true_positive_rate": float(np.mean(true_edge_binary)),
        "edge_pred_positive_rate": float(np.mean(pred_edge_binary)),
        "value_auprc": _average_precision(value_true, value_prob),
        "value_precision": float(value_precision),
        "value_recall": float(value_recall),
        "value_f1": float(value_f1),
        "value_true_positive_rate": float(np.mean(value_true)),
        "edge_pos_weight": float(edge_pos_weight),
        "num_val_samples": int(num_val),
        "mode": mode,
        "device": device,
        **extra,
    }


def _train_numpy(args, x, y_edge, y_value, y_phase, y_demand, demand_mask, y_availability, uncertainty_beta, xv, yv_edge, yv_value, yv_phase, yv_demand, vmask, yv_availability, v_uncertainty_beta, edge_pos_weight, vocab, out, device, sample_probs=None):
    input_dim = x.shape[1]
    mean, std = _normalization_stats(x)
    xn = (x - mean) / std
    xvn = (xv - mean) / std
    n_phase = len(vocab.phases)
    n_res = len(vocab.resources)
    demand_scale = np.asarray(demand_scale_vector(vocab.resources), dtype=np.float32)
    W_edge = np.zeros(input_dim, dtype=np.float32); b_edge = np.float32(0.0)
    W_value = np.zeros(input_dim, dtype=np.float32); b_value = np.float32(0.0)
    W_phase = np.zeros((input_dim, n_phase), dtype=np.float32); b_phase = np.zeros(n_phase, dtype=np.float32)
    W_demand = np.zeros((input_dim, n_res), dtype=np.float32); b_demand = np.zeros(n_res, dtype=np.float32)
    W_availability = np.zeros(input_dim, dtype=np.float32); b_availability = np.float32(0.0)
    metrics_rows = []
    for epoch in range(1, args.epochs + 1):
        if sample_probs is None:
            idx = np.arange(len(xn)); np.random.shuffle(idx)
        else:
            idx = np.random.choice(len(xn), size=len(xn), replace=True, p=sample_probs)
        for start in range(0, len(idx), max(1, args.batch_size)):
            batch = idx[start:start + max(1, args.batch_size)]
            xb = xn[batch]
            ye = y_edge[batch]; yv = y_value[batch]; yp = y_phase[batch]
            yd = y_demand[batch]; m = demand_mask[batch]; ya = y_availability[batch]
            pe = _sigmoid(xb @ W_edge + b_edge)
            pv = _sigmoid(xb @ W_value + b_value)
            pa = _sigmoid(xb @ W_availability + b_availability)
            pp = _softmax(xb @ W_phase + b_phase)
            pd = xb @ W_demand + b_demand
            edge_w = np.where(ye >= 0.5, edge_pos_weight, 1.0).astype(np.float32)
            ge = (pe - ye) * edge_w / max(float(np.sum(edge_w)), 1.0)
            gv = (pv - yv) / len(batch)
            gp = pp
            gp[np.arange(len(batch)), np.clip(yp, 0, n_phase - 1)] -= 1.0
            gp /= len(batch)
            denom = max(float(np.sum(m)), 1.0)
            norm_err = (pd - yd) / demand_scale[None, :]
            huber_grad = np.where(np.abs(norm_err) <= 1.0, norm_err, np.sign(norm_err))
            gd = huber_grad * m / demand_scale[None, :] / denom
            ga = (pa - ya) / len(batch)
            W_edge -= args.lr * (xb.T @ ge); b_edge -= args.lr * ge.sum()
            W_value -= args.lr * (xb.T @ gv); b_value -= args.lr * gv.sum()
            if args.predict_availability:
                W_availability -= args.lr * (xb.T @ ga); b_availability -= args.lr * ga.sum()
            if args.phase_supervision:
                W_phase -= args.lr * (xb.T @ gp); b_phase -= args.lr * gp.sum(axis=0)
            if args.predict_typed_demand:
                W_demand -= args.lr * (xb.T @ gd); b_demand -= args.lr * gd.sum(axis=0)
        epoch_uncertainty = np.ones_like(y_demand, dtype=np.float32) * 0.1
        epoch_losses = casa_loss(_sigmoid(xn @ W_edge + b_edge), y_edge, _sigmoid(xn @ W_value + b_value), y_value, uncertainty=epoch_uncertainty, uncertainty_beta=uncertainty_beta, phase_pred=_softmax(xn @ W_phase + b_phase), phase_target=y_phase, demand_pred=xn @ W_demand + b_demand, demand_target=y_demand, demand_mask=demand_mask, demand_scale=demand_scale)
        epoch_losses["L_availability"] = float(np.mean((_sigmoid(xn @ W_availability + b_availability) - y_availability) ** 2))
        metrics_rows.append({"epoch": epoch, **epoch_losses})
    val_edge = _sigmoid(xvn @ W_edge + b_edge)
    val_value = _sigmoid(xvn @ W_value + b_value)
    val_phase = _softmax(xvn @ W_phase + b_phase)
    val_demand = xvn @ W_demand + b_demand
    val_availability = _sigmoid(xvn @ W_availability + b_availability)
    val_uncertainty = np.ones_like(val_demand, dtype=np.float32) * 0.1
    val_metrics = _metrics_from_predictions(val_edge, yv_edge, val_value, yv_value, val_phase, yv_phase, val_demand, yv_demand, vmask, edge_pos_weight, args.casa_mode, device, len(xv), uncertainty_pred=val_uncertainty, uncertainty_beta=v_uncertainty_beta, availability_pred=val_availability, availability_target=yv_availability, demand_scale=demand_scale, resource_names=vocab.resources)
    checkpoint = {
        "mode": args.casa_mode,
        "model_type": "linear_smoke" if args.model_type == "linear_smoke" else f"{args.model_type}_numpy_surrogate",
        "weights": {"W_edge": W_edge.tolist(), "b_edge": float(b_edge), "W_value": W_value.tolist(), "b_value": float(b_value), "W_phase": W_phase.tolist(), "b_phase": b_phase.tolist(), "W_demand": W_demand.tolist(), "b_demand": b_demand.tolist(), "W_availability": W_availability.tolist(), "b_availability": float(b_availability), "mean": mean.tolist(), "std": std.tolist()},
        "input_dim": int(input_dim), "vocab": vocab.to_dict(), "demand_normalizers": {name: float(scale) for name, scale in zip(vocab.resources, demand_scale)}, "config": {**vars(args), "edge_pos_weight_resolved": float(edge_pos_weight)},
    }
    _save_checkpoint(out / "checkpoint.pt", checkpoint)
    return metrics_rows, val_metrics, checkpoint


def _autocast_context(torch, device: str, amp_mode: str):
    if not str(device).startswith("cuda") or amp_mode == "off":
        return contextlib.nullcontext()
    dtype = torch.bfloat16 if amp_mode == "bf16" else torch.float16
    return torch.autocast(device_type="cuda", dtype=dtype)


def _torch_predict_batched(
    model,
    x_np: np.ndarray,
    *,
    batch_size: int,
    device: str,
    amp_mode: str = "off",
    show_progress: bool = True,
    desc: str = "CASA validation",
    preload_max_mb: int = 2048,
):
    """Memory-bounded, progress-aware inference for validation arrays.

    On CUDA, validation features are preloaded once when they fit the configured
    budget, avoiding one host->device allocation/copy for every validation batch.
    If preload fails (e.g. GPU memory pressure), the function falls back to
    chunked transfers without changing prediction semantics.
    """
    import torch
    from tqdm.auto import tqdm

    outputs: Dict[str, list[np.ndarray]] = {
        "edge_logits": [], "value": [], "availability": [], "phase_logits": [],
        "typed_demand": [], "uncertainty": [],
    }
    bs = max(1, int(batch_size))
    was_training = bool(model.training)
    model.eval()
    x_contig = np.ascontiguousarray(x_np, dtype=np.float32)
    preload = None
    if str(device).startswith("cuda") and x_contig.nbytes <= max(0, int(preload_max_mb)) * 1024 * 1024:
        try:
            preload = torch.from_numpy(x_contig).to(device=device, non_blocking=False)
        except RuntimeError as exc:
            if "out of memory" in str(exc).lower():
                try:
                    torch.cuda.empty_cache()
                except Exception:
                    pass
                preload = None
            else:
                raise

    starts = range(0, len(x_contig), bs)
    bar = tqdm(
        starts, total=math.ceil(len(x_contig) / bs) if len(x_contig) else 0,
        desc=desc, unit="batch", dynamic_ncols=True, disable=not show_progress,
    )
    with torch.inference_mode():
        for start in bar:
            if preload is not None:
                xb = preload[start:start + bs]
            else:
                xb = torch.from_numpy(x_contig[start:start + bs]).to(device=device, non_blocking=False)
            with _autocast_context(torch, device, amp_mode):
                pred = model(xb)
            for key in outputs:
                outputs[key].append(pred[key].detach().float().cpu().numpy())
    del preload
    if str(device).startswith("cuda"):
        try:
            torch.cuda.empty_cache()
        except Exception:
            pass
    if was_training:
        model.train()
    return {key: np.concatenate(chunks, axis=0) if chunks else np.empty((0,), dtype=np.float32) for key, chunks in outputs.items()}


def _resolve_fused_adamw(torch, device: str, mode: str) -> bool:
    if mode == "off" or not str(device).startswith("cuda"):
        return False
    if mode == "on":
        return True
    # auto: use fused AdamW only when the installed torch exposes it.
    try:
        import inspect
        return "fused" in inspect.signature(torch.optim.AdamW).parameters
    except Exception:
        return False


def _train_torch(args, x, y_edge, y_value, y_phase, y_demand, demand_mask, y_availability, uncertainty_beta, xv, yv_edge, yv_value, yv_phase, yv_demand, vmask, yv_availability, v_uncertainty_beta, edge_pos_weight, vocab, out, device, sample_probs=None):
    import torch
    import torch.nn.functional as F
    from tqdm.auto import tqdm
    from capplan.models.casa_torch import CASAHetGraphNet

    torch.manual_seed(int(args.seed))
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(int(args.seed))

    input_dim = x.shape[1]
    mean, std = _normalization_stats(x)
    xn = np.ascontiguousarray((x - mean) / std, dtype=np.float32)
    xvn = np.ascontiguousarray((xv - mean) / std, dtype=np.float32)

    if str(device).startswith("cuda"):
        if not torch.cuda.is_available():
            raise RuntimeError(f"CUDA requested via --device {device}, but torch.cuda.is_available() is False")
        if ":" in str(device):
            torch.cuda.set_device(int(str(device).split(":", 1)[1]))
        torch.backends.cuda.matmul.allow_tf32 = bool(args.tf32)
        try:
            torch.set_float32_matmul_precision(args.matmul_precision)
        except Exception:
            pass
        props = torch.cuda.get_device_properties(torch.device(device))
        print(
            f"[CAPPLAN_CUDA] device={device} name={props.name} memory_gb={props.total_memory/1024**3:.1f} "
            f"amp={args.amp} tf32={bool(args.tf32)} matmul_precision={args.matmul_precision}"
        )

    base_model = CASAHetGraphNet(input_dim, len(vocab.phases), len(vocab.resources), model_type=args.model_type).to(device)
    model = base_model
    if args.torch_compile:
        if not hasattr(torch, "compile"):
            raise RuntimeError("--torch_compile requires torch.compile support (PyTorch >= 2.0)")
        try:
            model = torch.compile(base_model, mode=args.compile_mode)
            print(f"[CAPPLAN_CUDA] torch.compile enabled mode={args.compile_mode}")
        except Exception as exc:
            print(f"[CAPPLAN_CUDA] WARNING torch.compile failed; falling back to eager mode: {exc}")
            model = base_model

    fused = _resolve_fused_adamw(torch, device, args.fused_adamw)
    try:
        opt = torch.optim.AdamW(base_model.parameters(), lr=args.lr, fused=fused)
    except TypeError:
        fused = False
        opt = torch.optim.AdamW(base_model.parameters(), lr=args.lr)
    print(f"[CAPPLAN_CUDA] AdamW fused={fused}")

    # The current four-city benchmark comfortably fits on one A30. Keeping the
    # training tensors resident on one GPU avoids DataLoader/process/PCIe overhead
    # and is faster for this small relation-MLP than DDP.
    X = torch.from_numpy(xn).to(device=device)
    Ye = torch.from_numpy(np.asarray(y_edge, dtype=np.float32)).to(device=device)
    Yv = torch.from_numpy(np.asarray(y_value, dtype=np.float32)).to(device=device)
    Yp = torch.from_numpy(np.asarray(y_phase, dtype=np.int64)).to(device=device)
    Yd = torch.from_numpy(np.asarray(y_demand, dtype=np.float32)).to(device=device)
    M = torch.from_numpy(np.asarray(demand_mask, dtype=np.float32)).to(device=device)
    Ya = torch.from_numpy(np.asarray(y_availability, dtype=np.float32)).to(device=device)
    B = torch.from_numpy(np.asarray(uncertainty_beta, dtype=np.float32)).to(device=device)
    demand_scale_np = np.asarray(demand_scale_vector(vocab.resources), dtype=np.float32)
    demand_scale = torch.from_numpy(demand_scale_np).to(device=device).view(1, -1)
    metrics_rows = []
    pos_weight = torch.tensor(float(edge_pos_weight), dtype=torch.float32, device=device)
    sampling_probs = torch.from_numpy(np.asarray(sample_probs, dtype=np.float32)).to(device=device) if sample_probs is not None else None
    scaler = torch.amp.GradScaler("cuda", enabled=(str(device).startswith("cuda") and args.amp == "fp16"))

    loss_names = ["L_edge", "L_value", "L_phase", "L_demand", "L_cal", "L_availability", "L_total"]
    bs = max(1, int(args.batch_size))
    num_steps = math.ceil(X.shape[0] / bs)
    training_t0 = time.perf_counter()

    for epoch in range(1, args.epochs + 1):
        model.train()
        if sampling_probs is None:
            idx = torch.randperm(X.shape[0], device=device)
        else:
            idx = torch.multinomial(sampling_probs, X.shape[0], replacement=True)
        # Keep loss accounting on GPU; the reviewfix9 implementation copied seven
        # scalars to CPU on every batch, forcing repeated CUDA synchronizations.
        epoch_sum = torch.zeros(len(loss_names), dtype=torch.float64, device=device)
        epoch_weight = 0.0
        epoch_t0 = time.perf_counter()
        starts = range(0, len(idx), bs)
        bar = tqdm(
            starts, total=num_steps, desc=f"CASA train {epoch}/{args.epochs}",
            unit="batch", dynamic_ncols=True, disable=not args.progress,
        )
        for step, start in enumerate(bar, start=1):
            b = idx[start:start + bs]
            with _autocast_context(torch, device, args.amp):
                outp = model(X[b])
                edge_loss = F.binary_cross_entropy_with_logits(outp["edge_logits"], Ye[b], pos_weight=pos_weight)
                value_loss = F.binary_cross_entropy(outp["value"], Yv[b])
                zero = outp["edge_logits"].sum() * 0.0
                phase_loss = F.cross_entropy(outp["phase_logits"], Yp[b]) if args.phase_supervision else zero
                if args.predict_typed_demand:
                    norm_pred = outp["typed_demand"] / demand_scale
                    norm_target = Yd[b] / demand_scale
                    per_resource_huber = F.smooth_l1_loss(norm_pred, norm_target, reduction="none")
                    demand_loss = (per_resource_huber * M[b]).sum() / torch.clamp(M[b].sum(), min=1.0)
                else:
                    demand_loss = zero
                availability_loss = F.mse_loss(outp["availability"], Ya[b]) if args.predict_availability else zero
                sigma = outp["uncertainty"]
                observed = M[b]
                denom_cal = torch.clamp(observed.sum(), min=1.0)
                residual_norm = torch.abs(outp["typed_demand"] - Yd[b]) / demand_scale
                sigma_norm = sigma / demand_scale
                if args.predict_uncertainty and args.predict_typed_demand:
                    cal_loss = (torch.relu(residual_norm - B[b] * sigma_norm) * observed).sum() / denom_cal
                    cal_loss = cal_loss + 0.001 * (sigma_norm * observed).sum() / denom_cal
                else:
                    cal_loss = zero
                loss = edge_loss + value_loss + phase_loss + demand_loss + cal_loss + availability_loss

            opt.zero_grad(set_to_none=True)
            if scaler.is_enabled():
                scaler.scale(loss).backward()
                scaler.step(opt)
                scaler.update()
            else:
                loss.backward()
                opt.step()

            w = float(b.numel())
            epoch_weight += w
            loss_vec = torch.stack([
                edge_loss, value_loss, phase_loss, demand_loss, cal_loss, availability_loss, loss
            ]).detach().to(dtype=torch.float64)
            epoch_sum += loss_vec * w

            if args.progress and (step == 1 or step % max(1, args.progress_update_interval) == 0 or step == num_steps):
                # One synchronization per display update, rather than seven per batch.
                lv = loss_vec.float().cpu().tolist()
                samples_done = min(start + bs, len(idx))
                elapsed = max(time.perf_counter() - epoch_t0, 1e-9)
                postfix = {
                    "loss": f"{lv[-1]:.4f}",
                    "edge": f"{lv[0]:.4f}",
                    "value": f"{lv[1]:.4f}",
                    "demand": f"{lv[3]:.4f}",
                    "samples/s": f"{samples_done/elapsed:,.0f}",
                }
                if str(device).startswith("cuda"):
                    postfix["memGB"] = f"{torch.cuda.memory_allocated(device)/1024**3:.2f}"
                bar.set_postfix(postfix, refresh=False)

        epoch_vals = (epoch_sum / max(epoch_weight, 1.0)).float().cpu().tolist()
        epoch_losses = dict(zip(loss_names, map(float, epoch_vals)))
        epoch_seconds = time.perf_counter() - epoch_t0
        metrics_rows.append({
            "epoch": epoch, **epoch_losses,
            "epoch_seconds": float(epoch_seconds),
            "samples_per_second": float(len(idx) / max(epoch_seconds, 1e-9)),
        })
        tqdm.write(
            f"[CAPPLAN_CASA_TRAIN] epoch={epoch}/{args.epochs} "
            f"L_edge={epoch_losses['L_edge']:.6f} L_value={epoch_losses['L_value']:.6f} "
            f"L_demand={epoch_losses['L_demand']:.6f} L_cal={epoch_losses['L_cal']:.6f} "
            f"L_total={epoch_losses['L_total']:.6f} time={epoch_seconds:.1f}s "
            f"throughput={len(idx)/max(epoch_seconds,1e-9):,.0f} samples/s"
        )

    print(f"[CAPPLAN_CASA_TRAIN] optimization_done seconds={time.perf_counter()-training_t0:.1f}; starting validation")
    predv = _torch_predict_batched(
        model, xvn, batch_size=args.eval_batch_size, device=device, amp_mode=args.amp,
        show_progress=args.progress, desc="CASA validation", preload_max_mb=args.eval_preload_max_mb,
    )
    val_edge = _sigmoid(predv["edge_logits"])
    val_value = predv["value"]
    val_phase = _softmax(predv["phase_logits"])
    val_demand = predv["typed_demand"]
    val_availability = predv["availability"]
    val_uncertainty = predv["uncertainty"]
    val_metrics = _metrics_from_predictions(val_edge, yv_edge, val_value, yv_value, val_phase, yv_phase, val_demand, yv_demand, vmask, edge_pos_weight, args.casa_mode, device, len(xv), uncertainty_pred=val_uncertainty, uncertainty_beta=v_uncertainty_beta, availability_pred=val_availability, availability_target=yv_availability, demand_scale=demand_scale_np, resource_names=vocab.resources)
    checkpoint = {
        "mode": args.casa_mode,
        "training_runtime_version": CASA_TRAINING_RUNTIME_VERSION,
        "model_type": f"casa_{args.model_type}_multihead",
        "architecture_semantics": "relation_aware_transition_mlp_surrogate",
        "true_heterogeneous_message_passing": False,
        "torch_state_dict": base_model.state_dict(),
        "weights": {"mean": mean.tolist(), "std": std.tolist()},
        "input_dim": int(input_dim), "num_phases": len(vocab.phases), "num_resources": len(vocab.resources), "vocab": vocab.to_dict(), "demand_normalizers": {name: float(scale) for name, scale in zip(vocab.resources, demand_scale_np)}, "config": {**vars(args), "edge_pos_weight_resolved": float(edge_pos_weight), "fused_adamw_resolved": bool(fused)},
    }
    _save_checkpoint(out / "checkpoint.pt", checkpoint)
    return metrics_rows, val_metrics, checkpoint

def main() -> None:
    p = argparse.ArgumentParser(description="Train CASA-Net learned edge/value/demand/phase predictors.")
    p.add_argument("--dataset_dir", required=True)
    p.add_argument("--output_dir", required=True)
    p.add_argument("--epochs", type=int, default=20)
    p.add_argument("--batch_size", type=int, default=1024)
    p.add_argument("--lr", type=float, default=1e-3)
    p.add_argument("--eval_batch_size", type=int, default=8192, help="Chunk size for validation inference.")
    p.add_argument("--seed", type=int, default=13)
    p.add_argument("--device", default="auto", help="Training device, e.g. cuda:0. For the current small relation_mlp, one GPU per seed is preferred over DDP.")
    p.add_argument("--amp", choices=["off", "bf16", "fp16"], default="off", help="Optional CUDA autocast. off preserves the FP32 baseline; bf16 is the recommended A30 speed mode after confirming metric parity.")
    p.add_argument("--tf32", action=argparse.BooleanOptionalAction, default=False, help="Allow TF32 matmul on CUDA. Disabled by default for closest FP32 reproducibility.")
    p.add_argument("--matmul_precision", choices=["highest", "high", "medium"], default="highest")
    p.add_argument("--fused_adamw", choices=["auto", "on", "off"], default="auto", help="Use fused CUDA AdamW when supported; auto enables it on CUDA.")
    p.add_argument("--torch_compile", action="store_true", help="Optionally compile the tiny CASA surrogate with torch.compile; eager remains the compatibility default.")
    p.add_argument("--compile_mode", choices=["default", "reduce-overhead", "max-autotune"], default="reduce-overhead")
    p.add_argument("--progress", action=argparse.BooleanOptionalAction, default=True, help="Show TQDM data/training/validation progress.")
    p.add_argument("--progress_update_interval", type=int, default=20, help="Refresh loss/throughput postfix every N training batches.")
    p.add_argument("--eval_preload_max_mb", type=int, default=2048, help="Preload validation features to CUDA when they fit this budget; otherwise stream batches.")
    p.add_argument("--casa_mode", choices=["learned", "heuristic_oracle_baseline"], default="learned")
    p.add_argument("--model_type", choices=["relation_mlp", "hgt", "rgcn", "linear_smoke"], default="linear_smoke")
    p.add_argument("--paper_mode", action="store_true")
    p.add_argument("--feature_policy", choices=["auto", "legacy", "paper_safe", "paper_safe_v2"], default="auto", help="Feature masking policy. paper_safe_v2 removes label-derived target slots while preserving structural action/phase/cost relations; the current model remains a relation-MLP surrogate, not true HGT/R-GCN.")
    p.add_argument("--allow_relation_surrogate_paper_mode", action="store_true", help="Explicit acknowledgement that current hgt/rgcn modes are relation-aware transition MLP surrogates, not the heterogeneous message-passing CASA-Net described in the paper.")
    p.add_argument("--phase_supervision", action="store_true")
    p.add_argument("--predict_typed_demand", action="store_true")
    p.add_argument("--predict_uncertainty", action="store_true")
    p.add_argument("--predict_availability", action="store_true")
    p.add_argument("--value_target", choices=["offline_tsbs", "rollout", "skeleton"], default="skeleton")
    p.add_argument("--profile_balanced_sampler", action="store_true")
    p.add_argument("--action_balanced_sampler", action="store_true")
    p.add_argument("--save_calibration_report", action="store_true")
    p.add_argument("--edge_pos_weight", default="auto", help="Positive-class weight for sparse passenger edge labels. Use auto or a numeric value.")
    args = p.parse_args()
    if args.paper_mode and args.model_type == "linear_smoke":
        raise RuntimeError("paper_mode training cannot use linear_smoke")
    if args.paper_mode and args.model_type in {"relation_mlp", "hgt", "rgcn"} and not args.allow_relation_surrogate_paper_mode:
        raise RuntimeError(
            "paper_mode is publication-facing, but the current relation_mlp/hgt/rgcn backend is a relation-aware transition MLP surrogate, "
            "not true heterogeneous graph message passing. Use --feature_policy paper_safe_v2 without --paper_mode for baseline development, "
            "or pass --allow_relation_surrogate_paper_mode only when explicitly reporting it as a surrogate baseline."
        )
    if args.paper_mode:
        missing_flags = [
            name for name, enabled in {
                "--phase_supervision": args.phase_supervision,
                "--predict_typed_demand": args.predict_typed_demand,
                "--predict_uncertainty": args.predict_uncertainty,
                "--predict_availability": args.predict_availability,
            }.items() if not enabled
        ]
        if missing_flags:
            raise RuntimeError("paper_mode CASA training requires explicit heads: " + ", ".join(missing_flags))
        # The paper permits completion-value supervision from expert/audited
        # skeletons, offline TSBS, or closed-loop rollouts.  The selected target
        # must actually be materialized by CASADataset; offline/rollout modes now
        # fail closed if their explicit label files are absent.
    random.seed(args.seed); np.random.seed(args.seed)
    out = Path(args.output_dir); out.mkdir(parents=True, exist_ok=True)
    vocab = FeatureVocab()
    feature_policy = args.feature_policy
    if feature_policy == "auto":
        feature_policy = "paper_safe_v2" if args.paper_mode else "legacy"
    if args.paper_mode and feature_policy != "paper_safe_v2":
        raise RuntimeError("paper_mode requires --feature_policy paper_safe_v2 (or auto)")
    # Persist the resolved policy in checkpoints so inference uses the exact
    # same masking semantics as training.
    args.feature_policy = feature_policy
    if args.phase_supervision and feature_policy == "paper_safe_v2":
        print("WARNING: current relation_mlp phase head reconstructs a candidate-transition phase and is an auxiliary head, not the runtime service-phase belief model described by the paper.")
    print("[CAPPLAN_CASA_DATA] loading train split")
    train = CASADataset(args.dataset_dir, "train", vocab, value_target=args.value_target, feature_policy=feature_policy, show_progress=args.progress)
    print("[CAPPLAN_CASA_DATA] loading val split")
    val = CASADataset(args.dataset_dir, "val", vocab, value_target=args.value_target, feature_policy=feature_policy, show_progress=args.progress)
    if not train.samples:
        raise RuntimeError(f"no CASA training samples found in {args.dataset_dir}")
    if args.paper_mode and not train.split_file.exists():
        raise RuntimeError(f"paper_mode requires an explicit train split file: {train.split_file}")
    if args.paper_mode and (not val.split_file.exists() or not val.samples):
        raise RuntimeError(
            "paper_mode requires a non-empty, disjoint validation split in the same canonical dataset directory; "
            "merge abilitybench_av_train + abilitybench_av_val (+ test) before training instead of silently validating on train"
        )
    sample_probs, sampler_report = _balanced_sampling_probabilities(
        train.samples, profile_balanced=args.profile_balanced_sampler, action_balanced=args.action_balanced_sampler
    )
    num_train_samples = len(train.samples)
    num_val_samples = len(val.samples)
    x, y_edge, y_value, y_phase, y_demand, demand_mask, y_availability, uncertainty_beta = train.arrays_for_training()
    xv, yv_edge, yv_value, yv_phase, yv_demand, vmask, yv_availability, v_uncertainty_beta = val.arrays_for_training() if val.samples else train.arrays_for_training()
    # CASASample objects are very memory-heavy at ~1.6M train pairs. Once dense
    # arrays and sampler probabilities exist, release them before CUDA training.
    del train, val
    gc.collect()
    device = _device_auto(args.device)
    pos = float(np.sum(y_edge >= 0.5)); neg = float(len(y_edge) - pos)
    edge_pos_weight = (neg / max(pos, 1.0)) if str(args.edge_pos_weight).lower() == "auto" else max(0.0, float(args.edge_pos_weight))
    print(f"[CAPPLAN_CASA_DATA] train_samples={num_train_samples:,} val_samples={num_val_samples:,} input_dim={x.shape[1]} edge_positive_rate={pos/max(len(y_edge),1):.6f}")
    if args.model_type == "linear_smoke":
        metrics_rows, val_metrics, checkpoint = _train_numpy(args, x, y_edge, y_value, y_phase, y_demand, demand_mask, y_availability, uncertainty_beta, xv, yv_edge, yv_value, yv_phase, yv_demand, vmask, yv_availability, v_uncertainty_beta, edge_pos_weight, vocab, out, device, sample_probs=sample_probs)
    else:
        metrics_rows, val_metrics, checkpoint = _train_torch(args, x, y_edge, y_value, y_phase, y_demand, demand_mask, y_availability, uncertainty_beta, xv, yv_edge, yv_value, yv_phase, yv_demand, vmask, yv_availability, v_uncertainty_beta, edge_pos_weight, vocab, out, device, sample_probs=sample_probs)
    if args.paper_mode and (val_metrics.get("L_phase", 0.0) <= 0.0 or val_metrics.get("L_demand", 0.0) <= 0.0):
        raise RuntimeError(f"paper_mode requires non-zero L_phase and L_demand; got L_phase={val_metrics.get('L_phase')} L_demand={val_metrics.get('L_demand')}")
    dump_json(out / "vocab.json", vocab.to_dict())
    dump_json(out / "config.json", {"training_runtime_version": CASA_TRAINING_RUNTIME_VERSION, **vars(args), "edge_pos_weight_resolved": float(edge_pos_weight), "mode": args.casa_mode, "device_resolved": device, "input_dim": int(x.shape[1]), "feature_policy": feature_policy, "num_train_samples": int(num_train_samples), "num_val_samples": int(num_val_samples), "edge_train_positive_rate": float(np.mean(y_edge >= 0.5)), "model_type": checkpoint.get("model_type"), "architecture_semantics": checkpoint.get("architecture_semantics", "linear_smoke"), "true_heterogeneous_message_passing": bool(checkpoint.get("true_heterogeneous_message_passing", False)), "sampler_report": sampler_report, "relation_categorical_slots_unnormalized": 3, "phase_head_semantics": "candidate_transition_phase_auxiliary_not_runtime_phase_belief"})
    write_jsonl(out / "train_metrics.jsonl", metrics_rows)
    dump_json(out / "val_metrics.json", val_metrics)
    if args.save_calibration_report:
        dump_json(out / "calibration_report.json", {"L_cal": val_metrics.get("L_cal"), "uncertainty_empirical_coverage": val_metrics.get("uncertainty_empirical_coverage"), "calibration_semantics": "typed_demand_normalized_residual_coverage_beta_tau_sigma", "edge_true_positive_rate": val_metrics.get("edge_true_positive_rate"), "edge_pred_positive_rate": val_metrics.get("edge_pred_positive_rate"), "edge_auprc": val_metrics.get("edge_auprc"), "value_auprc": val_metrics.get("value_auprc")})
    print(f"wrote CASA checkpoint and metrics to {out}")
    print(val_metrics)


if __name__ == "__main__":
    main()
