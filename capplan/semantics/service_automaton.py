"""Passenger-complete service automaton."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Set, Tuple

PHASES = ["origin", "access", "wait", "board", "ride", "alight", "egress", "destination"]
ACTIONS = ["access", "wait", "board", "ride", "alight", "egress", "replan"]
PHASE_INDEX = {p: i for i, p in enumerate(PHASES)}


@dataclass(frozen=True)
class AutomatonTransition:
    from_phase: str
    action: str
    to_phase: str


class ServiceAutomaton:
    def __init__(self, allow_replan: bool = True, disabled: bool = False) -> None:
        self.disabled = disabled
        self.transitions: Set[Tuple[str, str, str]] = set()
        self._install_canonical(allow_replan=allow_replan)

    def _install_canonical(self, allow_replan: bool) -> None:
        normal = [
            ("origin", "access", "access"),
            ("access", "wait", "wait"),
            ("wait", "board", "board"),
            ("board", "ride", "ride"),
            ("ride", "alight", "alight"),
            ("alight", "egress", "egress"),
            ("egress", "egress", "destination"),
        ]
        self.transitions.update(normal)
        # Waiting may continue without advancing the lifecycle.
        self.transitions.add(("wait", "wait", "wait"))
        if allow_replan:
            for p in PHASES:
                if p != "destination":
                    self.transitions.add((p, "replan", p))

    def legal(self, from_phase: str, action: str, to_phase: str) -> bool:
        if self.disabled:
            # Ablation: only prevent leaving the accepting state backwards; hard
            # resource constraints are still checked elsewhere.
            return from_phase != "destination"
        return (from_phase, action, to_phase) in self.transitions

    def accept(self, phase: str) -> bool:
        return phase == "destination"

    def phase_index(self, phase: str) -> int:
        return PHASE_INDEX.get(phase, len(PHASES))

    def next_phases(self, phase: str) -> List[str]:
        return [q2 for q, _, q2 in self.transitions if q == phase]
