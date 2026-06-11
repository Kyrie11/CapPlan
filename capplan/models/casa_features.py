"""Feature encoders and stable vocabularies for CASA models."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

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
