"""Dataset loader for CASA training."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple

import numpy as np

from capplan.data.schemas import transition_from_dict
from capplan.models.casa_features import FeatureVocab, encode_transition
from capplan.utils.serialization import read_jsonl


@dataclass
class CASASample:
    transition_id: str
    episode_id: str
    x: List[float]
    y_edge: float
    y_value: float
    y_phase: int


class CASADataset:
    def __init__(self, dataset_dir: str | Path, split: str = "train", vocab: FeatureVocab | None = None) -> None:
        self.dataset_dir = Path(dataset_dir)
        self.vocab = vocab or FeatureVocab()
        split_ids = self._read_split(split)
        labels = {d["transition_id"]: d for d in read_jsonl(self.dataset_dir / "transition_labels.jsonl")}
        self.samples: List[CASASample] = []
        for d in read_jsonl(self.dataset_dir / "candidate_transitions.jsonl"):
            t = transition_from_dict(d)
            if split_ids and t.episode_id not in split_ids:
                continue
            lab = labels.get(t.transition_id, {})
            y_edge = 1.0 if lab.get("z_e", t.tests.z_e) else 0.0
            y_value = max(0.0, min(1.0, t.completion_value))
            y_phase = self.vocab.phases.index(t.to_phase) if t.to_phase in self.vocab.phases else 0
            self.samples.append(CASASample(t.transition_id, t.episode_id, encode_transition(t, self.vocab), y_edge, y_value, y_phase))

    def _read_split(self, split: str) -> set[str]:
        p = self.dataset_dir / "splits" / f"{split}_episodes.txt"
        if not p.exists():
            return set()
        return {line.strip() for line in p.read_text(encoding="utf-8").splitlines() if line.strip()}

    def arrays(self) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        x = np.array([s.x for s in self.samples], dtype=np.float32)
        y_edge = np.array([s.y_edge for s in self.samples], dtype=np.float32)
        y_value = np.array([s.y_value for s in self.samples], dtype=np.float32)
        y_phase = np.array([s.y_phase for s in self.samples], dtype=np.int64)
        return x, y_edge, y_value, y_phase
