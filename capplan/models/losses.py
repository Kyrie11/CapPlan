"""CASA training losses in NumPy/PyTorch-compatible scalar form."""
from __future__ import annotations

import numpy as np

EPS = 1e-9


def binary_cross_entropy(pred, target) -> float:
    p = np.clip(np.asarray(pred, dtype=float), EPS, 1.0 - EPS)
    y = np.asarray(target, dtype=float)
    return float(np.mean(-(y * np.log(p) + (1.0 - y) * np.log(1.0 - p))))


def mse(pred, target) -> float:
    p = np.asarray(pred, dtype=float)
    y = np.asarray(target, dtype=float)
    return float(np.mean((p - y) ** 2))


def calibration_interval_loss(error, sigma) -> float:
    e = np.abs(np.asarray(error, dtype=float))
    s = np.maximum(np.asarray(sigma, dtype=float), EPS)
    return float(np.mean(np.maximum(0.0, e - s) + 0.01 * s))


def phase_cross_entropy(phase_prob, phase_target) -> float:
    p = np.clip(np.asarray(phase_prob, dtype=float), EPS, 1.0)
    y = np.asarray(phase_target, dtype=int)
    if p.ndim != 2 or len(y) == 0:
        return 0.0
    y = np.clip(y, 0, p.shape[1] - 1)
    return float(np.mean(-np.log(p[np.arange(len(y)), y])))


def masked_mse(pred, target, mask) -> float:
    p = np.asarray(pred, dtype=float)
    y = np.asarray(target, dtype=float)
    m = np.asarray(mask, dtype=float)
    denom = float(np.sum(m))
    if denom <= 0.0:
        return 0.0
    return float(np.sum(((p - y) ** 2) * m) / denom)


def casa_loss(edge_pred, edge_target, value_pred, value_target, uncertainty=None, phase_pred=None, phase_target=None, demand_pred=None, demand_target=None, demand_mask=None) -> dict:
    le = binary_cross_entropy(edge_pred, edge_target)
    lv = mse(value_pred, value_target)
    lu = calibration_interval_loss(np.asarray(edge_pred) - np.asarray(edge_target), uncertainty if uncertainty is not None else np.ones_like(np.asarray(edge_pred)) * 0.1)
    lp = phase_cross_entropy(phase_pred, phase_target) if phase_pred is not None and phase_target is not None else 0.0
    ld = masked_mse(demand_pred, demand_target, demand_mask) if demand_pred is not None and demand_target is not None and demand_mask is not None else 0.0
    return {"L_phase": lp, "L_edge": le, "L_demand": ld, "L_cal": lu, "L_value": lv, "L_CASA": lp + le + ld + lu + lv}
