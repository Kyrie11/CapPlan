"""Diagnostic failure certificates."""
from __future__ import annotations

from typing import Iterable, List

from capplan.data.schemas import FailureCertificate, ViolationRecord
from capplan.semantics.service_automaton import PHASE_INDEX


def select_certificate(episode_id: str, passenger_id: str, violations: Iterable[ViolationRecord]) -> FailureCertificate:
    records: List[ViolationRecord] = list(violations)
    if not records:
        dummy = ViolationRecord(
            phase="origin",
            transition_id="none",
            resource_type="search_frontier",
            signed_margin=-1.0,
            evidence_source="planner",
            confidence=1.0,
            reason="no_expandable_frontier",
        )
        records = [dummy]

    def key(v: ViolationRecord):
        return (v.signed_margin, -v.confidence, PHASE_INDEX.get(v.phase, 999))

    best = min(records, key=key)
    return FailureCertificate(
        episode_id=episode_id,
        passenger_id=passenger_id,
        phase=best.phase,
        transition_id=best.transition_id,
        resource_type=best.resource_type,
        signed_margin=best.signed_margin,
        evidence_source=best.evidence_source,
        confidence=best.confidence,
        reason=best.reason,
        violations=records,
    )
