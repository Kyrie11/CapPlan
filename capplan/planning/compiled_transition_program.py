"""Request-local capability-compiled transition programs (V13).

V12 showed that exact state/ledger/edge memoization has little overlap between
primary search and proof-on-demand replay.  V13 therefore shares a different
object: the *ledger-independent* part of one transition's executable semantics
under one compiled passenger contract.  A program caches active clauses/groups,
registered evidence update operators, conservative evidence values and static
hard failures, but still applies the exact typed algebra to the current ledger.

This is an exact compilation, not a learned shortcut. Unsupported settings can
fall back to the historical ``_try_expand`` implementation.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Optional, Sequence, Tuple

from capplan.data.schemas import ResourceEvidence, ViolationRecord


@dataclass(frozen=True)
class CompiledEvidenceUpdate:
    """Ledger-independent ingredients for one registered evidence field."""

    evidence: ResourceEvidence
    resource_name: str
    resource_type: Any
    clauses_for_resource: Tuple[Any, ...]
    categorical: bool
    prepared_value: Any


@dataclass(frozen=True)
class CompiledTransitionProgram:
    """Exact request-local transition microprogram.

    The program deliberately contains only data that is invariant across
    forward ledgers for a fixed request.  Ledger-dependent typed update,
    requirement-group evaluation, dominance, and certificate selection remain
    in the normal search implementation.
    """

    transition_id: str
    from_phase: str
    to_phase: str
    action: str
    active_clauses: Tuple[Any, ...]
    active_groups: Tuple[Any, ...]
    grouped_clause_ids: FrozenSet[str]
    observed_resources: FrozenSet[str]
    updates: Tuple[CompiledEvidenceUpdate, ...]
    # Clauses whose required resource is absent from this edge. Whether they
    # fail still depends on the incoming/updated ledger, so they are only stored
    # as conditional checks.
    unobserved_fail_closed_clauses: Tuple[Any, ...]
    # Missing/low-confidence violations for resources explicitly observed on
    # this edge are ledger-independent after group filtering.
    observed_uncertainty_violations: Tuple[ViolationRecord, ...]
    # Spatial/topological/interface/dynamic failure after lifecycle legality.
    static_failure: Tuple[ViolationRecord, ...] = ()


@dataclass
class CompiledTransitionProgramCache:
    """Per-request lazy cache for V13 transition programs."""

    programs: Dict[str, Optional[CompiledTransitionProgram]] = field(default_factory=dict)
    hits: int = 0
    misses: int = 0
    compiles: int = 0
    applications: int = 0
    fallbacks: int = 0
    static_failures: int = 0

    def __len__(self) -> int:
        return len(self.programs)
