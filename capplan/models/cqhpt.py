"""Capability-Query Heterogeneous Polyline Transformer (CQ-HPT, V14).

The model is deliberately a *guidance-only* module.  It consumes lower-level
heterogeneous service evidence and a compiled capability-state query, then
returns one scalar ordering score.  It never implements Allow/Update/Sat and
therefore cannot acquire hard-feasibility authority.

V14 is a necessity-validation version with matched-capacity controls:
``full``            capability query participates in evidence routing,
``late_fusion``     identical evidence encoder; capability enters only after
                    passenger-neutral evidence routing,
``neutral_query``   identical architecture; passenger-specific query fields are
                    masked before routing and scoring,
``query_only``      identical query tower/head without heterogeneous evidence.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from pathlib import Path
import time
from typing import Any, Dict, List, Mapping, Sequence

import torch
from torch import nn

from capplan.models.cqhpt_features import (
    CQHPTFeatureConfig,
    RAW_TOKEN_DIM,
    TOKEN_TYPES,
    build_capability_query_features,
    build_raw_evidence_tokens,
    select_transition_tokens_from_bank,
    cqhpt_query_feature_names,
)


CQHPT_MODEL_VERSION = "cqhpt_v14_capability_routing_20260906"
CQHPT_MODES = {"full", "late_fusion", "neutral_query", "query_only"}


@dataclass(frozen=True)
class CQHPTConfig:
    raw_token_dim: int = RAW_TOKEN_DIM
    query_dim: int = len(cqhpt_query_feature_names())
    hidden_dim: int = 96
    num_heads: int = 4
    token_layers: int = 2
    dropout: float = 0.05
    mode: str = "full"
    max_tokens: int = 48

    def __post_init__(self) -> None:
        if self.mode not in CQHPT_MODES:
            raise ValueError(f"unsupported CQ-HPT mode {self.mode!r}")
        if self.hidden_dim % self.num_heads:
            raise ValueError("hidden_dim must be divisible by num_heads")


class _CapabilityCrossAttention(nn.Module):
    """Typed capability-query cross attention with relative-pose bias."""

    def __init__(self, hidden_dim: int, num_heads: int, num_types: int, dropout: float) -> None:
        super().__init__()
        self.hidden_dim = hidden_dim
        self.num_heads = num_heads
        self.head_dim = hidden_dim // num_heads
        self.q = nn.Linear(hidden_dim, hidden_dim, bias=False)
        self.k = nn.Linear(hidden_dim, hidden_dim, bias=False)
        self.v = nn.Linear(hidden_dim, hidden_dim, bias=False)
        self.o = nn.Linear(hidden_dim, hidden_dim, bias=False)
        self.type_bias = nn.Embedding(num_types, num_heads)
        self.rel_bias = nn.Sequential(
            nn.Linear(2, hidden_dim // 2), nn.GELU(), nn.Linear(hidden_dim // 2, num_heads)
        )
        self.query_rel_gate = nn.Sequential(
            nn.Linear(hidden_dim + 2, hidden_dim // 2), nn.GELU(), nn.Linear(hidden_dim // 2, num_heads)
        )
        self.dropout = nn.Dropout(dropout)
        self.norm = nn.LayerNorm(hidden_dim)

    def forward(
        self,
        query_h: torch.Tensor,              # [B,D]
        token_h: torch.Tensor,              # [B,K,D]
        token_types: torch.Tensor,          # [B,K]
        rel_xy: torch.Tensor,               # [B,K,2]
        mask: torch.Tensor,                 # [B,K] True for valid
    ) -> tuple[torch.Tensor, torch.Tensor]:
        b, k, _ = token_h.shape
        q = self.q(query_h).view(b, self.num_heads, self.head_dim)             # B,H,d
        kk = self.k(token_h).view(b, k, self.num_heads, self.head_dim)         # B,K,H,d
        vv = self.v(token_h).view(b, k, self.num_heads, self.head_dim)
        logits = torch.einsum("bhd,bkhd->bhk", q, kk) / math.sqrt(self.head_dim)
        logits = logits + self.type_bias(token_types).permute(0, 2, 1)
        logits = logits + self.rel_bias(rel_xy).permute(0, 2, 1)
        qrep = query_h[:, None, :].expand(-1, k, -1)
        logits = logits + self.query_rel_gate(torch.cat([qrep, rel_xy], dim=-1)).permute(0, 2, 1)
        logits = logits.masked_fill(~mask[:, None, :], -1e4)
        attn = torch.softmax(logits, dim=-1)
        attn = self.dropout(attn)
        ctx = torch.einsum("bhk,bkhd->bhd", attn, vv).reshape(b, self.hidden_dim)
        return self.norm(query_h + self.o(ctx)), attn


class CQHPTModel(nn.Module):
    """Small heterogeneous-polyline proposal model used in V14.

    Static evidence encoding is intentionally separable from capability-query
    scoring so inference can cache scene/transition context and report cold vs.
    amortized costs cleanly.
    """

    def __init__(self, config: CQHPTConfig | None = None) -> None:
        super().__init__()
        self.config = config or CQHPTConfig()
        c = self.config
        self.token_proj = nn.Linear(c.raw_token_dim, c.hidden_dim)
        self.type_emb = nn.Embedding(len(TOKEN_TYPES), c.hidden_dim)
        self.pos_proj = nn.Sequential(nn.Linear(2, c.hidden_dim), nn.GELU(), nn.Linear(c.hidden_dim, c.hidden_dim))
        enc_layer = nn.TransformerEncoderLayer(
            d_model=c.hidden_dim,
            nhead=c.num_heads,
            dim_feedforward=4 * c.hidden_dim,
            dropout=c.dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.token_encoder = nn.TransformerEncoder(enc_layer, num_layers=c.token_layers)
        self.query_proj = nn.Sequential(
            nn.Linear(c.query_dim, c.hidden_dim), nn.GELU(), nn.Linear(c.hidden_dim, c.hidden_dim), nn.LayerNorm(c.hidden_dim)
        )
        # Neutral structural query has identical dimensionality/parameter count.
        self.neutral_query_proj = nn.Sequential(
            nn.Linear(c.query_dim, c.hidden_dim), nn.GELU(), nn.Linear(c.hidden_dim, c.hidden_dim), nn.LayerNorm(c.hidden_dim)
        )
        self.cross1 = _CapabilityCrossAttention(c.hidden_dim, c.num_heads, len(TOKEN_TYPES), c.dropout)
        self.cross2 = _CapabilityCrossAttention(c.hidden_dim, c.num_heads, len(TOKEN_TYPES), c.dropout)
        self.head = nn.Sequential(
            nn.Linear(2 * c.hidden_dim, 2 * c.hidden_dim), nn.GELU(), nn.Dropout(c.dropout),
            nn.Linear(2 * c.hidden_dim, c.hidden_dim), nn.GELU(), nn.Linear(c.hidden_dim, 1),
        )
        self.query_only_context = nn.Parameter(torch.zeros(c.hidden_dim))

    @staticmethod
    def _neutralize_query(query: torch.Tensor) -> torch.Tensor:
        """Mask passenger-specific fields while keeping phase/action/history/cost.

        Query layout is defined in ``cqhpt_features``: phase one-hot, 7 action
        entries, then 7 scalars (first three structural, last four capability
        counts), followed by 4 values per resource.  Neutral control zeros all
        capability-specific fields without changing tensor shape/capacity.
        """
        out = query.clone()
        structural = len([n for n in cqhpt_query_feature_names() if n.startswith("phase::")]) + 7
        # history_frac, remaining_phase_frac, transition_cost remain.
        out[:, structural + 3 :] = 0.0
        return out

    def encode_tokens(
        self,
        token_raw: torch.Tensor,
        token_types: torch.Tensor,
        rel_xy: torch.Tensor,
        mask: torch.Tensor,
    ) -> torch.Tensor:
        h = self.token_proj(token_raw) + self.type_emb(token_types) + self.pos_proj(rel_xy)
        # Transformer key-padding mask uses True for padding.
        h = self.token_encoder(h, src_key_padding_mask=~mask)
        return h

    def score_encoded(
        self,
        query: torch.Tensor,
        token_h: torch.Tensor | None,
        token_types: torch.Tensor | None,
        rel_xy: torch.Tensor | None,
        mask: torch.Tensor | None,
        *,
        mode: str | None = None,
    ) -> tuple[torch.Tensor, Dict[str, torch.Tensor]]:
        mode = mode or self.config.mode
        if mode not in CQHPT_MODES:
            raise ValueError(mode)
        q_full = self.query_proj(query)
        neutral_x = self._neutralize_query(query)
        q_neutral = self.neutral_query_proj(neutral_x)

        if mode == "query_only":
            routed_context = self.query_only_context[None, :].expand(query.shape[0], -1)
            attn = query.new_zeros((query.shape[0], self.config.num_heads, 1))
            head_q = q_full
        else:
            if token_h is None or token_types is None or rel_xy is None or mask is None:
                raise ValueError("token tensors are required outside query_only mode")
            # Full mode routes evidence with the passenger capability query.
            # Late-fusion and neutral-query controls use the *same* attention
            # stack and parameter count, but route with a passenger-neutral
            # structural query.  This makes the causal comparison isolate
            # capability-conditioned routing rather than extra attention
            # capacity or a different pooling operator.
            route_q = q_full if mode == "full" else q_neutral
            routed_context, a1 = self.cross1(route_q, token_h, token_types, rel_xy, mask)
            routed_context, a2 = self.cross2(routed_context, token_h, token_types, rel_xy, mask)
            attn = 0.5 * (a1 + a2)
            head_q = q_neutral if mode == "neutral_query" else q_full

        score = self.head(torch.cat([head_q, routed_context], dim=-1)).squeeze(-1)
        return score, {
            "attention": attn,
            "query_embedding": head_q,
            "context_embedding": routed_context,
        }

    def forward(
        self,
        query: torch.Tensor,
        token_raw: torch.Tensor,
        token_types: torch.Tensor,
        rel_xy: torch.Tensor,
        mask: torch.Tensor,
        *,
        mode: str | None = None,
    ) -> tuple[torch.Tensor, Dict[str, torch.Tensor]]:
        if (mode or self.config.mode) == "query_only":
            return self.score_encoded(query, None, None, None, None, mode=mode)
        h = self.encode_tokens(token_raw, token_types, rel_xy, mask)
        return self.score_encoded(query, h, token_types, rel_xy, mask, mode=mode)


@dataclass
class _PreparedTransitionContext:
    encoded: torch.Tensor | None
    token_types: torch.Tensor | None
    rel_xy: torch.Tensor | None
    mask: torch.Tensor | None


class CQHPTGuide:
    """Request-local inference adapter implementing TSBS ``score_successors``."""

    def __init__(self, checkpoint: str | Path | Mapping[str, Any], device: str = "auto", feature_config: CQHPTFeatureConfig | None = None) -> None:
        if isinstance(checkpoint, Mapping):
            payload = dict(checkpoint)
        else:
            payload = torch.load(str(checkpoint), map_location="cpu", weights_only=False)
        cfg = CQHPTConfig(**dict(payload.get("config") or {}))
        self.model = CQHPTModel(cfg)
        self.model.load_state_dict(payload["state_dict"])
        self.model.eval()
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = torch.device(device)
        self.model.to(self.device)
        self.feature_config = feature_config or CQHPTFeatureConfig(max_tokens=cfg.max_tokens)
        self.mode = cfg.mode
        self._contexts: Dict[str, _PreparedTransitionContext] = {}
        self._transition_tokens: Dict[str, List[Dict[str, Any]]] = {}
        self.inference_calls = 0
        self.inference_ms = 0.0
        self.context_encode_ms = 0.0
        self.scored_successors = 0
        self.attention_entropy_sum = 0.0
        self.attention_entropy_count = 0

    @property
    def config(self) -> CQHPTConfig:
        return self.model.config

    def clear_request(self) -> None:
        self._contexts.clear()
        self._transition_tokens.clear()
        self.inference_calls = 0
        self.inference_ms = 0.0
        self.context_encode_ms = 0.0
        self.scored_successors = 0
        self.attention_entropy_sum = 0.0
        self.attention_entropy_count = 0

    @staticmethod
    def _pad_token_batch(token_lists: Sequence[Sequence[Mapping[str, Any]]], device: torch.device) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        b = len(token_lists)
        k = max(1, max((len(x) for x in token_lists), default=1))
        raw = torch.zeros((b, k, RAW_TOKEN_DIM), dtype=torch.float32, device=device)
        typ = torch.zeros((b, k), dtype=torch.long, device=device)
        rel = torch.zeros((b, k, 2), dtype=torch.float32, device=device)
        mask = torch.zeros((b, k), dtype=torch.bool, device=device)
        for i, toks in enumerate(token_lists):
            if not toks:
                # One neutral transition token prevents all-masked attention.
                mask[i, 0] = True
                continue
            for j, t in enumerate(toks[:k]):
                raw[i, j] = torch.as_tensor(t["raw"], dtype=torch.float32, device=device)
                typ[i, j] = int(t["type_id"])
                rel[i, j] = torch.as_tensor(t["rel_xy"], dtype=torch.float32, device=device)
                mask[i, j] = True
        return raw, typ, rel, mask

    def prepare_request(
        self,
        *,
        graph: Any,
        pudos: Sequence[Any],
        vehicle: Any,
        scene: Mapping[str, Any] | Any | None,
        transitions: Sequence[Any],
        evidence_bank: Mapping[str, Any] | None = None,
    ) -> None:
        self.clear_request()
        tids: List[str] = []
        lists: List[List[Dict[str, Any]]] = []
        for e in transitions:
            if evidence_bank is not None:
                toks = select_transition_tokens_from_bank(
                    bank=evidence_bank, transition=e, vehicle=vehicle, config=self.feature_config,
                )
            else:
                if not getattr(graph, "nodes", None) and not getattr(graph, "edges", None):
                    raise RuntimeError(
                        "CQ-HPT requires a compact evidence cache when evaluation uses saved-transition fast path. "
                        "Run scripts/prepare_cqhpt_evidence_cache.py once for the dataset."
                    )
                toks = build_raw_evidence_tokens(
                    transition=e, graph=graph, pudos=pudos, vehicle=vehicle, scene=scene, config=self.feature_config,
                )
            tid = str(e.transition_id)
            tids.append(tid)
            lists.append(toks)
            self._transition_tokens[tid] = toks
        if self.mode == "query_only" or not lists:
            for tid in tids:
                self._contexts[tid] = _PreparedTransitionContext(None, None, None, None)
            return
        raw, typ, rel, mask = self._pad_token_batch(lists, self.device)
        t0 = time.perf_counter()
        with torch.inference_mode():
            enc = self.model.encode_tokens(raw, typ, rel, mask)
        self.context_encode_ms = (time.perf_counter() - t0) * 1000.0
        for i, tid in enumerate(tids):
            self._contexts[tid] = _PreparedTransitionContext(
                enc[i : i + 1].detach(), typ[i : i + 1], rel[i : i + 1], mask[i : i + 1]
            )

    def score_successors(self, rows: Sequence[tuple[Any, Any]], compiled: Any, registry: Any) -> List[float]:
        if not rows:
            return []
        queries = [build_capability_query_features(successor_label=label, transition=edge, compiled=compiled, registry=registry) for label, edge in rows]
        q = torch.as_tensor(queries, dtype=torch.float32, device=self.device)
        # Sibling transitions generally have different token contexts.  Score in
        # one batch by concatenating the already cached per-transition contexts.
        if self.mode == "query_only":
            enc = typ = rel = mask = None
        else:
            contexts = [self._contexts.get(str(edge.transition_id)) for _, edge in rows]
            if any(c is None or c.encoded is None for c in contexts):
                raise RuntimeError("CQ-HPT request context was not prepared before search")
            enc = torch.cat([c.encoded for c in contexts if c is not None], dim=0)
            typ = torch.cat([c.token_types for c in contexts if c is not None], dim=0)
            rel = torch.cat([c.rel_xy for c in contexts if c is not None], dim=0)
            mask = torch.cat([c.mask for c in contexts if c is not None], dim=0)
        t0 = time.perf_counter()
        with torch.inference_mode():
            scores, aux = self.model.score_encoded(q, enc, typ, rel, mask, mode=self.mode)
        self.inference_ms += (time.perf_counter() - t0) * 1000.0
        self.inference_calls += 1
        self.scored_successors += len(rows)
        attn = aux.get("attention")
        if attn is not None and attn.numel() > 1:
            p = attn.clamp_min(1e-12)
            ent = -(p * p.log()).sum(dim=-1).mean().item()
            self.attention_entropy_sum += float(ent)
            self.attention_entropy_count += 1
        return [float(x) for x in scores.detach().cpu().tolist()]

    def diagnostics(self) -> Dict[str, Any]:
        return {
            "cqhpt_model_version": CQHPT_MODEL_VERSION,
            "cqhpt_mode": self.mode,
            "cqhpt_context_encode_ms": float(self.context_encode_ms),
            "cqhpt_inference_ms": float(self.inference_ms),
            "cqhpt_inference_calls": int(self.inference_calls),
            "cqhpt_scored_successors": int(self.scored_successors),
            "cqhpt_attention_entropy": (
                float(self.attention_entropy_sum / self.attention_entropy_count) if self.attention_entropy_count else 0.0
            ),
        }


def save_cqhpt_checkpoint(path: str | Path, model: CQHPTModel, *, metadata: Mapping[str, Any] | None = None) -> None:
    payload = {
        "model_version": CQHPT_MODEL_VERSION,
        "config": asdict(model.config),
        "state_dict": model.state_dict(),
        "metadata": dict(metadata or {}),
    }
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    torch.save(payload, str(path))
