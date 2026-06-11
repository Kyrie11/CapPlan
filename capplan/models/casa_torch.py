"""Optional PyTorch CASA model.  A NumPy fallback is used by train_casa.py."""
from __future__ import annotations


def torch_available() -> bool:
    try:
        import torch  # type: ignore  # noqa:F401
        return True
    except Exception:
        return False


def make_torch_model(input_dim: int):  # pragma: no cover - depends on torch
    import torch
    from torch import nn
    return nn.Sequential(nn.Linear(input_dim, 32), nn.ReLU(), nn.Linear(32, 2))
