"""Optional PyTorch CASA multi-head model.

The implementation is deliberately dependency-light: it consumes the current
transition/capability feature tensor plus graph/context summaries and exposes the
paper-required heads (edge, phase, typed demand, uncertainty, availability, and
completion value).  A full production HGT can be substituted behind the same
checkpoint schema.
"""
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


class CASAHetGraphNet:  # pragma: no cover - class body exercised only when torch is available
    def __new__(cls, input_dim: int, num_phases: int, num_resources: int, hidden_dim: int = 128, model_type: str = "hgt"):
        import torch
        from torch import nn

        class _Model(nn.Module):
            def __init__(self) -> None:
                super().__init__()
                self.model_type = model_type
                self.encoder = nn.Sequential(
                    nn.Linear(input_dim, hidden_dim),
                    nn.ReLU(),
                    nn.LayerNorm(hidden_dim),
                    nn.Linear(hidden_dim, hidden_dim),
                    nn.ReLU(),
                )
                self.edge_head = nn.Linear(hidden_dim, 1)
                self.value_head = nn.Linear(hidden_dim, 1)
                self.availability_head = nn.Linear(hidden_dim, 1)
                self.phase_head = nn.Linear(hidden_dim, num_phases)
                self.demand_head = nn.Linear(hidden_dim, num_resources)
                self.uncertainty_head = nn.Linear(hidden_dim, num_resources)

            def forward(self, x):
                h = self.encoder(x)
                return {
                    "edge_logits": self.edge_head(h).squeeze(-1),
                    "value": torch.sigmoid(self.value_head(h).squeeze(-1)),
                    "availability": torch.sigmoid(self.availability_head(h).squeeze(-1)),
                    "phase_logits": self.phase_head(h),
                    "typed_demand": self.demand_head(h),
                    "uncertainty": torch.nn.functional.softplus(self.uncertainty_head(h)) + 1e-4,
                }

        return _Model()
