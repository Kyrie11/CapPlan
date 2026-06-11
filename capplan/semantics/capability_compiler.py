"""Capability semantic compiler.

``CapabilityCompiler.compile`` maps a functional passenger contract and trip
context to the explicit paper tuple

    Psi_p = (G_p, B_p, I_p, U_p, Z_p)

where guards, budgets, interfaces, uncertainty policies, and stable CASA tokens
remain separate symbolic structures.  Neural tokens never replace the hard
symbolic clauses used by search and verification.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping

from capplan.data.schemas import CapabilityClause, CapabilityContract, RequirementGroup
from capplan.semantics.resource_registry import DEFAULT_REGISTRY, ResourceRegistry

PHASE_VOCAB = ["origin", "access", "wait", "board", "ride", "alight", "egress", "destination"]
KIND_VOCAB = ["cumulative", "upper", "lower", "categorical", "probabilistic"]
OP_VOCAB = ["<=", "<", ">=", ">", "=", "==", "requires", "forbids", "in", "compatible_side", "meets_lighting"]
RESOURCE_VOCAB = sorted(DEFAULT_REGISTRY.names())
MODALITY_VOCAB = ["audio", "haptic", "app", "visual"]
SIDE_VOCAB = ["left", "right", "both", "either", "unknown"]


@dataclass(frozen=True)
class BudgetSpec:
    resource_name: str
    threshold: Any
    operator: str
    kind: str
    order: str
    beta_tau: float
    phases: List[str]
    hard: bool = True


@dataclass(frozen=True)
class InterfacePredicate:
    resource_name: str
    operator: str
    required: Any
    phases: List[str]
    group_id: str | None = None
    hard: bool = True


@dataclass(frozen=True)
class UncertaintySpec:
    resource_name: str
    min_confidence: float = 0.0
    beta_tau: float = 1.0
    missing_policy: str = "fail_closed"
    max_risk: float | None = None
    phases: List[str] = field(default_factory=list)


@dataclass(frozen=True)
class CapabilityToken:
    schema_version: str
    resource_id: int
    kind_id: int
    operator_id: int
    threshold_value: float
    threshold_mask: int
    categorical_value: str
    phase_mask: List[int]
    beta_tau: float
    hard: int


@dataclass
class CompiledContract:
    passenger_id: str
    clauses: List[CapabilityClause]
    groups: List[RequirementGroup] = field(default_factory=list)
    guards: Dict[str, List[str]] = field(default_factory=dict)  # G_p: phase -> clause IDs
    budgets: Dict[str, BudgetSpec] = field(default_factory=dict)  # B_p
    interfaces: Dict[str, List[InterfacePredicate]] = field(default_factory=dict)  # I_p phase -> predicates
    uncertainty: Dict[str, UncertaintySpec] = field(default_factory=dict)  # U_p
    tokens: List[Dict[str, Any]] = field(default_factory=list)  # Z_p
    metadata: Dict[str, Any] = field(default_factory=dict)
    soft_only: bool = False

    @property
    def G_p(self) -> Dict[str, List[str]]:
        return self.guards

    @property
    def B_p(self) -> Dict[str, BudgetSpec]:
        return self.budgets

    @property
    def I_p(self) -> Dict[str, List[InterfacePredicate]]:
        return self.interfaces

    @property
    def U_p(self) -> Dict[str, UncertaintySpec]:
        return self.uncertainty

    @property
    def Z_p(self) -> List[Dict[str, Any]]:
        return self.tokens

    def active(self, phase: str) -> List[CapabilityClause]:
        return [c for c in self.clauses if phase in c.phase_scope or "all" in c.phase_scope]

    def active_groups(self, phase: str) -> List[RequirementGroup]:
        return [g for g in self.groups if phase in g.phase_scope or "all" in g.phase_scope]

    def clause_by_id(self) -> Dict[str, CapabilityClause]:
        return {c.id: c for c in self.clauses}


class CapabilityCompiler:
    def __init__(self, registry: ResourceRegistry = DEFAULT_REGISTRY, disabled: bool = False, soft_only: bool = False) -> None:
        self.registry = registry
        self.disabled = disabled
        self.soft_only = soft_only

    def compile(self, contract: CapabilityContract, trip_context: Dict[str, Any] | None = None) -> CompiledContract:
        trip_context = trip_context or {}
        if self.disabled:
            return CompiledContract(
                passenger_id=contract.passenger_id,
                clauses=[] if not self.soft_only else list(contract.clauses),
                groups=[] if not self.soft_only else list(contract.groups),
                tokens=[self._token(c) for c in contract.clauses],
                metadata={**contract.metadata, "compiler_disabled": True, "trip_context": trip_context},
                soft_only=self.soft_only,
            )

        clauses = [self._validate_clause(c) for c in contract.clauses]
        groups = list(contract.groups)
        clauses, groups = self._apply_trip_modifiers(clauses, groups, contract.passenger_id, trip_context or contract.metadata.get("trip_modifiers", {}))

        compiled = CompiledContract(
            passenger_id=contract.passenger_id,
            clauses=clauses,
            groups=groups,
            tokens=[self._token(c) for c in clauses],
            metadata={**contract.metadata, "trip_context": trip_context, "compiler_tuple": "(G_p,B_p,I_p,U_p,Z_p)"},
            soft_only=self.soft_only,
        )
        for c in clauses:
            for p in c.phase_scope:
                compiled.guards.setdefault(p, []).append(c.id)
            rt = self.registry.get(c.resource_name)
            if c.kind in ("cumulative", "upper", "lower", "probabilistic"):
                compiled.budgets[c.resource_name] = BudgetSpec(c.resource_name, c.threshold, c.operator, c.kind, rt.feasibility_order, c.beta_tau, list(c.phase_scope), c.hard)
            if c.kind == "categorical":
                pred = InterfacePredicate(c.resource_name, c.operator, c.threshold, list(c.phase_scope), hard=c.hard)
                compiled.interfaces.setdefault(c.resource_name, []).append(pred)
                for p in c.phase_scope:
                    compiled.interfaces.setdefault(p, []).append(pred)
            if c.resource_name in ("map_confidence", "dynamic_confidence"):
                compiled.uncertainty[c.resource_name] = UncertaintySpec(c.resource_name, min_confidence=float(c.threshold), beta_tau=c.beta_tau, missing_policy=c.missing_policy, phases=list(c.phase_scope))
            elif c.kind == "probabilistic":
                max_risk = float(c.risk_tolerance if c.risk_tolerance is not None else c.threshold)
                compiled.uncertainty[c.resource_name] = UncertaintySpec(c.resource_name, min_confidence=0.0, beta_tau=c.beta_tau, missing_policy=c.missing_policy, max_risk=max_risk, phases=list(c.phase_scope))
            else:
                compiled.uncertainty.setdefault(c.resource_name, UncertaintySpec(c.resource_name, min_confidence=0.0, beta_tau=c.beta_tau, missing_policy=c.missing_policy, phases=list(c.phase_scope)))
        # Attach group IDs to interface predicates for auditability.
        group_clause_ids = {cid: g.group_id for g in groups for cid in g.clause_ids}
        for phase, preds in list(compiled.interfaces.items()):
            new_preds = []
            for pred in preds:
                cid = next((c.id for c in clauses if c.resource_name == pred.resource_name and pred.required == c.threshold), None)
                new_preds.append(InterfacePredicate(pred.resource_name, pred.operator, pred.required, pred.phases, group_clause_ids.get(cid), pred.hard))
            compiled.interfaces[phase] = new_preds
        return compiled

    def _validate_clause(self, c: CapabilityClause) -> CapabilityClause:
        if not self.registry.has(c.resource_name):
            raise KeyError(f"contract references unknown resource {c.resource_name}")
        rt = self.registry.get(c.resource_name)
        if rt.kind != c.kind:
            raise ValueError(f"kind mismatch for {c.resource_name}: contract {c.kind}, registry {rt.kind}")
        if c.resource_name == "door_side" and isinstance(c.threshold, bool):
            raise ValueError("door_side must be a required side/policy, not True/False")
        return c

    def _apply_trip_modifiers(self, clauses: List[CapabilityClause], groups: List[RequirementGroup], passenger_id: str, trip_context: Mapping[str, Any]) -> tuple[List[CapabilityClause], List[RequirementGroup]]:
        out = list(clauses)
        group_out = list(groups)

        def has_resource(name: str) -> bool:
            return any(c.resource_name == name for c in out)

        mods = dict(trip_context or {})
        if trip_context.get("trip_modifiers"):
            mods.update(trip_context.get("trip_modifiers") or {})
        next_idx = len(out)
        if mods.get("night_trip") and not has_resource("lighting"):
            out.append(CapabilityClause("lighting", ["access", "wait", "egress"], "meets_lighting", "lit", "categorical", source="trip_modifier", clause_id=f"{passenger_id}:c{next_idx:02d}:lighting", beta_tau=1.2, missing_policy="fail_closed")); next_idx += 1
        if mods.get("rain_or_snow"):
            for i, c in enumerate(out):
                if c.resource_name == "slope" and isinstance(c.threshold, (int, float)):
                    out[i] = CapabilityClause(c.resource_name, c.phase_scope, c.operator, min(float(c.threshold), 0.06), c.kind, c.confidence, c.risk_tolerance, c.source, c.consent_scope, c.clause_id, c.hard, max(c.beta_tau, 1.4), c.missing_policy, c.metadata)
                elif c.resource_name == "blockage_risk" and isinstance(c.threshold, (int, float)):
                    th = min(float(c.threshold), 0.30)
                    out[i] = CapabilityClause(c.resource_name, c.phase_scope, c.operator, th, c.kind, c.confidence, th, c.source, c.consent_scope, c.clause_id, c.hard, max(c.beta_tau, 1.4), c.missing_policy, c.metadata)
        if mods.get("luggage") or mods.get("companion"):
            for i, c in enumerate(out):
                if c.resource_name in ("access_distance_m", "egress_distance_m") and isinstance(c.threshold, (int, float)):
                    out[i] = CapabilityClause(c.resource_name, c.phase_scope, c.operator, float(c.threshold) * 0.9, c.kind, c.confidence, c.risk_tolerance, c.source, c.consent_scope, c.clause_id, c.hard, c.beta_tau, c.missing_policy, c.metadata)
                elif c.resource_name in ("deployment_clearance_m", "door_width_m") and isinstance(c.threshold, (int, float)):
                    out[i] = CapabilityClause(c.resource_name, c.phase_scope, c.operator, max(float(c.threshold), 1.1 if c.resource_name == "deployment_clearance_m" else 0.82), c.kind, c.confidence, c.risk_tolerance, c.source, c.consent_scope, c.clause_id, c.hard, c.beta_tau, c.missing_policy, c.metadata)
        if mods.get("temporary_assistance_required") and not has_resource("assistance"):
            out.append(CapabilityClause("assistance", ["wait", "board", "alight"], "requires", True, "categorical", source="trip_modifier", clause_id=f"{passenger_id}:c{next_idx:02d}:assistance", missing_policy="fail_closed"))
        return out, group_out

    @staticmethod
    def _token(c: CapabilityClause) -> Dict[str, Any]:
        tok = CapabilityToken(
            schema_version="casa_token_v1",
            resource_id=RESOURCE_VOCAB.index(c.resource_name) if c.resource_name in RESOURCE_VOCAB else -1,
            kind_id=KIND_VOCAB.index(c.kind) if c.kind in KIND_VOCAB else -1,
            operator_id=OP_VOCAB.index(c.operator) if c.operator in OP_VOCAB else -1,
            threshold_value=float(c.threshold) if isinstance(c.threshold, (int, float)) and not isinstance(c.threshold, bool) else 0.0,
            threshold_mask=1 if isinstance(c.threshold, (int, float)) and not isinstance(c.threshold, bool) else 0,
            categorical_value="|".join(map(str, c.threshold)) if isinstance(c.threshold, (list, tuple, set)) else str(c.threshold),
            phase_mask=[1 if p in c.phase_scope or "all" in c.phase_scope else 0 for p in PHASE_VOCAB],
            beta_tau=float(c.beta_tau),
            hard=1 if c.hard else 0,
        )
        return tok.__dict__


# ---------- Monotonicity helpers ----------

def _clause_map(contract: CapabilityContract) -> Dict[str, CapabilityClause]:
    return {c.resource_name: c for c in contract.clauses}


def _cat_implies(strict: CapabilityClause, weak: CapabilityClause) -> bool:
    if strict.threshold == weak.threshold:
        return True
    if weak.resource_name == "door_side":
        if weak.threshold in ("either", "both", "unknown", None):
            return True
        return strict.threshold == weak.threshold
    if weak.operator == "in" and strict.operator == "in":
        s = set(strict.threshold if isinstance(strict.threshold, (list, tuple, set)) else [strict.threshold])
        w = set(weak.threshold if isinstance(weak.threshold, (list, tuple, set)) else [weak.threshold])
        return s.issubset(w)
    if weak.operator == "requires" and strict.operator == "requires":
        return strict.threshold == weak.threshold
    return False


def stricter_or_equal(weak: CapabilityContract, strict: CapabilityContract) -> bool:
    """Return True when ``strict`` is no weaker than ``weak`` on comparable resources."""
    weak_by_res = _clause_map(weak)
    for cs in strict.clauses:
        cw = weak_by_res.get(cs.resource_name)
        if cw is None or cw.kind != cs.kind:
            continue
        if cs.kind in ("cumulative", "upper", "probabilistic") and isinstance(cw.threshold, (int, float)) and isinstance(cs.threshold, (int, float)):
            if float(cs.threshold) > float(cw.threshold) + 1e-9:
                return False
        elif cs.kind == "lower" and isinstance(cw.threshold, (int, float)) and isinstance(cs.threshold, (int, float)):
            if float(cs.threshold) + 1e-9 < float(cw.threshold):
                return False
        elif cs.kind == "categorical":
            if not _cat_implies(cs, cw):
                return False
    return True
