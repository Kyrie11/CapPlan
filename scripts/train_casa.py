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


def _metrics_from_predictions(edge_prob, y_edge, value_prob, y_value, phase_prob, y_phase, demand_pred, y_demand, demand_mask, edge_pos_weight: float, mode: str, device: str, num_val: int, uncertainty_pred=None, uncertainty_beta=None, availability_pred=None, availability_target=None) -> Dict[str, Any]:
    if uncertainty_pred is None:
        uncertainty_pred = np.ones_like(demand_pred, dtype=np.float32) * 0.1
    losses = casa_loss(
        edge_prob, y_edge, value_prob, y_value,
        uncertainty=uncertainty_pred,
        uncertainty_beta=uncertainty_beta,
        phase_pred=phase_prob, phase_target=y_phase,
        demand_pred=demand_pred, demand_target=y_demand, demand_mask=demand_mask,
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
    return {
        **losses,
        "edge_accuracy": float(np.mean(pred_edge_binary == true_edge_binary)),
        "edge_balanced_accuracy": float(0.5 * (recall + specificity)),
        "edge_precision": float(precision),
        "edge_recall": float(recall),
        "edge_f1": float(f1),
        "edge_true_positive_rate": float(np.mean(true_edge_binary)),
        "edge_pred_positive_rate": float(np.mean(pred_edge_binary)),
        "edge_pos_weight": float(edge_pos_weight),
        "num_val_samples": int(num_val),
        "mode": mode,
        "device": device,
    }


def _train_numpy(args, x, y_edge, y_value, y_phase, y_demand, demand_mask, y_availability, uncertainty_beta, xv, yv_edge, yv_value, yv_phase, yv_demand, vmask, yv_availability, v_uncertainty_beta, edge_pos_weight, vocab, out, device, sample_probs=None):
    input_dim = x.shape[1]
    mean = x.mean(axis=0)
    std = x.std(axis=0) + 1e-6
    xn = (x - mean) / std
    xvn = (xv - mean) / std
    n_phase = len(vocab.phases)
    n_res = len(vocab.resources)
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
            gd = 2.0 * (pd - yd) * m / denom
            ga = (pa - ya) / len(batch)
            W_edge -= args.lr * (xb.T @ ge); b_edge -= args.lr * ge.sum()
            W_value -= args.lr * (xb.T @ gv); b_value -= args.lr * gv.sum()
            W_availability -= args.lr * (xb.T @ ga); b_availability -= args.lr * ga.sum()
            W_phase -= args.lr * (xb.T @ gp); b_phase -= args.lr * gp.sum(axis=0)
            W_demand -= args.lr * (xb.T @ gd); b_demand -= args.lr * gd.sum(axis=0)
        epoch_uncertainty = np.ones_like(y_demand, dtype=np.float32) * 0.1
        epoch_losses = casa_loss(_sigmoid(xn @ W_edge + b_edge), y_edge, _sigmoid(xn @ W_value + b_value), y_value, uncertainty=epoch_uncertainty, uncertainty_beta=uncertainty_beta, phase_pred=_softmax(xn @ W_phase + b_phase), phase_target=y_phase, demand_pred=xn @ W_demand + b_demand, demand_target=y_demand, demand_mask=demand_mask)
        epoch_losses["L_availability"] = float(np.mean((_sigmoid(xn @ W_availability + b_availability) - y_availability) ** 2))
        metrics_rows.append({"epoch": epoch, **epoch_losses})
    val_edge = _sigmoid(xvn @ W_edge + b_edge)
    val_value = _sigmoid(xvn @ W_value + b_value)
    val_phase = _softmax(xvn @ W_phase + b_phase)
    val_demand = xvn @ W_demand + b_demand
    val_availability = _sigmoid(xvn @ W_availability + b_availability)
    val_uncertainty = np.ones_like(val_demand, dtype=np.float32) * 0.1
    val_metrics = _metrics_from_predictions(val_edge, yv_edge, val_value, yv_value, val_phase, yv_phase, val_demand, yv_demand, vmask, edge_pos_weight, args.casa_mode, device, len(xv), uncertainty_pred=val_uncertainty, uncertainty_beta=v_uncertainty_beta, availability_pred=val_availability, availability_target=yv_availability)
    checkpoint = {
        "mode": args.casa_mode,
        "model_type": "linear_smoke" if args.model_type == "linear_smoke" else f"{args.model_type}_numpy_surrogate",
        "weights": {"W_edge": W_edge.tolist(), "b_edge": float(b_edge), "W_value": W_value.tolist(), "b_value": float(b_value), "W_phase": W_phase.tolist(), "b_phase": b_phase.tolist(), "W_demand": W_demand.tolist(), "b_demand": b_demand.tolist(), "W_availability": W_availability.tolist(), "b_availability": float(b_availability), "mean": mean.tolist(), "std": std.tolist()},
        "input_dim": int(input_dim), "vocab": vocab.to_dict(), "config": {**vars(args), "edge_pos_weight_resolved": float(edge_pos_weight)},
    }
    _save_checkpoint(out / "checkpoint.pt", checkpoint)
    return metrics_rows, val_metrics, checkpoint


def _train_torch(args, x, y_edge, y_value, y_phase, y_demand, demand_mask, y_availability, uncertainty_beta, xv, yv_edge, yv_value, yv_phase, yv_demand, vmask, yv_availability, v_uncertainty_beta, edge_pos_weight, vocab, out, device, sample_probs=None):
    import torch
    import torch.nn.functional as F
    from capplan.models.casa_torch import CASAHetGraphNet
    input_dim = x.shape[1]
    mean = x.mean(axis=0); std = x.std(axis=0) + 1e-6
    xn = (x - mean) / std; xvn = (xv - mean) / std
    model = CASAHetGraphNet(input_dim, len(vocab.phases), len(vocab.resources), model_type=args.model_type).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr)
    X = torch.tensor(xn, dtype=torch.float32, device=device)
    Ye = torch.tensor(y_edge, dtype=torch.float32, device=device)
    Yv = torch.tensor(y_value, dtype=torch.float32, device=device)
    Yp = torch.tensor(y_phase, dtype=torch.long, device=device)
    Yd = torch.tensor(y_demand, dtype=torch.float32, device=device)
    M = torch.tensor(demand_mask, dtype=torch.float32, device=device)
    Ya = torch.tensor(y_availability, dtype=torch.float32, device=device)
    B = torch.tensor(uncertainty_beta, dtype=torch.float32, device=device)
    metrics_rows = []
    pos_weight = torch.tensor(float(edge_pos_weight), dtype=torch.float32, device=device)
    for epoch in range(1, args.epochs + 1):
        if sample_probs is None:
            idx = torch.randperm(X.shape[0], device=device)
        else:
            sampling_probs = torch.tensor(sample_probs, dtype=torch.float32, device=device)
            idx = torch.multinomial(sampling_probs, X.shape[0], replacement=True)
        for start in range(0, len(idx), max(1, args.batch_size)):
            b = idx[start:start + max(1, args.batch_size)]
            outp = model(X[b])
            edge_loss = F.binary_cross_entropy_with_logits(outp["edge_logits"], Ye[b], pos_weight=pos_weight)
            value_loss = F.mse_loss(outp["value"], Yv[b])
            phase_loss = F.cross_entropy(outp["phase_logits"], Yp[b])
            demand_loss = (((outp["typed_demand"] - Yd[b]) ** 2) * M[b]).sum() / torch.clamp(M[b].sum(), min=1.0)
            availability_loss = F.mse_loss(outp["availability"], Ya[b])
            sigma = outp["uncertainty"]
            observed = M[b]
            denom_cal = torch.clamp(observed.sum(), min=1.0)
            residual = torch.abs(outp["typed_demand"] - Yd[b])
            cal_loss = (torch.relu(residual - B[b] * sigma) * observed).sum() / denom_cal
            cal_loss = cal_loss + 0.001 * (sigma * observed).sum() / denom_cal
            loss = phase_loss + edge_loss + demand_loss + cal_loss + value_loss + availability_loss
            opt.zero_grad(); loss.backward(); opt.step()
        with torch.no_grad():
            pred = model(X)
            epoch_losses = casa_loss(torch.sigmoid(pred["edge_logits"]).cpu().numpy(), y_edge, pred["value"].cpu().numpy(), y_value, uncertainty=pred["uncertainty"].cpu().numpy(), uncertainty_beta=uncertainty_beta, phase_pred=torch.softmax(pred["phase_logits"], dim=1).cpu().numpy(), phase_target=y_phase, demand_pred=pred["typed_demand"].cpu().numpy(), demand_target=y_demand, demand_mask=demand_mask)
            epoch_losses["L_availability"] = float(F.mse_loss(pred["availability"], Ya).detach().cpu())
            metrics_rows.append({"epoch": epoch, **epoch_losses})
    with torch.no_grad():
        XV = torch.tensor(xvn, dtype=torch.float32, device=device)
        predv = model(XV)
        val_edge = torch.sigmoid(predv["edge_logits"]).cpu().numpy()
        val_value = predv["value"].cpu().numpy()
        val_phase = torch.softmax(predv["phase_logits"], dim=1).cpu().numpy()
        val_demand = predv["typed_demand"].cpu().numpy()
        val_availability = predv["availability"].cpu().numpy()
    val_uncertainty = predv["uncertainty"].cpu().numpy()
    val_metrics = _metrics_from_predictions(val_edge, yv_edge, val_value, yv_value, val_phase, yv_phase, val_demand, yv_demand, vmask, edge_pos_weight, args.casa_mode, device, len(xv), uncertainty_pred=val_uncertainty, uncertainty_beta=v_uncertainty_beta, availability_pred=val_availability, availability_target=yv_availability)
    checkpoint = {
        "mode": args.casa_mode,
        "model_type": f"casa_{args.model_type}_multihead",
        "architecture_semantics": "relation_aware_transition_mlp_surrogate",
        "true_heterogeneous_message_passing": False,
        "torch_state_dict": model.state_dict(),
        "weights": {"mean": mean.tolist(), "std": std.tolist()},
        "input_dim": int(input_dim), "num_phases": len(vocab.phases), "num_resources": len(vocab.resources), "vocab": vocab.to_dict(), "config": {**vars(args), "edge_pos_weight_resolved": float(edge_pos_weight)},
    }
    _save_checkpoint(out / "checkpoint.pt", checkpoint)
    return metrics_rows, val_metrics, checkpoint


def main() -> None:
    p = argparse.ArgumentParser(description="Train CASA-Net learned edge/value/demand/phase predictors.")
    p.add_argument("--dataset_dir", required=True)
    p.add_argument("--output_dir", required=True)
    p.add_argument("--epochs", type=int, default=20)
    p.add_argument("--batch_size", type=int, default=16)
    p.add_argument("--lr", type=float, default=1e-3)
    p.add_argument("--seed", type=int, default=13)
    p.add_argument("--device", default="auto")
    p.add_argument("--casa_mode", choices=["learned", "heuristic_oracle_baseline"], default="learned")
    p.add_argument("--model_type", choices=["relation_mlp", "hgt", "rgcn", "linear_smoke"], default="linear_smoke")
    p.add_argument("--paper_mode", action="store_true")
    p.add_argument("--feature_policy", choices=["auto", "legacy", "paper_safe"], default="auto", help="Feature masking policy. paper_safe removes oracle/label-derived transition slots without implying the current surrogate is a true HGT/R-GCN.")
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
            "not true heterogeneous graph message passing. Use --feature_policy paper_safe without --paper_mode for baseline development, "
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
        feature_policy = "paper_safe" if args.paper_mode else "legacy"
    if args.paper_mode and feature_policy != "paper_safe":
        raise RuntimeError("paper_mode requires --feature_policy paper_safe (or auto)")
    # Persist the resolved policy in checkpoints so inference uses the exact
    # same masking semantics as training.
    args.feature_policy = feature_policy
    train = CASADataset(args.dataset_dir, "train", vocab, value_target=args.value_target, feature_policy=feature_policy)
    val = CASADataset(args.dataset_dir, "val", vocab, value_target=args.value_target, feature_policy=feature_policy)
    if not train.samples:
        raise RuntimeError(f"no CASA training samples found in {args.dataset_dir}")
    if args.paper_mode and not train.split_file.exists():
        raise RuntimeError(f"paper_mode requires an explicit train split file: {train.split_file}")
    if args.paper_mode and (not val.split_file.exists() or not val.samples):
        raise RuntimeError(
            "paper_mode requires a non-empty, disjoint validation split in the same canonical dataset directory; "
            "merge abilitybench_av_train + abilitybench_av_val (+ test) before training instead of silently validating on train"
        )
    x, y_edge, y_value, y_phase, y_demand, demand_mask, y_availability, uncertainty_beta = train.arrays_for_training()
    xv, yv_edge, yv_value, yv_phase, yv_demand, vmask, yv_availability, v_uncertainty_beta = val.arrays_for_training() if val.samples else train.arrays_for_training()
    device = _device_auto(args.device)
    pos = float(np.sum(y_edge >= 0.5)); neg = float(len(y_edge) - pos)
    edge_pos_weight = (neg / max(pos, 1.0)) if str(args.edge_pos_weight).lower() == "auto" else max(0.0, float(args.edge_pos_weight))
    sample_probs, sampler_report = _balanced_sampling_probabilities(
        train.samples, profile_balanced=args.profile_balanced_sampler, action_balanced=args.action_balanced_sampler
    )
    if args.model_type == "linear_smoke":
        metrics_rows, val_metrics, checkpoint = _train_numpy(args, x, y_edge, y_value, y_phase, y_demand, demand_mask, y_availability, uncertainty_beta, xv, yv_edge, yv_value, yv_phase, yv_demand, vmask, yv_availability, v_uncertainty_beta, edge_pos_weight, vocab, out, device, sample_probs=sample_probs)
    else:
        metrics_rows, val_metrics, checkpoint = _train_torch(args, x, y_edge, y_value, y_phase, y_demand, demand_mask, y_availability, uncertainty_beta, xv, yv_edge, yv_value, yv_phase, yv_demand, vmask, yv_availability, v_uncertainty_beta, edge_pos_weight, vocab, out, device, sample_probs=sample_probs)
    if args.paper_mode and (val_metrics.get("L_phase", 0.0) <= 0.0 or val_metrics.get("L_demand", 0.0) <= 0.0):
        raise RuntimeError(f"paper_mode requires non-zero L_phase and L_demand; got L_phase={val_metrics.get('L_phase')} L_demand={val_metrics.get('L_demand')}")
    dump_json(out / "vocab.json", vocab.to_dict())
    dump_json(out / "config.json", {**vars(args), "edge_pos_weight_resolved": float(edge_pos_weight), "mode": args.casa_mode, "device_resolved": device, "input_dim": int(x.shape[1]), "feature_policy": feature_policy, "num_train_samples": len(train.samples), "edge_train_positive_rate": float(np.mean(y_edge >= 0.5)), "model_type": checkpoint.get("model_type"), "architecture_semantics": checkpoint.get("architecture_semantics", "linear_smoke"), "true_heterogeneous_message_passing": bool(checkpoint.get("true_heterogeneous_message_passing", False)), "sampler_report": sampler_report})
    write_jsonl(out / "train_metrics.jsonl", metrics_rows)
    dump_json(out / "val_metrics.json", val_metrics)
    if args.save_calibration_report:
        dump_json(out / "calibration_report.json", {"L_cal": val_metrics.get("L_cal"), "calibration_semantics": "typed_demand_residual_coverage_beta_tau_sigma", "edge_true_positive_rate": val_metrics.get("edge_true_positive_rate"), "edge_pred_positive_rate": val_metrics.get("edge_pred_positive_rate")})
    print(f"wrote CASA checkpoint and metrics to {out}")
    print(val_metrics)


if __name__ == "__main__":
    main()
