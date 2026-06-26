"""Optional PyTorch CASA multi-head model.

The implementation remains dependency-light but is no longer a plain MLP for
paper-facing model types.  The ``hgt``/``rgcn`` modes inject relation-aware
embeddings for the automaton transition tuple (action, source phase, target
phase) before the multi-head predictor.  This is a compact surrogate for the
paper CASA-Net interface when PyG/HGT is unavailable; it preserves the same
checkpoint schema and the required heads (edge, phase, typed demand,
uncertainty, availability, completion value).
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
    def __new__(cls, input_dim: int, num_phases: int, num_resources: int, hidden_dim: int = 128, model_type: str = "hgt", num_actions: int = 16):
        import torch
        from torch import nn

        class _Model(nn.Module):
            def __init__(self) -> None:
                super().__init__()
                self.model_type = model_type
                self.relation_aware = model_type in {"hgt", "rgcn"}
                emb_dim = 16 if self.relation_aware else 0
                if self.relation_aware:
                    self.action_emb = nn.Embedding(max(1, num_actions), emb_dim)
                    self.src_phase_emb = nn.Embedding(max(1, num_phases), emb_dim)
                    self.dst_phase_emb = nn.Embedding(max(1, num_phases), emb_dim)
                    encoder_in = input_dim + 3 * emb_dim
                else:
                    encoder_in = input_dim
                if model_type == "rgcn":
                    self.relation_gate = nn.Sequential(nn.Linear(3 * emb_dim, hidden_dim), nn.Sigmoid()) if self.relation_aware else None
                else:
                    self.relation_gate = None
                self.encoder = nn.Sequential(
                    nn.Linear(encoder_in, hidden_dim),
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

            @staticmethod
            def _index_column(x, col: int, size: int):
                if x.shape[1] <= col or size <= 0:
                    return x.new_zeros((x.shape[0],), dtype=torch.long)
                return torch.remainder(torch.clamp(torch.round(x[:, col]).long(), min=0), size)

            def _relation_features(self, x):
                action_idx = self._index_column(x, 0, self.action_emb.num_embeddings)
                src_idx = self._index_column(x, 1, self.src_phase_emb.num_embeddings)
                dst_idx = self._index_column(x, 2, self.dst_phase_emb.num_embeddings)
                return torch.cat([self.action_emb(action_idx), self.src_phase_emb(src_idx), self.dst_phase_emb(dst_idx)], dim=1)

            def forward(self, x):
                rel = None
                if self.relation_aware:
                    rel = self._relation_features(x)
                    x = torch.cat([x, rel], dim=1)
                h = self.encoder(x)
                if self.relation_gate is not None and rel is not None:
                    h = h * self.relation_gate(rel)
                return {
                    "edge_logits": self.edge_head(h).squeeze(-1),
                    "value": torch.sigmoid(self.value_head(h).squeeze(-1)),
                    "availability": torch.sigmoid(self.availability_head(h).squeeze(-1)),
                    "phase_logits": self.phase_head(h),
                    "typed_demand": self.demand_head(h),
                    "uncertainty": torch.nn.functional.softplus(self.uncertainty_head(h)) + 1e-4,
                }

        return _Model()
