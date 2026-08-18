"""Publication evidence policy helpers for AbilityBench/CapPlan.

These helpers intentionally implement a conservative, fail-closed policy.  A
record is publication-grade only when the field value and its provenance are
both independently auditable; candidate semantics never become ground truth by
being copied into a different column.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional


PAPER_PHYSICAL_FIELDS = (
    "curb_height_m",
    "sidewalk_width_m",
    "deployment_clearance_m",
    "curb_ramp",
)


def as_boolish(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    s = str(value).strip().lower()
    if s in {"1", "true", "yes", "y", "allowed", "legal"}:
        return True
    if s in {"0", "false", "no", "n", "forbidden", "illegal"}:
        return False
    return None


def has_timezone_iso8601(value: Any) -> bool:
    """Return True only for an ISO-8601 datetime carrying an explicit offset."""
    s = str(value or "").strip()
    if not s:
        return False
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return False
    return dt.tzinfo is not None and dt.utcoffset() is not None


def source_tier(record: Dict[str, Any]) -> str:
    tier = str(record.get("evidence_tier") or record.get("source_tier") or "").strip()
    if tier:
        return tier
    if as_boolish(record.get("audited")) is True:
        return "A_manual_audit"
    if as_boolish(record.get("authoritative")) is True:
        return "A_authoritative"
    return "unknown"


def is_tier_a(record: Dict[str, Any]) -> bool:
    tier = source_tier(record).lower()
    return tier.startswith("a_") or tier == "a" or as_boolish(record.get("audited")) is True


def is_proxy_or_candidate(record: Dict[str, Any]) -> bool:
    tier = source_tier(record).lower()
    source = str(record.get("source") or "").lower()
    kind = str(record.get("kind") or "").lower()
    return (
        as_boolish(record.get("candidate_only")) is True
        or as_boolish(record.get("is_proxy")) is True
        or as_boolish(record.get("requires_manual_legality_audit")) is True
        or "proxy" in tier
        or "candidate" in tier
        or "proxy" in source
        or kind == "entrance_proxy"
    )


def legal_evidence_is_independent(record: Dict[str, Any]) -> bool:
    """Require positive/negative legality to have a Tier-A independent basis."""
    if not is_tier_a(record) or is_proxy_or_candidate(record):
        return False
    if as_boolish(record.get("legal_stop")) is None:
        return False
    basis = str(record.get("legal_basis") or "").strip()
    source = str(record.get("source") or record.get("regulation_id") or "").strip()
    if not basis or not source:
        return False
    bad = ("heuristic", "candidate", "no_matching", "no_legality", "unknown", "todo", "review", "verify")
    low = f"{basis} {source}".lower()
    return not any(token in low for token in bad)


def physical_field_is_paper_grade(record: Dict[str, Any], field: str) -> bool:
    if record.get(field) is None or not is_tier_a(record) or is_proxy_or_candidate(record):
        return False
    field_prov = record.get("field_provenance")
    if isinstance(field_prov, dict) and field in field_prov:
        p = field_prov[field]
        if isinstance(p, dict):
            return is_tier_a({**record, **p}) and not is_proxy_or_candidate({**record, **p})
    # Older official normalized records have one source/tier for all mapped fields.
    return True


def validate_physical_ranges(record: Dict[str, Any]) -> list[str]:
    errors: list[str] = []
    bounds = {
        "curb_height_m": (0.0, 0.50),
        "sidewalk_width_m": (0.10, 20.0),
        "deployment_clearance_m": (0.10, 20.0),
        "running_slope": (0.0, 1.0),
        "cross_slope": (0.0, 1.0),
    }
    for key, (lo, hi) in bounds.items():
        v = record.get(key)
        if v in (None, ""):
            continue
        try:
            fv = float(v)
        except (TypeError, ValueError):
            errors.append(f"{key}:non_numeric")
            continue
        if not (lo <= fv <= hi):
            errors.append(f"{key}:out_of_range[{lo},{hi}]")
    return errors
