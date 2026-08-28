#!/usr/bin/env python
"""Build an auditable hybrid PUDO truth layer for AbilityBench-AV.

The input remains geometry-anchored to real nuPlan/GIS candidates.  Observed or
source-prefilled values are never overwritten.  Only missing benchmark fields
are filled with deterministic physically-plausible simulated values.  Every
filled field carries explicit provenance and simulated values NEVER set the
paper_evidence_complete/paper_eligible flags.

This mode is intended for the paper's measured-or-simulated typed-resource
benchmark, not for claims about exact curb conditions or local law at a real
city location.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from capplan.utils.serialization import iter_jsonl, write_jsonl

VERSION = "abilitybench_hybrid_pudo_v7_20260828"
PHYSICAL_FIELDS = ("curb_height_m", "sidewalk_width_m", "deployment_clearance_m", "curb_ramp")
STATIC_TRANSFER_FIELDS = PHYSICAL_FIELDS
SIDE_SEMANTICS = "episode_route_relative_service_approach_relation"
REQUIRED_FIELDS = (*PHYSICAL_FIELDS, "legal_stop", "legal_basis", "side", "adjacent_ped_node_id")


def _blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _as_float(v: Any) -> Optional[float]:
    if _blank(v):
        return None
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except Exception:
        return None


def _valid_numeric_field(field: str, value: Any) -> Optional[float]:
    """Return a physically usable numeric PUDO value, else None.

    Base PUDO builders may emit sentinel values such as 0.0 for fields that
    were geometrically present but not actually measured.  Treating those as
    complete benchmark truth bypasses the hybrid evidence completion layer and
    leaves the final semantic audit without field-level provenance.
    """
    x = _as_float(value)
    if x is None:
        return None
    if field == "curb_height_m":
        return x if 0.0 <= x <= 0.50 else None
    if field == "sidewalk_width_m":
        return x if 0.05 < x <= 12.0 else None
    if field == "deployment_clearance_m":
        return x if 0.05 < x <= 8.0 else None
    if field == "blockage_risk":
        return x if 0.0 <= x <= 1.0 else None
    return x


def _numeric_missing(field: str, value: Any) -> bool:
    return _valid_numeric_field(field, value) is None


def _as_bool(v: Any) -> Optional[bool]:
    if isinstance(v, bool):
        return v
    if _blank(v):
        return None
    s = str(v).strip().lower()
    if s in {"1", "true", "yes", "y", "allowed", "legal"}:
        return True
    if s in {"0", "false", "no", "n", "disallowed", "illegal", "not_allowed"}:
        return False
    return None


def _seed(base: int, *parts: str) -> int:
    payload = "|".join([str(base), *map(str, parts)]).encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") & 0x7FFFFFFF


def _source_is_real(value: Any) -> bool:
    s = str(value or "").strip().lower()
    if not s:
        return False
    return not (s.startswith("synthetic") or "proxy" in s or "simulated" in s or s in {"unknown", "default", "toy", "mock"})


def _load_audit_map(path: Optional[str], split: str) -> Dict[str, Dict[str, Any]]:
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    out: Dict[str, Dict[str, Any]] = {}
    with p.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            anchors = str(row.get(f"candidate_anchor_ids_{split}") or "").split(";")
            for aid in anchors:
                aid = aid.strip()
                if aid:
                    out[aid] = dict(row)
    return out


def _audit_value(row: Mapping[str, Any], field: str) -> Any:
    if field in {"curb_height_m", "sidewalk_width_m", "deployment_clearance_m"}:
        return _valid_numeric_field(field, row.get(field))
    if field in {"curb_ramp", "legal_stop"}:
        return _as_bool(row.get(field))
    value = row.get(field)
    return None if _blank(value) else value


def _audit_provenance(audit: Mapping[str, Any], field: str) -> Optional[Dict[str, Any]]:
    value = _audit_value(audit, field)
    if value is None:
        return None
    if field in {"legal_stop", "legal_basis"}:
        source = audit.get("legal_stop_source") or audit.get("legal_basis_source")
        tier = audit.get("legal_stop_evidence_tier") or audit.get("legal_basis_evidence_tier")
        distance = audit.get("legal_stop_match_distance_m")
        as_of = audit.get("legal_stop_evidence_as_of")
        linkage = audit.get("legal_linkage_method")
    else:
        source = audit.get(f"{field}_source")
        tier = audit.get(f"{field}_evidence_tier")
        distance = audit.get(f"{field}_match_distance_m")
        as_of = audit.get(f"{field}_evidence_as_of")
        linkage = None
    # Existing human-entered rows may not have a source column.  Treat them as
    # observed only when the audit itself has reviewer/time evidence.
    human = bool(str(audit.get("auditor_id") or "").strip() and str(audit.get("observed_at") or "").strip())
    if not _source_is_real(source) and not human:
        return None
    return {
        "kind": "observed",
        "source": str(source or f"manual_audit:{audit.get('audit_id') or 'unknown'}"),
        "evidence_tier": str(tier or ("A_manual_observation" if human else "unknown")),
        "method": "source_prefill_or_manual_audit",
        "audit_id": audit.get("audit_id"),
        "match_distance_m": _as_float(distance),
        "evidence_as_of": as_of or audit.get("observed_at"),
        "linkage_method": linkage,
    }


def _base_provenance(row: Mapping[str, Any], field: str) -> Optional[Dict[str, Any]]:
    value = row.get(field)
    if field in {"curb_height_m", "sidewalk_width_m", "deployment_clearance_m"}:
        value = _valid_numeric_field(field, value)
    elif field in {"curb_ramp", "legal_stop"}:
        value = _as_bool(value)
    if value is None or _blank(value):
        return None
    fp = row.get("field_provenance")
    if isinstance(fp, Mapping) and isinstance(fp.get(field), Mapping):
        p = dict(fp[field])
        if str(p.get("kind") or "").lower() in {"observed", "derived", "simulated"}:
            return p
    source = row.get(f"{field}_source") or (row.get("legal_stop_source") if field.startswith("legal") else None) or row.get("source")
    if field in {"legal_stop", "legal_basis"} and _source_is_real(source):
        linkage = str(row.get("legal_linkage_method") or "").lower()
        tier = str(row.get("evidence_tier") or row.get("legal_stop_evidence_tier") or "").lower()
        if linkage in {"authoritative_source_relation", "direct_feature_relation"} or tier.startswith("a_"):
            return {"kind": "observed", "source": str(source), "method": "explicit_authoritative_source_semantics", "linkage_method": linkage or None}
    if bool(row.get("paper_evidence_complete")) and _source_is_real(source):
        return {"kind": "observed", "source": str(source), "method": "preexisting_paper_audited_evidence"}
    # Geometry/lane-side and pedestrian binding are allowed as map-derived facts.
    if field in {"side", "adjacent_ped_node_id"} and value not in {None, "", "unknown"}:
        return {"kind": "derived", "source": str(source or "nuplan_gis_geometry"), "method": "geometry_or_topology_binding"}
    return None


def _sim_prov(city: str, split: str, episode_id: str, anchor_id: str, field: str, seed: int, profile: str, method: str) -> Dict[str, Any]:
    return {
        "kind": "simulated",
        "source": VERSION,
        "method": method,
        "seed": seed,
        "city_context": city,
        "split": split,
        "episode_id": episode_id,
        "anchor_id": anchor_id,
        "standard_profile": profile,
        "claim_scope": "benchmark_scenario_truth_not_real_site_ground_truth",
    }


def _profile(city: str) -> Dict[str, Any]:
    if city == "singapore":
        return {
            "name": "SG_physically_plausible_accessibility_prior_v1",
            "positive_sidewalk": (1.35, 3.20),
            "positive_clearance": (1.55, 2.20),
            "narrow_sidewalk": (0.80, 1.20),
            "narrow_clearance": (0.65, 1.10),
            "standard_reference": "Singapore BCA Code on Accessibility 2025 used as design-context reference; numeric simulation ranges are benchmark priors, not site measurements.",
        }
    return {
        "name": "US_accessible_loading_geometry_prior_v1",
        "positive_sidewalk": (1.20, 3.40),
        "positive_clearance": (1.55, 2.25),
        "narrow_sidewalk": (0.75, 1.10),
        "narrow_clearance": (0.65, 1.10),
        "standard_reference": "ADA/PROWAG passenger-loading access aisle 60 in (1.525 m) used as a plausibility lower-bound for accessible simulated loading scenarios; not a claim about the mapped site.",
    }


def _xy(row: Mapping[str, Any]) -> tuple[Optional[float], Optional[float]]:
    x = _as_float(row.get("x")); y = _as_float(row.get("y"))
    if x is not None and y is not None:
        return x, y
    pose = row.get("curb_pose") if isinstance(row.get("curb_pose"), Mapping) else {}
    return _as_float(pose.get("x")), _as_float(pose.get("y"))


def _site_key(row: Mapping[str, Any], city: str, radius_m: float = 5.0) -> str:
    """Stable physical-site key shared across episodes and official splits.

    Static simulated curb/interface facts must not flip just because the same
    curb is observed in another nuPlan snapshot.  Prefer the stable adjacent
    pedestrian node and include a 5 m geometry cell as a guard.  The route-
    relative ``side`` relation is deliberately NOT part of this identity.
    """
    x, y = _xy(row)
    q = "unknown"
    if x is not None and y is not None:
        q = f"{round(x/max(radius_m,0.1))}:{round(y/max(radius_m,0.1))}"
    ped = str(row.get("adjacent_ped_node_id") or "")
    lane = str(row.get("lane_id") or row.get("roadblock_id") or "")
    return f"{city}|{ped or 'no_ped'}|{lane or 'no_lane'}|{q}"


def _static_site_class(rng: random.Random) -> str:
    # Most urban curb candidates remain operationally usable; the minority of
    # coherent negative sites create T4/T5 counterfactual pressure without an
    # IID negative on every nearby anchor.
    u = rng.random()
    if u < 0.78: return "accessible_loading"
    if u < 0.85: return "narrow_clearance"
    if u < 0.90: return "no_curb_ramp"
    if u < 0.94: return "high_curb"
    return "simulated_loading_prohibited"


def _observed_site_class(row: Mapping[str, Any]) -> Optional[str]:
    """Return a site class forced by real/derived evidence, if any.

    The hybrid prior must complete missing facts *around* observed evidence, not
    sample a contradictory latent class.  The precedence is conservative:
    explicit prohibition > high curb > narrow interface > missing ramp >
    observed accessible ramp/flush curb.
    """
    legal = _as_bool(row.get("legal_stop"))
    curb_h = _valid_numeric_field("curb_height_m", row.get("curb_height_m"))
    sw = _valid_numeric_field("sidewalk_width_m", row.get("sidewalk_width_m"))
    cl = _valid_numeric_field("deployment_clearance_m", row.get("deployment_clearance_m"))
    ramp = _as_bool(row.get("curb_ramp"))
    if legal is False:
        return "simulated_loading_prohibited"
    if curb_h is not None and curb_h >= 0.18:
        return "high_curb"
    if (sw is not None and sw < 1.20) or (cl is not None and cl < 1.20):
        return "narrow_clearance"
    if ramp is False:
        return "no_curb_ramp"
    if ramp is True or (curb_h is not None and curb_h <= 0.04):
        return "accessible_loading"
    return None


def _resolve_site_class(observed: Iterable[str], rng: random.Random) -> str:
    """Resolve one physical-site latent class shared across snapshots."""
    forced = set(observed)
    for cls in (
        "simulated_loading_prohibited",
        "high_curb",
        "narrow_clearance",
        "no_curb_ramp",
        "accessible_loading",
    ):
        if cls in forced:
            return cls
    return _static_site_class(rng)


def _canonical_site_static_evidence(
    prepared_by_episode: Mapping[str, List[tuple[Dict[str, Any], Dict[str, Any]]]],
    city: str,
) -> tuple[Dict[str, Dict[str, tuple[Any, Dict[str, Any]]]], Counter, List[Dict[str, Any]]]:
    """Collect stable observed/derived site facts and make them reusable.

    Repeated nuPlan snapshots often revisit the same physical curb.  If one
    snapshot has an observed/derived static interface fact and another snapshot
    is missing it, independently simulating the latter throws away real
    evidence and can make one curb change width/height across time.  We transfer
    only evidence-backed *immutable physical* fields and never dynamic blockage
    or the episode-route-relative left/right service relation.  Conflicting
    physical evidence is reported and left untouched rather than silently averaged.
    """
    vals: Dict[str, Dict[str, List[tuple[Any, Dict[str, Any]]]]] = defaultdict(lambda: defaultdict(list))
    for prepared in prepared_by_episode.values():
        for row, prov in prepared:
            key = _site_key(row, city)
            for field in STATIC_TRANSFER_FIELDS:
                pv = prov.get(field) if isinstance(prov.get(field), Mapping) else None
                if not isinstance(pv, Mapping) or str(pv.get("kind") or "").lower() not in {"observed", "derived", "observed_or_derived"}:
                    continue
                value: Any
                if field in {"curb_height_m", "sidewalk_width_m", "deployment_clearance_m"}:
                    value = _valid_numeric_field(field, row.get(field))
                elif field == "curb_ramp":
                    value = _as_bool(row.get(field))
                else:
                    value = None if _blank(row.get(field)) or str(row.get(field)).lower() == "unknown" else str(row.get(field)).lower()
                if value is not None:
                    vals[key][field].append((value, dict(pv)))

    canonical: Dict[str, Dict[str, tuple[Any, Dict[str, Any]]]] = defaultdict(dict)
    counts = Counter()
    conflicts: List[Dict[str, Any]] = []
    tolerances = {"curb_height_m": 0.03, "sidewalk_width_m": 0.25, "deployment_clearance_m": 0.25}
    for key, fields in vals.items():
        for field, observations in fields.items():
            raw_values = [x[0] for x in observations]
            if field in tolerances:
                xs = sorted(float(x) for x in raw_values)
                spread = xs[-1] - xs[0]
                if spread > tolerances[field]:
                    conflicts.append({"physical_site_key": key, "field": field, "values": xs[:20], "spread": spread})
                    counts[f"conflict:{field}"] += 1
                    continue
                value = xs[len(xs) // 2] if len(xs) % 2 else (xs[len(xs)//2 - 1] + xs[len(xs)//2]) / 2.0
                value = round(value, 4)
            else:
                unique = {str(x) for x in raw_values}
                if len(unique) != 1:
                    conflicts.append({"physical_site_key": key, "field": field, "values": sorted(unique)[:20]})
                    counts[f"conflict:{field}"] += 1
                    continue
                value = raw_values[0]
            sources = sorted({str(pv.get("source") or "unknown") for _v, pv in observations})
            kinds = sorted({str(pv.get("kind") or "unknown") for _v, pv in observations})
            canonical[key][field] = (value, {
                "kind": "derived",
                "source": ";".join(sources[:8]),
                "method": "same_physical_site_static_evidence_transfer",
                "physical_site_key": key,
                "upstream_evidence_kinds": kinds,
                "supporting_observation_count": len(observations),
                "claim_scope": "static_site_evidence_reused_across_nuplan_snapshots",
            })
            counts[f"canonical:{field}"] += 1
    return canonical, counts, conflicts


def _apply_site_static_evidence(
    row: MutableMapping[str, Any],
    prov: MutableMapping[str, Any],
    site_key: str,
    canonical: Mapping[str, Mapping[str, tuple[Any, Dict[str, Any]]]],
    counts: Counter,
) -> None:
    facts = canonical.get(site_key) or {}
    for field, (value, pv) in facts.items():
        current_missing = (
            _numeric_missing(field, row.get(field)) if field in {"curb_height_m", "sidewalk_width_m", "deployment_clearance_m"}
            else _as_bool(row.get(field)) is None if field == "curb_ramp"
            else _blank(row.get(field)) or str(row.get(field)).lower() == "unknown"
        )
        if current_missing:
            row[field] = value
            prov[field] = dict(pv)
            counts[f"transferred:{field}"] += 1


def _scenario_from_row(row: Mapping[str, Any]) -> str:
    legal = _as_bool(row.get("legal_stop"))
    blocked = float(row.get("blockage_risk") or 0.0) >= 0.85
    curb_h = _valid_numeric_field("curb_height_m", row.get("curb_height_m"))
    sw = _valid_numeric_field("sidewalk_width_m", row.get("sidewalk_width_m"))
    cl = _valid_numeric_field("deployment_clearance_m", row.get("deployment_clearance_m"))
    ramp = _as_bool(row.get("curb_ramp"))
    if legal is False:
        base = "simulated_or_observed_loading_prohibited"
    elif curb_h is not None and curb_h >= 0.18:
        base = "high_curb"
    elif ramp is False:
        base = "no_curb_ramp"
    elif (sw is not None and sw < 1.20) or (cl is not None and cl < 1.20):
        base = "narrow_clearance"
    else:
        base = "accessible_loading"
    return base + ("+temporary_blockage" if blocked else "")

def _default_curb_side(city: str) -> str:
    # Singapore keeps left; the three US nuPlan cities keep right.  This is a
    # traffic-side prior only when map geometry did not already determine side.
    return "left" if city == "singapore" else "right"


def _fill_missing(
    row: MutableMapping[str, Any],
    prov: MutableMapping[str, Any],
    *,
    city: str,
    split: str,
    site_class: str,
    site_seed: int,
    dynamic_seed: int,
    site_key: str,
    profile: Mapping[str, Any],
) -> None:
    static_rng = random.Random(site_seed)
    dyn_rng = random.Random(dynamic_seed)
    profile_name = str(profile["name"])
    eid = str(row.get("episode_id")); aid = str(row.get("anchor_id") or row.get("pudo_id"))

    def put_static(field: str, value: Any, method: str) -> None:
        missing = (
            _numeric_missing(field, row.get(field))
            if field in {"curb_height_m", "sidewalk_width_m", "deployment_clearance_m"}
            else (_as_bool(row.get(field)) is None if field == "curb_ramp" else row.get(field) is None or _blank(row.get(field)) or (field == "side" and str(row.get(field)).lower() == "unknown"))
        )
        if missing:
            row[field] = value
            pv = _sim_prov(city, split, eid, aid, field, site_seed, profile_name, method)
            pv.update({"physical_site_key": site_key, "correlation_scope": "physical_site_across_splits"})
            prov[field] = pv

    def put_dynamic(field: str, value: Any, method: str) -> None:
        if row.get(field) is None or _blank(row.get(field)):
            row[field] = value
            pv = _sim_prov(city, split, eid, aid, field, dynamic_seed, profile_name, method)
            pv.update({"physical_site_key": site_key, "correlation_scope": "episode_time_at_physical_site"})
            prov[field] = pv

    def put_relational(field: str, value: Any, method: str) -> None:
        """Fill an episode/route-relative service relation without making it a site fact."""
        if row.get(field) is None or _blank(row.get(field)) or str(row.get(field)).lower() == "unknown":
            row[field] = value
            pv = _sim_prov(city, split, eid, aid, field, dynamic_seed, profile_name, method)
            pv.update({
                "physical_site_key": site_key,
                "semantic_scope": "episode_route_relative_service_relation",
                "correlation_scope": "episode_route_approach",
                "fallback_reason": "route_geometry_side_unknown",
            })
            prov[field] = pv

    if site_class == "narrow_clearance":
        sw_lo, sw_hi = profile["narrow_sidewalk"]
        cl_lo, cl_hi = profile["narrow_clearance"]
    else:
        sw_lo, sw_hi = profile["positive_sidewalk"]
        cl_lo, cl_hi = profile["positive_clearance"]
    sidewalk = round(static_rng.uniform(float(sw_lo), float(sw_hi)), 3)
    clearance = round(static_rng.uniform(float(cl_lo), float(cl_hi)), 3)

    observed_ramp = _as_bool(row.get("curb_ramp"))
    observed_curb_h = _valid_numeric_field("curb_height_m", row.get("curb_height_m"))
    # Keep simulated curb height/ramp mutually coherent with any observed
    # counterpart. A verified ramp should not be paired with a simulated
    # 25-cm barrier at the same anchor, and an observed missing-ramp curb should
    # not be completed as a nearly flush 1-cm lip.
    if observed_ramp is True:
        ramp = True
        curb_h = round(static_rng.uniform(0.0, 0.035), 3)
    elif observed_ramp is False:
        ramp = False
        curb_h = round(static_rng.uniform(0.18, 0.25), 3) if site_class == "high_curb" else round(static_rng.uniform(0.08, 0.18), 3)
    elif observed_curb_h is not None:
        curb_h = observed_curb_h
        ramp = bool(observed_curb_h <= 0.04) if site_class not in {"no_curb_ramp", "high_curb"} else False
    elif site_class == "high_curb":
        ramp = False; curb_h = round(static_rng.uniform(0.18, 0.25), 3)
    elif site_class == "no_curb_ramp":
        ramp = False; curb_h = round(static_rng.uniform(0.10, 0.18), 3)
    else:
        # Accessible sites are not all flush.  A majority have a curb ramp/low
        # lip; others retain a normal curb that can still be served by a vehicle
        # ramp/lift, creating meaningful interface dependence.
        ramp = static_rng.random() < (0.82 if city == "singapore" else 0.76)
        curb_h = round(static_rng.uniform(0.0, 0.035), 3) if ramp else round(static_rng.uniform(0.08, 0.16), 3)

    put_static("sidewalk_width_m", sidewalk, "site_correlated_pedestrian_clear_width_prior")
    put_static("deployment_clearance_m", clearance, "site_correlated_loading_clear_space_prior")
    put_static("curb_ramp", ramp, "site_correlated_curb_ramp_prior")
    put_static("curb_height_m", curb_h, "site_correlated_curb_height_prior")
    if str(row.get("side") or "unknown").lower() == "unknown":
        put_relational("side", _default_curb_side(city), "city_driving_side_prior_when_route_geometry_unknown")

    if _as_bool(row.get("legal_stop")) is None:
        legal = site_class != "simulated_loading_prohibited"
        row["legal_stop"] = legal
        pv = _sim_prov(city, split, eid, aid, "legal_stop", site_seed, profile_name, "site_correlated_simulated_service_permission")
        pv.update({"physical_site_key": site_key, "correlation_scope": "physical_site_across_splits"})
        prov["legal_stop"] = pv
    if _blank(row.get("legal_basis")):
        legal = bool(_as_bool(row.get("legal_stop")))
        row["legal_basis"] = "SIMULATED_BENCHMARK_LOADING_PERMISSION" if legal else "SIMULATED_BENCHMARK_LOADING_PROHIBITION"
        pv = _sim_prov(city, split, eid, aid, "legal_basis", site_seed, profile_name, "site_correlated_legal_context_not_municipal_law")
        pv.update({"physical_site_key": site_key, "correlation_scope": "physical_site_across_splits"})
        prov["legal_basis"] = pv
        if "legal_stop" not in prov:
            prov["legal_stop"] = dict(pv)
    if not _source_is_real(row.get("legal_stop_source")) and str(prov.get("legal_stop", {}).get("kind")) == "simulated":
        row["legal_stop_source"] = VERSION + ":simulated_service_permission"

    # Dynamic availability is intentionally episode/time dependent even at the
    # same physical site. Existing observed blockage is never overwritten.
    if _valid_numeric_field("blockage_risk", row.get("blockage_risk")) is None:
        blocked = dyn_rng.random() < 0.05
        row["blockage_risk"] = round(dyn_rng.uniform(0.88, 0.98) if blocked else dyn_rng.uniform(0.01, 0.10), 3)
        pv = _sim_prov(city, split, eid, aid, "blockage_risk", dynamic_seed, profile_name, "episode_time_dynamic_blockage_prior")
        pv.update({"physical_site_key": site_key, "correlation_scope": "episode_time_at_physical_site"})
        prov["blockage_risk"] = pv
    elif "blockage_risk" not in prov:
        # Base nuPlan PUDO candidates compute blockage risk from the nearest
        # dynamic agent in scene history.  Earlier hybrid versions preserved
        # that score but forgot its field-level provenance, causing the final
        # semantic audit to flag exactly one missing core provenance item per
        # retained PUDO.  Preserve the value and describe its evidence rather
        # than silently re-simulating it.
        base_source = str(row.get("source") or "").lower()
        if base_source.startswith("nuplan_route"):
            pv = {
                "kind": "derived",
                "source": "nuplan_agent_history",
                "method": "nearest_agent_distance_dynamic_blockage_risk",
                "episode_id": eid,
                "anchor_id": aid,
                "city_context": city,
                "split": split,
                "physical_site_key": site_key,
                "correlation_scope": "episode_time_at_physical_site",
                "claim_scope": "scene_derived_dynamic_benchmark_evidence",
            }
        else:
            pv = _sim_prov(
                city, split, eid, aid, "blockage_risk", dynamic_seed,
                profile_name, "preserved_preexisting_dynamic_score_without_audited_source",
            )
            pv.update({
                "physical_site_key": site_key,
                "correlation_scope": "episode_time_at_physical_site",
            })
        prov["blockage_risk"] = pv

    # Lighting/shelter are site amenities, not IID edge noise.  Time-of-day is
    # resolved later in TransitionGenerator using the nuPlan request timestamp.
    if row.get("lighting") is None:
        lit_probability = 0.94 if city == "singapore" else 0.90
        put_static("lighting", "lit" if static_rng.random() < lit_probability else "unlit", "site_correlated_lighting_infrastructure_prior")
    if row.get("shelter") is None:
        shelter_probability = 0.18 if city == "singapore" else 0.10
        put_static("shelter", bool(static_rng.random() < shelter_probability), "site_correlated_wait_shelter_prior")

    if row.get("map_confidence") is None:
        row["map_confidence"] = 0.85
        pv = _sim_prov(city, split, eid, aid, "map_confidence", site_seed, profile_name, "benchmark_default_map_confidence")
        pv.update({"physical_site_key": site_key})
        prov["map_confidence"] = pv
    if row.get("dynamic_confidence") is None:
        row["dynamic_confidence"] = 0.95
        pv = _sim_prov(city, split, eid, aid, "dynamic_confidence", dynamic_seed, profile_name, "simulator_state_confidence")
        pv.update({"physical_site_key": site_key})
        prov["dynamic_confidence"] = pv

def _ensure_core_provenance(
    row: MutableMapping[str, Any],
    prov: MutableMapping[str, Any],
    *,
    city: str,
    split: str,
    site_seed: int,
    dynamic_seed: int,
    site_key: str,
    profile: Mapping[str, Any],
    counters: Counter,
) -> None:
    """Guarantee every retained PUDO core field has explicit provenance.

    This is deliberately a provenance backstop, not a way to bless unknown data
    as measured evidence. Values preserved from older base PUDO artifacts without
    an auditable source are marked simulated benchmark truth, while invalid
    numeric sentinels should already have been replaced by _fill_missing().
    """
    eid = str(row.get("episode_id")); aid = str(row.get("anchor_id") or row.get("pudo_id"))
    profile_name = str(profile["name"])
    core_fields = ("curb_height_m", "sidewalk_width_m", "deployment_clearance_m", "curb_ramp", "legal_stop", "side", "blockage_risk")
    for field in core_fields:
        if isinstance(prov.get(field), Mapping):
            continue
        if field in {"curb_height_m", "sidewalk_width_m", "deployment_clearance_m", "blockage_risk"}:
            if _valid_numeric_field(field, row.get(field)) is None:
                continue
        elif field in {"curb_ramp", "legal_stop"}:
            if _as_bool(row.get(field)) is None:
                continue
        elif _blank(row.get(field)) or str(row.get(field)).lower() == "unknown":
            continue

        seed = dynamic_seed if field in {"blockage_risk", "side"} else site_seed
        method = {
            "blockage_risk": "preserved_preexisting_dynamic_score_without_audited_source",
            "side": "preserved_preexisting_route_side_without_audited_source",
        }.get(field, "preserved_preexisting_core_value_without_audited_source")
        pv = _sim_prov(city, split, eid, aid, field, seed, profile_name, method)
        pv.update({"physical_site_key": site_key})
        if field == "blockage_risk":
            pv.update({"correlation_scope": "episode_time_at_physical_site"})
        elif field == "side":
            pv.update({
                "semantic_scope": "episode_route_relative_service_relation",
                "correlation_scope": "episode_route_approach",
                "claim_scope": "benchmark_route_relation_not_static_site_ground_truth",
            })
        else:
            pv.update({"correlation_scope": "physical_site_across_splits"})
        prov[field] = pv
        counters[f"backfilled:{field}"] += 1

def _copy_observed_from_audit(row: MutableMapping[str, Any], audit: Mapping[str, Any], prov: MutableMapping[str, Any]) -> None:
    for field in (*PHYSICAL_FIELDS, "legal_stop", "legal_basis"):
        p = _audit_provenance(audit, field)
        if p is None:
            continue
        value = _audit_value(audit, field)
        if value is None:
            continue
        row[field] = value
        prov[field] = p
        if field == "legal_stop" and audit.get("legal_stop_source"):
            row["legal_stop_source"] = audit.get("legal_stop_source")


def _normalize_base(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row)
    for field in ("curb_height_m", "sidewalk_width_m", "deployment_clearance_m"):
        out[field] = _valid_numeric_field(field, out.get(field))
    for field in ("legal_stop", "curb_ramp"):
        val = _as_bool(out.get(field))
        if val is not None:
            out[field] = val
    # Preserve UNKNOWN. Turning a missing dynamic state into 0.0 would silently
    # assert "definitely unblocked" and prevent the hybrid dynamic prior from
    # being applied. Eligibility code already treats None conservatively after
    # the overlay has had a chance to fill it.
    out["blockage_risk"] = _valid_numeric_field("blockage_risk", out.get("blockage_risk"))
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input_pudo_jsonl", required=True)
    p.add_argument("--output_pudo_jsonl", required=True)
    p.add_argument("--city", required=True, choices=["boston", "pittsburgh", "vegas", "singapore"])
    p.add_argument("--split", required=True, choices=["train", "val", "test"])
    p.add_argument("--audit_worklist_csv", default=None, help="Optional source-prefilled site worklist from prepare_pudo_audit_worklist.py")
    p.add_argument("--site_evidence_peer_jsonl", action="append", default=[], metavar="SPLIT=PATH", help="Additional base-PUDO split used only to construct cross-split canonical static site evidence; may be repeated.")
    p.add_argument("--seed", type=int, default=20260822)
    p.add_argument("--min_positive_per_episode", type=int, default=2)
    p.add_argument("--report_json", required=True)
    args = p.parse_args()

    base_rows = [_normalize_base(dict(r)) for r in iter_jsonl(args.input_pudo_jsonl)]
    if not base_rows:
        raise RuntimeError("input PUDO JSONL is empty")
    audit_map = _load_audit_map(args.audit_worklist_csv, args.split)
    profile = _profile(args.city)
    by_episode: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in base_rows:
        eid = str(row.get("episode_id") or "")
        aid = str(row.get("anchor_id") or row.get("pudo_id") or "")
        if eid and aid:
            by_episode[eid].append(row)

    output: List[Dict[str, Any]] = []
    field_kinds: Dict[str, Counter] = defaultdict(Counter)
    scenarios = Counter()
    curb_sides = Counter()
    physical_site_keys: set[str] = set()
    numeric_minmax: Dict[str, List[Optional[float]]] = {k: [None, None] for k in ("curb_height_m", "sidewalk_width_m", "deployment_clearance_m", "blockage_risk")}
    complete_eps = 0
    eligible_eps = 0
    insufficient_eps: List[Dict[str, Any]] = []
    simulated_rows = 0
    core_provenance_backfills: Counter = Counter()

    # Apply all available evidence before sampling any static site class.  This
    # lets the same physical curb reuse one evidence-consistent latent class
    # across temporal snapshots and official splits rather than contradicting an
    # observed ramp/width/legality on another row for that site.
    prepared_by_episode: Dict[str, List[tuple[Dict[str, Any], Dict[str, Any]]]] = {}
    observed_classes_by_site: Dict[str, List[str]] = defaultdict(list)
    for eid, rows in sorted(by_episode.items()):
        prepared: List[tuple[Dict[str, Any], Dict[str, Any]]] = []
        for row in sorted(rows, key=lambda r: str(r.get("anchor_id") or r.get("pudo_id"))):
            aid = str(row.get("anchor_id") or row.get("pudo_id"))
            prov: Dict[str, Any] = dict(row.get("field_provenance") or {}) if isinstance(row.get("field_provenance"), Mapping) else {}
            for field in REQUIRED_FIELDS:
                if field in prov:
                    continue
                bp = _base_provenance(row, field)
                if bp is not None:
                    prov[field] = bp
            if "legal_stop" not in prov:
                row["legal_stop"] = None
                if str(row.get("legal_basis") or "").lower().startswith(("unknown", "no_", "bootstrap")):
                    row["legal_basis"] = None
            audit = audit_map.get(aid)
            if audit:
                _copy_observed_from_audit(row, audit, prov)
                row["hybrid_site_audit_id"] = audit.get("audit_id")
                if audit.get("entrance_candidate_id"):
                    row["entrance_candidate_id"] = audit.get("entrance_candidate_id")
                    row["entrance_candidate_source"] = audit.get("entrance_candidate_source")
                    row["entrance_candidate_match_distance_m"] = _as_float(audit.get("entrance_candidate_match_distance_m"))
            site_key = _site_key(row, args.city)
            forced = _observed_site_class(row)
            if forced is not None:
                observed_classes_by_site[site_key].append(forced)
            prepared.append((row, prov))
        prepared_by_episode[eid] = prepared

    # Canonical static site evidence must be global to the official split set.
    # The previous implementation only saw the current split while claiming
    # cross-split consistency; a site observed in train could therefore be
    # independently simulated in val/test.  Peer inputs fix that without
    # changing dynamic blockage, which intentionally remains episode-specific.
    site_evidence_prepared: Dict[str, List[tuple[Dict[str, Any], Dict[str, Any]]]] = {
        f"{args.split}:{eid}": rows for eid, rows in prepared_by_episode.items()
    }
    peer_rows_loaded = 0
    for spec in args.site_evidence_peer_jsonl:
        if "=" not in str(spec):
            raise RuntimeError(f"--site_evidence_peer_jsonl expects SPLIT=PATH, got {spec!r}")
        peer_split, peer_path = str(spec).split("=", 1)
        peer_split = peer_split.strip(); peer_path = peer_path.strip()
        if peer_split not in {"train", "val", "test"}:
            raise RuntimeError(f"invalid peer split {peer_split!r}")
        pp = Path(peer_path)
        if pp.resolve() == Path(args.input_pudo_jsonl).resolve():
            continue
        if not pp.exists():
            raise FileNotFoundError(pp)
        peer_audit_map = _load_audit_map(args.audit_worklist_csv, peer_split)
        grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        for raw in iter_jsonl(pp):
            row = _normalize_base(dict(raw)); eid = str(row.get("episode_id") or "")
            if eid:
                grouped[eid].append(row); peer_rows_loaded += 1
        for eid, rows in grouped.items():
            prepared_peer: List[tuple[Dict[str, Any], Dict[str, Any]]] = []
            for row in sorted(rows, key=lambda r: str(r.get("anchor_id") or r.get("pudo_id"))):
                aid = str(row.get("anchor_id") or row.get("pudo_id"))
                prov: Dict[str, Any] = dict(row.get("field_provenance") or {}) if isinstance(row.get("field_provenance"), Mapping) else {}
                for field in REQUIRED_FIELDS:
                    if field not in prov:
                        bp = _base_provenance(row, field)
                        if bp is not None:
                            prov[field] = bp
                if "legal_stop" not in prov:
                    row["legal_stop"] = None
                    if str(row.get("legal_basis") or "").lower().startswith(("unknown", "no_", "bootstrap")):
                        row["legal_basis"] = None
                audit = peer_audit_map.get(aid)
                if audit:
                    _copy_observed_from_audit(row, audit, prov)
                site_key = _site_key(row, args.city)
                forced = _observed_site_class(row)
                if forced is not None:
                    observed_classes_by_site[site_key].append(forced)
                prepared_peer.append((row, prov))
            site_evidence_prepared[f"{peer_split}:{eid}"] = prepared_peer

    site_static_evidence, site_static_counts, site_static_conflicts = _canonical_site_static_evidence(site_evidence_prepared, args.city)
    for prepared in prepared_by_episode.values():
        for row, prov in prepared:
            _apply_site_static_evidence(row, prov, _site_key(row, args.city), site_static_evidence, site_static_counts)

    site_class_by_key: Dict[str, str] = {}
    for prepared in prepared_by_episode.values():
        for row, _prov in prepared:
            site_key = _site_key(row, args.city)
            if site_key not in site_class_by_key:
                site_seed = _seed(args.seed, args.city, site_key, "static")
                site_class_by_key[site_key] = _resolve_site_class(
                    observed_classes_by_site.get(site_key, []), random.Random(site_seed)
                )
    site_prior_classes = Counter(site_class_by_key.values())

    for eid, prepared in sorted(prepared_by_episode.items()):
        # Static simulated physical/site facts are keyed by physical location,
        # not by episode/split. This prevents the same curb from becoming narrow
        # in train and wide in test merely because it was observed at another time.
        eligible_count = 0
        complete_count = 0
        for idx, (row, prov) in enumerate(prepared):
            aid = str(row.get("anchor_id") or row.get("pudo_id"))
            site_key = _site_key(row, args.city)
            site_seed = _seed(args.seed, args.city, site_key, "static")
            dynamic_seed = _seed(args.seed, args.city, eid, site_key, "dynamic")
            site_class = site_class_by_key[site_key]
            _fill_missing(
                row, prov, city=args.city, split=args.split, site_class=site_class,
                site_seed=site_seed, dynamic_seed=dynamic_seed, site_key=site_key,
                profile=profile,
            )
            _ensure_core_provenance(
                row, prov, city=args.city, split=args.split,
                site_seed=site_seed, dynamic_seed=dynamic_seed, site_key=site_key,
                profile=profile, counters=core_provenance_backfills,
            )
            scenario = _scenario_from_row(row)
            physical_site_keys.add(site_key)
            curb_sides[str(row.get("side") or "unknown")] += 1
            for field, bounds in numeric_minmax.items():
                val = _valid_numeric_field(field, row.get(field))
                if val is not None:
                    bounds[0] = val if bounds[0] is None else min(float(bounds[0]), val)
                    bounds[1] = val if bounds[1] is None else max(float(bounds[1]), val)

            simulated = any(isinstance(v, Mapping) and str(v.get("kind")) == "simulated" for v in prov.values())
            if simulated:
                simulated_rows += 1
                row["paper_evidence_complete"] = False
                row["paper_eligible"] = False
                row["paper_claim_allowed"] = False
            else:
                row["paper_claim_allowed"] = bool(row.get("paper_claim_allowed", True))

            missing = []
            for field in REQUIRED_FIELDS:
                val = row.get(field)
                if field == "legal_stop":
                    if _as_bool(val) is None: missing.append(field)
                elif field in {"curb_height_m", "sidewalk_width_m", "deployment_clearance_m"}:
                    if _numeric_missing(field, val): missing.append(field)
                elif field == "curb_ramp":
                    if _as_bool(val) is None: missing.append(field)
                elif _blank(val) or (field == "side" and str(val).lower() == "unknown"):
                    missing.append(field)
                if field not in prov:
                    missing.append(field + ":provenance")
            complete = not missing
            legal = bool(_as_bool(row.get("legal_stop")))
            blockage = _valid_numeric_field("blockage_risk", row.get("blockage_risk"))
            clearance_ok = _valid_numeric_field("deployment_clearance_m", row.get("deployment_clearance_m")) is not None
            eligible = complete and legal and float(blockage if blockage is not None else 1.0) < 0.85 and clearance_ok
            row.update({
                "truth_mode": "hybrid_geometry_anchored_site_correlated_simulated_interface_v7",
                "evidence_kind": "mixed" if simulated and any(isinstance(v, Mapping) and str(v.get("kind")) in {"observed", "derived"} for v in prov.values()) else ("simulated" if simulated else "observed_or_derived"),
                "field_provenance": prov,
                "hybrid_evidence_complete": complete,
                "hybrid_eligible": eligible,
                "deployment_clearance_semantics": "available_environment_clear_space",
                "hybrid_scenario_class": scenario,
                "hybrid_site_prior_class": site_class,
                "hybrid_seed": site_seed,
                "hybrid_dynamic_seed": dynamic_seed,
                "hybrid_physical_site_key": site_key,
                "hybrid_standard_profile": profile["name"],
                "hybrid_missing_fields": sorted(set(missing)),
                "evidence_status": "hybrid_ready" if complete else "hybrid_incomplete",
                "source": str(row.get("source") or "geometry_anchored_pudo") + ("+" + VERSION if simulated else ""),
            })
            for field, pv in prov.items():
                if isinstance(pv, Mapping):
                    field_kinds[field][str(pv.get("kind") or "unknown")] += 1
            scenarios[scenario] += 1
            complete_count += int(complete)
            eligible_count += int(eligible)
            output.append(row)
        complete_eps += int(complete_count >= args.min_positive_per_episode)
        eligible_eps += int(eligible_count >= args.min_positive_per_episode)
        if eligible_count < args.min_positive_per_episode:
            insufficient_eps.append({"episode_id": eid, "hybrid_eligible_pudos": eligible_count, "pudos": len(prepared)})

    out = Path(args.output_pudo_jsonl); out.parent.mkdir(parents=True, exist_ok=True)
    write_jsonl(out, output)
    report = {
        "status": "PASS" if not insufficient_eps else "PARTIAL",
        "version": VERSION,
        "city": args.city,
        "split": args.split,
        "input_rows": len(base_rows),
        "output_rows": len(output),
        "episodes": len(by_episode),
        "episodes_with_min_complete_pudos": complete_eps,
        "episodes_with_min_hybrid_eligible_pudos": eligible_eps,
        "min_positive_per_episode": args.min_positive_per_episode,
        "simulated_overlay_rows": simulated_rows,
        "scenario_class_counts": dict(scenarios),
        "physical_site_prior_class_counts": dict(site_prior_classes),
        "physical_site_key_count": len(physical_site_keys),
        "curb_side_counts": dict(curb_sides),
        "side_semantics": SIDE_SEMANTICS,
        "static_transfer_fields": list(STATIC_TRANSFER_FIELDS),
        "numeric_field_ranges": {k: {"min": v[0], "max": v[1]} for k, v in numeric_minmax.items()},
        "field_provenance_kind_counts": {k: dict(v) for k, v in sorted(field_kinds.items())},
        "core_provenance_backfill_counts": dict(core_provenance_backfills),
        "same_site_static_evidence_counts": dict(site_static_counts),
        "cross_split_site_evidence_peer_rows_loaded": peer_rows_loaded,
        "same_site_static_evidence_conflict_count": len(site_static_conflicts),
        "same_site_static_evidence_conflict_examples": site_static_conflicts[:50],
        "insufficient_episode_count": len(insufficient_eps),
        "insufficient_episode_examples": insufficient_eps[:50],
        "standard_profile": profile,
        "paper_claim_allowed_for_simulated_rows": False,
        "interpretation": "Benchmark-ready hybrid truth may contain simulated typed-resource values. Observed/derived immutable physical site facts are reused across repeated nuPlan snapshots when consistent; remaining static simulated curb/interface facts are physical-site correlated, while dynamic blockage remains episode/time-specific. PUDO side is route-relative to the current episode approach and is therefore not canonicalized across physical-site snapshots. Conflicting immutable site evidence is reported rather than overwritten. Simulated fields are scenario truth only and must not be reported as measured city ground truth.",
        "output_pudo_jsonl": str(out),
    }
    rp = Path(args.report_json); rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    print("HYBRID_PUDO_EVIDENCE_CHECK=" + ("PASS" if report["status"] == "PASS" else "PARTIAL"))


if __name__ == "__main__":
    main()
