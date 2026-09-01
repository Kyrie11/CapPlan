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


def calibration_interval_loss(error, sigma, beta=None, mask=None, sigma_regularizer: float = 0.001) -> float:
    """Conservative residual-coverage loss.

    Paper-facing CASA calibration is resource-wise: |x - x_hat| should be
    upper-bounded by beta_tau * sigma_tau for every observable typed resource.
    ``mask`` excludes missing/non-numeric resource targets.  A very small sigma
    regularizer avoids the trivial solution of unbounded uncertainty.
    """
    e = np.abs(np.asarray(error, dtype=float))
    s = np.maximum(np.asarray(sigma, dtype=float), EPS)
    b = np.ones_like(s, dtype=float) if beta is None else np.asarray(beta, dtype=float)
    if b.shape != s.shape:
        b = np.broadcast_to(b, s.shape)
    coverage = np.maximum(0.0, e - np.maximum(b, 0.0) * s)
    if mask is None:
        return float(np.mean(coverage) + sigma_regularizer * np.mean(s))
    m = np.asarray(mask, dtype=float)
    if m.shape != coverage.shape:
        m = np.broadcast_to(m, coverage.shape)
    denom = float(np.sum(m))
    if denom <= 0.0:
        return 0.0
    return float(np.sum(coverage * m) / denom + sigma_regularizer * np.sum(s * m) / denom)


def phase_cross_entropy(phase_prob, phase_target) -> float:
    p = np.clip(np.asarray(phase_prob, dtype=float), EPS, 1.0)
    y = np.asarray(phase_target, dtype=int)
    if p.ndim != 2 or len(y) == 0:
        return 0.0
    y = np.clip(y, 0, p.shape[1] - 1)
    return float(np.mean(-np.log(p[np.arange(len(y)), y])))


def masked_normalized_huber(pred, target, mask, scale=None, delta: float = 1.0) -> float:
    """Masked resource-wise normalized Huber loss.

    This implements the paper's typed-demand normalization ``rho((x_hat-x)/s_tau)``
    while preserving predictions/targets in their original physical units.
    """
    p = np.asarray(pred, dtype=float)
    y = np.asarray(target, dtype=float)
    m = np.asarray(mask, dtype=float)
    sc = np.ones_like(p, dtype=float) if scale is None else np.asarray(scale, dtype=float)
    if sc.shape != p.shape:
        sc = np.broadcast_to(sc, p.shape)
    sc = np.maximum(np.abs(sc), EPS)
    err = (p - y) / sc
    abs_err = np.abs(err)
    d = max(float(delta), EPS)
    huber = np.where(abs_err <= d, 0.5 * err * err, d * (abs_err - 0.5 * d))
    denom = float(np.sum(m))
    if denom <= 0.0:
        return 0.0
    return float(np.sum(huber * m) / denom)


def casa_loss(
    edge_pred,
    edge_target,
    value_pred,
    value_target,
    uncertainty=None,
    phase_pred=None,
    phase_target=None,
    demand_pred=None,
    demand_target=None,
    demand_mask=None,
    uncertainty_beta=None,
    demand_scale=None,
) -> dict:
    le = binary_cross_entropy(edge_pred, edge_target)
    # Completion-value targets are binary audited-skeleton reachability labels
    # in the canonical benchmark.
    lv = binary_cross_entropy(value_pred, value_target)
    lp = phase_cross_entropy(phase_pred, phase_target) if phase_pred is not None and phase_target is not None else 0.0
    ld = masked_normalized_huber(demand_pred, demand_target, demand_mask, scale=demand_scale) if demand_pred is not None and demand_target is not None and demand_mask is not None else 0.0

    # The paper defines calibration on typed demand residuals, not on the binary
    # edge-classification residual.  Keep a legacy fallback only for callers that
    # do not provide typed demand targets.
    if uncertainty is not None and demand_pred is not None and demand_target is not None and demand_mask is not None:
        u = np.asarray(uncertainty, dtype=float)
        dp = np.asarray(demand_pred, dtype=float)
        dt = np.asarray(demand_target, dtype=float)
        if u.shape == dp.shape == dt.shape:
            sc = np.ones_like(dp, dtype=float) if demand_scale is None else np.asarray(demand_scale, dtype=float)
            if sc.shape != dp.shape:
                sc = np.broadcast_to(sc, dp.shape)
            sc = np.maximum(np.abs(sc), EPS)
            # Calibrate uncertainty on the same normalized resource scale as
            # L_demand so distance/time residuals do not dominate ratios and
            # probabilities merely because of their physical units.
            lu = calibration_interval_loss((dp - dt) / sc, u / sc, beta=uncertainty_beta, mask=demand_mask)
        else:
            lu = calibration_interval_loss(np.asarray(edge_pred) - np.asarray(edge_target), uncertainty)
    else:
        default_u = np.ones_like(np.asarray(edge_pred), dtype=float) * 0.1
        lu = calibration_interval_loss(np.asarray(edge_pred) - np.asarray(edge_target), default_u)
    return {"L_phase": lp, "L_edge": le, "L_demand": ld, "L_cal": lu, "L_value": lv, "L_CASA": lp + le + ld + lu + lv}
