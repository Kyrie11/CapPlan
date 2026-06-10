"""Typed resource algebra for passenger capability constraints."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from capplan.data.schemas import CapabilityClause, ResourceEvidence
from capplan.semantics.resource_registry import DEFAULT_REGISTRY, ResourceRegistry, ResourceType

EPS = 1e-9


def compatible(evidence: Any, threshold: Any, operator: str = "requires") -> bool:
    """Evaluate categorical compatibility without converting to a reward.

    The function supports booleans, scalar categories, and lists/sets of supported
    modalities.  ``requires`` means the evidence must provide the threshold;
    ``forbids`` means the evidence must not provide it; ``in`` means evidence is
    one of the allowed values.
    """
    if operator in ("=", "=="):
        return evidence == threshold
    if operator == "requires":
        if isinstance(threshold, bool):
            return bool(evidence) is threshold
        if isinstance(evidence, (list, tuple, set)):
            return threshold in evidence or (isinstance(threshold, (list, tuple, set)) and set(threshold).issubset(set(evidence)))
        return evidence == threshold or bool(evidence) is True
    if operator == "forbids":
        if isinstance(evidence, (list, tuple, set)):
            if isinstance(threshold, (list, tuple, set)):
                return set(threshold).isdisjoint(set(evidence))
            return threshold not in evidence
        return evidence != threshold and not (isinstance(threshold, bool) and bool(evidence) is threshold)
    if operator == "in":
        allowed = threshold if isinstance(threshold, (list, tuple, set)) else [threshold]
        if isinstance(evidence, (list, tuple, set)):
            return bool(set(evidence).intersection(set(allowed)))
        return evidence in allowed
    raise ValueError(f"unsupported categorical operator {operator}")


def conservative_value(value: Any, sigma: float, resource_type: ResourceType, beta: float = 1.0) -> Any:
    """Convert an estimate and uncertainty into conservative evidence.

    Upper-bounded burdens and cumulative burdens use ``+ beta * sigma``.  Lower
    affordances use ``- beta * sigma``.  Categorical resources are predicates and
    keep their normalized categorical value.  Probabilistic resources are clipped
    failure-risk upper bounds.
    """
    if resource_type.kind == "categorical":
        return value
    x = float(value)
    s = float(sigma or 0.0)
    if resource_type.kind in ("cumulative", "upper"):
        return x + beta * s
    if resource_type.kind == "lower":
        return x - beta * s
    if resource_type.kind == "probabilistic":
        return min(1.0, max(0.0, x + beta * s))
    raise ValueError(f"unknown resource kind {resource_type.kind}")


def initial_value(resource_type: ResourceType) -> Any:
    if resource_type.kind == "cumulative":
        return 0.0
    if resource_type.kind == "upper":
        return 0.0
    if resource_type.kind == "lower":
        return float("inf")
    if resource_type.kind == "categorical":
        return True
    if resource_type.kind == "probabilistic":
        return 0.0
    raise ValueError(resource_type.kind)


def init_ledger(resources: Iterable[str], registry: ResourceRegistry = DEFAULT_REGISTRY) -> Dict[str, Any]:
    return {name: initial_value(registry.get(name)) for name in resources}


def update_value(current: Any, evidence_value: Any, resource_type: ResourceType) -> Any:
    if resource_type.kind == "cumulative":
        return float(current or 0.0) + float(evidence_value)
    if resource_type.kind == "upper":
        return max(float(current or 0.0), float(evidence_value))
    if resource_type.kind == "lower":
        return min(float(current), float(evidence_value)) if current is not None else float(evidence_value)
    if resource_type.kind == "categorical":
        return bool(current) and bool(evidence_value)
    if resource_type.kind == "probabilistic":
        r = max(0.0, min(1.0, float(current or 0.0)))
        x = max(0.0, min(1.0, float(evidence_value)))
        return 1.0 - (1.0 - r) * (1.0 - x)
    raise ValueError(resource_type.kind)


def update(resource_state: Mapping[str, Any], evidence: ResourceEvidence, resource_type: ResourceType, beta: float = 1.0) -> Dict[str, Any]:
    new_state = dict(resource_state)
    current = new_state.get(resource_type.name, initial_value(resource_type))
    xbar = conservative_value(evidence.value, evidence.sigma, resource_type, beta=beta)
    new_state[resource_type.name] = update_value(current, xbar, resource_type)
    return new_state


def _numeric_clause_eval(value: float, operator: str, threshold: float) -> bool:
    if operator == "<=":
        return value <= threshold + EPS
    if operator == "<":
        return value < threshold + EPS
    if operator == ">=":
        return value + EPS >= threshold
    if operator == ">":
        return value > threshold - EPS
    if operator in ("=", "=="):
        return abs(value - threshold) <= EPS
    raise ValueError(f"unsupported numeric operator {operator}")


def satisfy(resource_state: Mapping[str, Any], clause: CapabilityClause, registry: ResourceRegistry = DEFAULT_REGISTRY) -> bool:
    rt = registry.get(clause.resource_name)
    value = resource_state.get(clause.resource_name, initial_value(rt))
    if rt.kind == "lower" and (value is None or (isinstance(value, float) and math.isinf(value))):
        return False
    if rt.kind == "categorical":
        if isinstance(value, bool):
            # value already represents transition-wise predicate conjunction.
            if clause.operator == "forbids":
                return bool(value)
            return bool(value)
        return compatible(value, clause.threshold, clause.operator)
    if rt.kind == "probabilistic" and clause.risk_tolerance is not None:
        threshold = clause.risk_tolerance
        op = "<="
    else:
        threshold = clause.threshold
        op = clause.operator
    return _numeric_clause_eval(float(value), op, float(threshold))


def signed_margin(resource_state: Mapping[str, Any], clause: CapabilityClause, registry: ResourceRegistry = DEFAULT_REGISTRY) -> float:
    rt = registry.get(clause.resource_name)
    value = resource_state.get(clause.resource_name, initial_value(rt))
    if rt.kind == "categorical":
        ok = satisfy(resource_state, clause, registry)
        return 1.0 if ok else -1.0
    if rt.kind == "lower" and (value is None or (isinstance(value, float) and math.isinf(value))):
        return -1.0
    threshold = clause.risk_tolerance if (rt.kind == "probabilistic" and clause.risk_tolerance is not None) else clause.threshold
    val = float(value)
    th = float(threshold)
    if rt.feasibility_order == "smaller" or clause.operator in ("<=", "<"):
        return (th - val) / (abs(th) + EPS)
    if rt.feasibility_order == "larger" or clause.operator in (">=", ">"):
        return (val - th) / (abs(th) + EPS)
    return 1.0 if satisfy(resource_state, clause, registry) else -1.0


def _resource_no_worse(v1: Any, v2: Any, rt: ResourceType) -> bool:
    if rt.kind in ("cumulative", "upper", "probabilistic"):
        return float(v1) <= float(v2) + EPS
    if rt.kind == "lower":
        return float(v1) + EPS >= float(v2)
    if rt.kind == "categorical":
        return bool(v1) >= bool(v2)
    return False


def dominates(label1: Mapping[str, Any], label2: Mapping[str, Any], registry: ResourceRegistry = DEFAULT_REGISTRY) -> bool:
    """Dominance for same anchor/phase labels.

    label mappings must contain ``cost`` and ``resource_ledger``.
    """
    if label1.get("anchor") != label2.get("anchor") or label1.get("phase") != label2.get("phase"):
        return False
    if float(label1.get("cost", 0.0)) > float(label2.get("cost", 0.0)) + EPS:
        return False
    r1 = label1.get("resource_ledger", {})
    r2 = label2.get("resource_ledger", {})
    for name in set(r1) | set(r2):
        if not registry.has(name):
            continue
        rt = registry.get(name)
        if not _resource_no_worse(r1.get(name, initial_value(rt)), r2.get(name, initial_value(rt)), rt):
            return False
    return True


def active_clauses(clauses: Sequence[CapabilityClause], phases: Iterable[str]) -> List[CapabilityClause]:
    pset = set(phases)
    return [c for c in clauses if pset.intersection(c.phase_scope) or "all" in c.phase_scope]


def all_margins(resource_state: Mapping[str, Any], clauses: Sequence[CapabilityClause], registry: ResourceRegistry = DEFAULT_REGISTRY) -> Dict[str, float]:
    return {c.resource_name: signed_margin(resource_state, c, registry) for c in clauses}
