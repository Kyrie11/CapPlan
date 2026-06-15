"""Feature encoders and stable vocabularies for CASA models."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Sequence

from capplan.data.schemas import ACTIONS, PHASES, CandidateTransition
from capplan.semantics.capability_compiler import KIND_VOCAB, OP_VOCAB, RESOURCE_VOCAB

NODE_TYPE_VOCAB = ["entrance", "ped_node", "pickup_pudo", "wait_state", "vehicle_state", "dropoff_pudo", "destination", "unknown"]
ACTION_VOCAB = list(ACTIONS)
PHASE_VOCAB = list(PHASES)


@dataclass
class FeatureVocab:
    resources: List[str] = field(default_factory=lambda: list(RESOURCE_VOCAB))
    phases: List[str] = field(default_factory=lambda: list(PHASE_VOCAB))
    actions: List[str] = field(default_factory=lambda: list(ACTION_VOCAB))
    kinds: List[str] = field(default_factory=lambda: list(KIND_VOCAB))
    operators: List[str] = field(default_factory=lambda: list(OP_VOCAB))
    node_types: List[str] = field(default_factory=lambda: list(NODE_TYPE_VOCAB))

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__


def encode_transition(t: CandidateTransition, vocab: FeatureVocab | None = None) -> List[float]:
    vocab = vocab or FeatureVocab()
    action_id = vocab.actions.index(t.action) if t.action in vocab.actions else -1
    from_id = vocab.phases.index(t.from_phase) if t.from_phase in vocab.phases else -1
    to_id = vocab.phases.index(t.to_phase) if t.to_phase in vocab.phases else -1
    numeric_vals = []
    confidences = []
    missing = 0.0
    for ev in t.resource_evidence:
        confidences.append(float(ev.confidence))
        if ev.missing:
            missing += 1.0
        try:
            if isinstance(ev.value, bool):
                numeric_vals.append(float(ev.value))
            elif isinstance(ev.value, (int, float)):
                numeric_vals.append(float(ev.value))
        except Exception:
            pass
    return [
        float(action_id),
        float(from_id),
        float(to_id),
        float(t.availability),
        float(t.map_confidence),
        float(t.cost),
        float(t.completion_value),
        sum(numeric_vals) / len(numeric_vals) if numeric_vals else 0.0,
        sum(confidences) / len(confidences) if confidences else 0.0,
        missing,
        1.0 if t.tests.z_e else 0.0,
    ]


def encode_capability_tokens(tokens: Sequence[Dict[str, Any]] | None, vocab: FeatureVocab | None = None) -> List[float]:
    """Compact passenger-contract conditioning vector for learned CASA mode.

    The original smoke encoder used only transition-level features, so a learned
    head could not distinguish two passengers facing the same transition.  This
    summary keeps the training script lightweight while making the learned target
    genuinely passenger-conditioned.
    """
    vocab = vocab or FeatureVocab()
    toks = [dict(t) for t in (tokens or [])]
    if not toks:
        return [0.0] * (6 + len(vocab.phases))
    n = float(len(toks))
    hard = sum(float(t.get("hard", 1)) for t in toks) / n
    numeric = [float(t.get("threshold_value", 0.0)) for t in toks if int(t.get("threshold_mask", 0))]
    beta = [float(t.get("beta_tau", 1.0)) for t in toks]
    resource_ids = [float(t.get("resource_id", -1)) for t in toks]
    kind_ids = [float(t.get("kind_id", -1)) for t in toks]
    phase_counts = [0.0 for _ in vocab.phases]
    for t in toks:
        mask = list(t.get("phase_mask", []))
        for i in range(min(len(mask), len(phase_counts))):
            phase_counts[i] += float(mask[i]) / n
    return [
        n,
        hard,
        sum(numeric) / len(numeric) if numeric else 0.0,
        min(numeric) if numeric else 0.0,
        sum(beta) / len(beta) if beta else 1.0,
        (sum(resource_ids) / len(resource_ids) if resource_ids else -1.0) + 0.01 * (sum(kind_ids) / len(kind_ids) if kind_ids else -1.0),
        *phase_counts,
    ]


def encode_transition_with_capability(t: CandidateTransition, tokens: Sequence[Dict[str, Any]] | None = None, vocab: FeatureVocab | None = None) -> List[float]:
    vocab = vocab or FeatureVocab()
    return encode_transition(t, vocab) + encode_capability_tokens(tokens, vocab)
