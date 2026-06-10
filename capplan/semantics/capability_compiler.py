"""Capability semantic compilation.

Structured contracts are already close to executable clauses, so the default
compiler validates them, binds resources to registry entries, and produces a
small compiled object.  Learned/free-form normalization can be added behind the
same interface without changing planning semantics.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from capplan.data.schemas import CapabilityClause, CapabilityContract
from capplan.semantics.resource_registry import DEFAULT_REGISTRY, ResourceRegistry


@dataclass
class CompiledContract:
    passenger_id: str
    clauses: List[CapabilityClause]
    guards: Dict[str, List[CapabilityClause]] = field(default_factory=dict)
    budgets: Dict[str, CapabilityClause] = field(default_factory=dict)
    interfaces: Dict[str, CapabilityClause] = field(default_factory=dict)
    uncertainty: Dict[str, CapabilityClause] = field(default_factory=dict)
    tokens: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    soft_only: bool = False

    def active(self, phase: str) -> List[CapabilityClause]:
        return [c for c in self.clauses if phase in c.phase_scope or "all" in c.phase_scope]


class CapabilityCompiler:
    def __init__(self, registry: ResourceRegistry = DEFAULT_REGISTRY, disabled: bool = False, soft_only: bool = False) -> None:
        self.registry = registry
        self.disabled = disabled
        self.soft_only = soft_only

    def compile(self, contract: CapabilityContract, trip_context: Dict[str, Any] | None = None) -> CompiledContract:
        if self.disabled:
            # Ablation: preserve fields as tokens but remove typed hard clauses.
            return CompiledContract(
                passenger_id=contract.passenger_id,
                clauses=[] if not self.soft_only else contract.clauses,
                tokens=[self._token(c) for c in contract.clauses],
                metadata={**contract.metadata, "compiler_disabled": True},
                soft_only=self.soft_only,
            )
        clauses: List[CapabilityClause] = []
        for c in contract.clauses:
            if not self.registry.has(c.resource_name):
                raise KeyError(f"contract references unknown resource {c.resource_name}")
            rt = self.registry.get(c.resource_name)
            if rt.kind != c.kind:
                raise ValueError(f"kind mismatch for {c.resource_name}: contract {c.kind}, registry {rt.kind}")
            clauses.append(c)
        compiled = CompiledContract(
            passenger_id=contract.passenger_id,
            clauses=clauses,
            tokens=[self._token(c) for c in clauses],
            metadata={**contract.metadata, "trip_context": trip_context or {}},
            soft_only=self.soft_only,
        )
        for c in clauses:
            if c.kind in ("cumulative", "upper", "lower", "probabilistic"):
                compiled.budgets[c.resource_name] = c
            if c.kind == "categorical":
                compiled.interfaces[c.resource_name] = c
            if c.resource_name in ("map_confidence", "blockage_risk", "deployment_risk", "availability_risk"):
                compiled.uncertainty[c.resource_name] = c
            for p in c.phase_scope:
                compiled.guards.setdefault(p, []).append(c)
        return compiled

    @staticmethod
    def _token(c: CapabilityClause) -> Dict[str, Any]:
        return {
            "resource": c.resource_name,
            "kind": c.kind,
            "operator": c.operator,
            "threshold": c.threshold,
            "phase_scope": c.phase_scope,
            "confidence": c.confidence,
            "risk_tolerance": c.risk_tolerance,
        }


def stricter_or_equal(a: CapabilityContract, b: CapabilityContract) -> bool:
    """Return True when contract b is no weaker than a for matched clauses."""
    by_res = {c.resource_name: c for c in a.clauses}
    for cb in b.clauses:
        ca = by_res.get(cb.resource_name)
        if ca is None or ca.kind != cb.kind:
            continue
        if cb.kind in ("cumulative", "upper", "probabilistic") and float(cb.threshold) > float(ca.threshold):
            return False
        if cb.kind == "lower" and float(cb.threshold) < float(ca.threshold):
            return False
        if cb.kind == "categorical" and cb.threshold != ca.threshold:
            # Different categorical requirements are not ordered.
            continue
    return True
