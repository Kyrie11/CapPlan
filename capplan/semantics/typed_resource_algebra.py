"""Typed resource algebra for passenger capability feasibility."""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict, is_dataclass
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from capplan.data.schemas import CapabilityClause, RequirementGroup, ResourceEvidence
from capplan.semantics.resource_registry import DEFAULT_REGISTRY, ResourceRegistry, ResourceType

EPS = 1e-9


@dataclass
class PredicateState:
    ok: bool
    observed: Any
    required: Any
    operator: str
    evidence_source: str
    confidence: float
    failures: List[Dict[str, Any]] | None = None

    def __bool__(self) -> bool:
        return bool(self.ok)


@dataclass
class MissingEvidence:
    resource_name: str
    phase: str = "unknown"
    reason: str = "not_observed"
    evidence_source: str = "missing"
    confidence: float = 0.0


MISSING = MissingEvidence("unknown")


def _as_plain(v: Any) -> Any:
    if is_dataclass(v):
        return asdict(v)
    return v


def is_missing(v: Any) -> bool:
    return isinstance(v, MissingEvidence) or (isinstance(v, Mapping) and v.get("__missing__"))


def compatible(evidence: Any, threshold: Any, operator: str = "requires") -> bool:
    """Evaluate symbolic compatibility without converting to reward."""
    if isinstance(evidence, PredicateState):
        return evidence.ok
    if isinstance(evidence, ResourceEvidence):
        evidence = evidence.value
    if evidence is None:
        return False
    if operator in ("=", "=="):
        return evidence == threshold
    if operator == "requires":
        if isinstance(threshold, bool):
            return bool(evidence) is threshold
        if isinstance(evidence, (list, tuple, set)):
            if isinstance(threshold, (list, tuple, set)):
                return set(threshold).issubset(set(evidence))
            return threshold in evidence
        return evidence == threshold
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
    if operator == "compatible_side":
        required = threshold
        if required in (None, "either", "both", "any"):
            return True
        if isinstance(evidence, Mapping):
            observed = evidence.get("observed") or evidence.get("door_side") or evidence.get("vehicle_side")
            curb_side = evidence.get("curb_side") or evidence.get("anchor_side")
            vehicle_side = evidence.get("vehicle_side") or observed
            if vehicle_side == "both":
                side_ok = curb_side in (required, "both", None) or required in ("either", "both")
            else:
                side_ok = vehicle_side == curb_side or curb_side in ("both", None)
            return side_ok and (required == "either" or vehicle_side == required or vehicle_side == "both")
        return evidence == required or evidence == "both"
    if operator == "meets_lighting":
        if threshold in (None, "any", "day_or_lit"):
            return evidence in ("day", "lit", "well_lit", "day_or_lit")
        if threshold == "lit":
            return evidence in ("lit", "well_lit", "day")
        return evidence == threshold
    raise ValueError(f"unsupported categorical operator {operator}")


def conservative_value(value: Any, sigma: float, resource_type: ResourceType, beta: float = 1.0) -> Any:
    if value is None or isinstance(value, MissingEvidence):
        return MissingEvidence(resource_type.name, reason="not_observed")
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
    return MissingEvidence(resource_type.name, reason="not_observed")


def neutral_value(resource_type: ResourceType) -> Any:
    if resource_type.kind == "cumulative":
        return 0.0
    if resource_type.kind == "upper":
        return 0.0
    if resource_type.kind == "lower":
        return float("inf")
    if resource_type.kind == "categorical":
        return PredicateState(True, observed="not_checked", required="not_checked", operator="requires", evidence_source="neutral", confidence=1.0, failures=[])
    if resource_type.kind == "probabilistic":
        return 0.0
    raise ValueError(resource_type.kind)


def init_ledger(resources: Iterable[str], registry: ResourceRegistry = DEFAULT_REGISTRY) -> Dict[str, Any]:
    return {name: initial_value(registry.get(name)) for name in resources}


def update_value(current: Any, evidence_value: Any, resource_type: ResourceType, *, evidence: ResourceEvidence | None = None, clause: CapabilityClause | None = None) -> Any:
    if is_missing(evidence_value):
        return MissingEvidence(resource_type.name, reason=getattr(evidence_value, "reason", "not_observed"))
    cur = neutral_value(resource_type) if is_missing(current) else current
    if resource_type.kind == "cumulative":
        return float(cur or 0.0) + float(evidence_value)
    if resource_type.kind == "upper":
        return max(float(cur or 0.0), float(evidence_value))
    if resource_type.kind == "lower":
        return min(float(cur), float(evidence_value)) if cur is not None else float(evidence_value)
    if resource_type.kind == "categorical":
        if clause is None:
            ok = bool(evidence_value)
            required = True
            op = "requires"
        else:
            ok = compatible(evidence_value, clause.threshold, clause.operator)
            required = clause.threshold
            op = clause.operator
        prev_ok = bool(cur.ok) if isinstance(cur, PredicateState) else bool(cur)
        failures = []
        if isinstance(cur, PredicateState) and cur.failures:
            failures.extend(cur.failures)
        if not ok:
            failures.append({"observed": evidence_value, "required": required, "operator": op, "source": evidence.source if evidence else "unknown"})
        return PredicateState(
            ok=prev_ok and ok,
            observed=evidence_value,
            required=required,
            operator=op,
            evidence_source=evidence.source if evidence else "unknown",
            confidence=evidence.confidence if evidence else 1.0,
            failures=failures,
        )
    if resource_type.kind == "probabilistic":
        r = max(0.0, min(1.0, float(cur or 0.0)))
        x = max(0.0, min(1.0, float(evidence_value)))
        return 1.0 - (1.0 - r) * (1.0 - x)
    raise ValueError(resource_type.kind)


def update(resource_state: Mapping[str, Any], evidence: ResourceEvidence, resource_type: ResourceType, beta: float = 1.0, clause: CapabilityClause | None = None) -> Dict[str, Any]:
    new_state = dict(resource_state)
    current = new_state.get(resource_type.name, initial_value(resource_type))
    if evidence.missing or evidence.value is None:
        xbar = MissingEvidence(evidence.resource_name, reason=evidence.reason or "not_observed", evidence_source=evidence.source, confidence=evidence.confidence)
    else:
        xbar = conservative_value(evidence.value, evidence.sigma, resource_type, beta=beta)
    new_state[resource_type.name] = update_value(current, xbar, resource_type, evidence=evidence, clause=clause)
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


def _state_value(resource_state: Mapping[str, Any], clause: CapabilityClause, registry: ResourceRegistry) -> Any:
    rt = registry.get(clause.resource_name)
    return resource_state.get(clause.resource_name, initial_value(rt))


def satisfy(resource_state: Mapping[str, Any], clause: CapabilityClause, registry: ResourceRegistry = DEFAULT_REGISTRY, optional: bool = False) -> bool:
    rt = registry.get(clause.resource_name)
    value = _state_value(resource_state, clause, registry)
    if is_missing(value):
        return bool(optional or (not clause.hard) or clause.missing_policy == "allow_if_optional")
    if rt.kind == "lower" and (value is None or (isinstance(value, float) and math.isinf(value))):
        return False
    if rt.kind == "categorical":
        if isinstance(value, PredicateState):
            # PredicateState already includes the clause-specific result when the
            # resource was updated with this clause.  For safety, re-check the
            # current observed value against the requested clause.
            if value.required == clause.threshold and value.operator == clause.operator:
                return bool(value.ok)
            return bool(value.ok) and compatible(value.observed, clause.threshold, clause.operator)
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
    value = _state_value(resource_state, clause, registry)
    if is_missing(value):
        return -1.0
    if rt.kind == "categorical":
        return 1.0 if satisfy(resource_state, clause, registry) else -1.0
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


def confidence_margin(observed_confidence: float, min_confidence: float) -> float:
    return (float(observed_confidence) - float(min_confidence)) / (abs(float(min_confidence)) + EPS)


def _resource_no_worse(v1: Any, v2: Any, rt: ResourceType) -> bool:
    if is_missing(v1) and not is_missing(v2):
        return False
    if not is_missing(v1) and is_missing(v2):
        return True
    if is_missing(v1) and is_missing(v2):
        return True
    if rt.kind in ("cumulative", "upper", "probabilistic"):
        return float(v1) <= float(v2) + EPS
    if rt.kind == "lower":
        return float(v1) + EPS >= float(v2)
    if rt.kind == "categorical":
        return bool(v1) >= bool(v2)
    return False


def dominates(label1: Mapping[str, Any], label2: Mapping[str, Any], registry: ResourceRegistry = DEFAULT_REGISTRY) -> bool:
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


def active_groups(groups: Sequence[RequirementGroup], phases: Iterable[str]) -> List[RequirementGroup]:
    pset = set(phases)
    return [g for g in groups if pset.intersection(g.phase_scope) or "all" in g.phase_scope]


def group_clause_ids(groups: Sequence[RequirementGroup]) -> set[str]:
    return {cid for g in groups for cid in g.clause_ids}


def satisfy_group(resource_state: Mapping[str, Any], group: RequirementGroup, clauses_by_id: Mapping[str, CapabilityClause], registry: ResourceRegistry = DEFAULT_REGISTRY) -> Tuple[bool, Dict[str, float], List[str]]:
    margins: Dict[str, float] = {}
    failed: List[str] = []
    vals: List[bool] = []
    for cid in group.clause_ids:
        c = clauses_by_id.get(cid)
        if c is None:
            vals.append(False)
            margins[cid] = -1.0
            failed.append(cid)
            continue
        ok = satisfy(resource_state, c, registry, optional=not group.hard)
        m = signed_margin(resource_state, c, registry)
        margins[c.resource_name] = m
        vals.append(ok)
        if not ok:
            failed.append(c.resource_name)
    if group.logic == "all_of":
        ok = all(vals)
    elif group.logic == "any_of":
        ok = any(vals)
    elif group.logic == "not":
        ok = not any(vals)
    else:
        raise ValueError(group.logic)
    return ok, margins, failed if not ok else []


def satisfy_all(resource_state: Mapping[str, Any], clauses: Sequence[CapabilityClause], groups: Sequence[RequirementGroup] | None = None, registry: ResourceRegistry = DEFAULT_REGISTRY) -> Tuple[bool, Dict[str, float], List[str]]:
    groups = groups or []
    grouped = group_clause_ids(groups)
    clauses_by_id = {c.id: c for c in clauses}
    margins: Dict[str, float] = {}
    failed: List[str] = []
    ok_all = True
    for c in clauses:
        if c.id in grouped:
            continue
        ok = satisfy(resource_state, c, registry, optional=not c.hard)
        m = signed_margin(resource_state, c, registry)
        margins[c.resource_name] = m
        if not ok:
            ok_all = False
            failed.append(c.resource_name)
    for g in groups:
        gok, gm, gf = satisfy_group(resource_state, g, clauses_by_id, registry)
        margins.update({f"{g.group_id}:{k}": v for k, v in gm.items()})
        if not gok:
            ok_all = False
            failed.extend(gf or [g.group_id])
    return ok_all, margins, failed


def all_margins(resource_state: Mapping[str, Any], clauses: Sequence[CapabilityClause], registry: ResourceRegistry = DEFAULT_REGISTRY, groups: Sequence[RequirementGroup] | None = None) -> Dict[str, float]:
    return satisfy_all(resource_state, clauses, groups, registry)[1]


def best_group_margin(resource_state: Mapping[str, Any], group: RequirementGroup, clauses_by_id: Mapping[str, CapabilityClause], registry: ResourceRegistry = DEFAULT_REGISTRY) -> float:
    ok, margins, _ = satisfy_group(resource_state, group, clauses_by_id, registry)
    if group.logic == "any_of" and margins:
        return max(margins.values())
    if group.logic == "not" and margins:
        return -max(margins.values())
    return min(margins.values()) if margins else (1.0 if ok else -1.0)
