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


def casa_loss(edge_pred, edge_target, value_pred, value_target, uncertainty=None) -> dict:
    le = binary_cross_entropy(edge_pred, edge_target)
    lv = mse(value_pred, value_target)
    lu = calibration_interval_loss(np.asarray(edge_pred) - np.asarray(edge_target), uncertainty if uncertainty is not None else np.ones_like(np.asarray(edge_pred)) * 0.1)
    return {"L_phase": 0.0, "L_edge": le, "L_demand": 0.0, "L_cal": lu, "L_value": lv, "L_CASA": le + lu + lv}
