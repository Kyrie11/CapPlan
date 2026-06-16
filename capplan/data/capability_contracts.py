"""Functional passenger capability profiles and executable contracts."""
from __future__ import annotations

import copy
import json
import random
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

import yaml

from capplan.data.schemas import CapabilityClause, CapabilityContract, CounterfactualPair, RequirementGroup, contract_from_dict
from capplan.utils.serialization import read_jsonl

CONFIG_PATH = Path(__file__).resolve().parents[2] / "configs" / "capability_profiles.yaml"
ARCHETYPES = [
    "standard_ambulatory",
    "manual_wheelchair",
    "power_wheelchair",
    "walker_or_cane",
    "low_vision",
    "hearing_limited",
    "motion_sensitive",
    "temporary_luggage_or_companion",
]


def load_profile_config(path: str | Path | None = None) -> Dict[str, Any]:
    p = Path(path) if path else CONFIG_PATH
    with p.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _sample(v: Any, rng: random.Random) -> Any:
    if isinstance(v, list) and len(v) == 2 and all(isinstance(x, (int, float)) for x in v):
        return round(rng.uniform(float(v[0]), float(v[1])), 4)
    return copy.deepcopy(v)


def make_profile(profile_id: str, archetype: str = "manual_wheelchair", seed: int = 0, trip_modifiers: Dict[str, Any] | None = None) -> Dict[str, Any]:
    cfg = load_profile_config()
    if archetype not in cfg["archetypes"]:
        raise KeyError(f"unknown capability archetype {archetype}")
    rng = random.Random(seed)
    template = copy.deepcopy(cfg["archetypes"][archetype])
    profile: Dict[str, Any] = {
        "profile_id": profile_id,
        "consent_scope": "trip_planning",
        "capability_version": cfg.get("version", "v1"),
        "archetype": archetype,
        "trip_modifiers": {
            "luggage": False,
            "companion": False,
            "night_trip": False,
            "rain_or_snow": False,
            "temporary_assistance_required": False,
        },
    }
    for section, values in template.items():
        profile[section] = {k: _sample(v, rng) for k, v in values.items()}
    if trip_modifiers:
        profile["trip_modifiers"].update(trip_modifiers)
    _apply_trip_modifier_defaults(profile)
    return profile


def _apply_trip_modifier_defaults(profile: Dict[str, Any]) -> None:
    mods = profile.get("trip_modifiers", {})
    mob = profile["mobility"]
    interface = profile["interface"]
    wait = profile["wait"]
    unc = profile["uncertainty"]
    if mods.get("luggage") or mods.get("companion"):
        mob["max_access_distance_m"] = min(float(mob["max_access_distance_m"]), 0.9 * float(mob["max_access_distance_m"]))
        mob["max_egress_distance_m"] = min(float(mob["max_egress_distance_m"]), 0.9 * float(mob["max_egress_distance_m"]))
        interface["min_deployment_clearance_m"] = max(float(interface.get("min_deployment_clearance_m") or 0.8), 1.1)
        interface["min_door_width_m"] = max(float(interface.get("min_door_width_m") or 0.75), 0.82)
    if mods.get("night_trip"):
        wait["min_lighting"] = "lit"
        unc["min_map_confidence"] = max(float(unc.get("min_map_confidence", 0.7)), 0.72)
    if mods.get("rain_or_snow"):
        mob["max_slope"] = min(float(mob["max_slope"]), 0.06)
        unc["max_blockage_risk"] = min(float(unc.get("max_blockage_risk", 0.35)), 0.30)
        unc["beta_tau"] = max(float(unc.get("beta_tau", 1.0)), 1.4)
    if mods.get("temporary_assistance_required"):
        profile["assistance_required"] = True


def _clause(profile_id: str, idx: int, resource: str, phases: List[str], op: str, threshold: Any, kind: str, source: str, beta: float, hard: bool = True, risk_tolerance: float | None = None, missing_policy: str = "fail_closed") -> CapabilityClause:
    return CapabilityClause(resource, phases, op, threshold, kind, confidence=1.0, risk_tolerance=risk_tolerance, source=source, clause_id=f"{profile_id}:c{idx:02d}:{resource}", hard=hard, beta_tau=beta, missing_policy=missing_policy)


def profile_to_contract(profile: Dict[str, Any]) -> CapabilityContract:
    """Compile a high-level functional profile into clauses and groups."""
    pid = profile["profile_id"]
    beta = float(profile.get("uncertainty", {}).get("beta_tau", 1.0))
    missing_policy = profile.get("uncertainty", {}).get("missing_policy", "fail_closed")
    clauses: List[CapabilityClause] = []
    groups: List[RequirementGroup] = []

    def add(resource: str, phases: List[str], op: str, threshold: Any, kind: str, source: str, hard: bool = True, risk_tolerance: float | None = None) -> CapabilityClause:
        c = _clause(pid, len(clauses), resource, phases, op, threshold, kind, source, beta, hard, risk_tolerance, missing_policy)
        clauses.append(c)
        return c

    mob = profile["mobility"]
    wait = profile["wait"]
    interface = profile["interface"]
    ride = profile["ride"]
    unc = profile["uncertainty"]

    add("access_distance_m", ["access"], "<=", float(mob["max_access_distance_m"]), "cumulative", "onboarding")
    add("egress_distance_m", ["egress"], "<=", float(mob["max_egress_distance_m"]), "cumulative", "onboarding")
    add("slope", ["access", "egress"], "<=", float(mob["max_slope"]), "upper", "accessibility_map")
    add("cross_slope", ["access", "egress"], "<=", float(mob["max_cross_slope"]), "upper", "accessibility_map")
    add("path_width_m", ["access", "egress"], ">=", float(mob["min_clear_width_m"]), "lower", "accessibility_map")
    if mob.get("step_free_required", False):
        add("step_free", ["access", "board", "alight", "egress"], "requires", True, "categorical", "onboarding")
    if mob.get("curb_ramp_required", False):
        add("curb_ramp", ["access", "egress"], "requires", True, "categorical", "accessibility_map")
    if mob.get("allowed_surfaces"):
        add("surface", ["access", "egress"], "in", list(mob["allowed_surfaces"]), "categorical", "accessibility_map")

    add("wait_exposure_s", ["wait"], "<=", float(wait["max_wait_exposure_s"]), "cumulative", "service_trace")
    if wait.get("shelter_required", False):
        add("shelter", ["wait"], "requires", True, "categorical", "curbside_map")
    if wait.get("min_lighting"):
        add("lighting", ["access", "wait", "egress"], "meets_lighting", wait["min_lighting"], "categorical", "map/perception")
    add("identification_modality", ["wait", "board"], "in", list(wait.get("identification_modalities_any_of", ["visual"])), "categorical", "vehicle_spec+app")

    # Door side is a required side/policy, never a boolean.
    add("door_side", ["board", "alight"], "compatible_side", interface.get("preferred_door_side", "either"), "categorical", "vehicle_spec+curbside_map")
    add("door_width_m", ["board", "alight"], ">=", float(interface.get("min_door_width_m") or 0.72), "lower", "vehicle_spec")
    add("deployment_clearance_m", ["board", "alight"], ">=", float(interface.get("min_deployment_clearance_m") or 0.7), "lower", "vehicle_spec+curbside_map")
    if interface.get("max_dwell_time_s") is not None:
        add("dwell_time_s", ["board", "alight"], "<=", float(interface["max_dwell_time_s"]), "cumulative", "vehicle_spec")

    boarding_options = interface.get("boarding_any_of") or []
    option_clause_ids: List[str] = []
    for opt in boarding_options:
        if opt.get("ramp") is True:
            option_clause_ids.append(add("ramp", ["board", "alight"], "requires", True, "categorical", "vehicle_spec").id)
        elif opt.get("lift") is True:
            option_clause_ids.append(add("lift", ["board", "alight"], "requires", True, "categorical", "vehicle_spec").id)
        elif opt.get("low_floor") and opt.get("kneeling"):
            # Composite option is emitted as an auditable categorical predicate.
            option_clause_ids.append(add("low_floor_kneeling", ["board", "alight"], "requires", True, "categorical", "vehicle_spec+curbside_map").id)
    if option_clause_ids:
        groups.append(RequirementGroup(f"{pid}:g_boarding_any_of", ["board", "alight"], "any_of", option_clause_ids, hard=True))

    add("ride_time_s", ["ride"], "<=", float(ride.get("max_ride_time_s", 3600)), "cumulative", "trajectory")
    add("peak_accel_mps2", ["ride"], "<=", float(ride["max_peak_accel_mps2"]), "upper", "trajectory")
    add("peak_jerk_mps3", ["ride"], "<=", float(ride["max_peak_jerk_mps3"]), "upper", "trajectory")
    add("motion_exposure", ["ride"], "<=", float(ride["max_motion_exposure"]), "cumulative", "trajectory")

    add("map_confidence", ["access", "wait", "board", "alight", "egress"], ">=", float(unc["min_map_confidence"]), "lower", "map/perception")
    add("blockage_risk", ["access", "wait", "board", "alight", "egress"], "<=", float(unc["max_blockage_risk"]), "probabilistic", "prediction", risk_tolerance=float(unc["max_blockage_risk"]))
    add("deployment_risk", ["board", "alight"], "<=", float(unc.get("max_deployment_risk", 0.25)), "probabilistic", "fleet_audit", risk_tolerance=float(unc.get("max_deployment_risk", 0.25)))

    if profile.get("trip_modifiers", {}).get("temporary_assistance_required") or profile.get("assistance_required"):
        add("assistance", ["wait", "board", "alight"], "requires", True, "categorical", "service_policy")

    return CapabilityContract(pid, clauses, metadata={"profile": profile.get("archetype"), "consent_scope": profile.get("consent_scope", "trip_planning"), "capability_version": profile.get("capability_version", "v1"), "trip_modifiers": profile.get("trip_modifiers", {})}, groups=groups, profile=profile)


def default_contract(passenger_id: str = "p0") -> CapabilityContract:
    return profile_to_contract(make_profile(passenger_id, "manual_wheelchair", seed=0))


def standard_contract(passenger_id: str = "p0") -> CapabilityContract:
    return profile_to_contract(make_profile(passenger_id, "standard_ambulatory", seed=0))


def _make_stricter(contract: CapabilityContract, passenger_id: str, factor: float = 0.75) -> CapabilityContract:
    clauses: List[CapabilityClause] = []
    for c in contract.clauses:
        th = copy.deepcopy(c.threshold)
        if c.kind in ("cumulative", "upper", "probabilistic") and isinstance(th, (int, float)):
            # Confidence lower bounds are different kind/order and handled below.
            if c.resource_name not in ("map_confidence",):
                th = max(0.0, float(th) * factor)
        if c.kind == "lower" and isinstance(th, (int, float)):
            th = min(2.5, float(th) / max(factor, 1e-6))
        if c.resource_name == "map_confidence" and isinstance(th, (int, float)):
            th = min(0.95, float(th) + 0.08)
        clauses.append(CapabilityClause(c.resource_name, list(c.phase_scope), c.operator, th, c.kind, c.confidence, c.risk_tolerance if c.resource_name not in ("blockage_risk", "deployment_risk") else (float(th) if isinstance(th, (int, float)) else c.risk_tolerance), c.source, c.consent_scope, c.clause_id.replace(contract.passenger_id, passenger_id) if c.clause_id else None, c.hard, c.beta_tau, c.missing_policy, copy.deepcopy(c.metadata)))
    groups = [RequirementGroup(g.group_id.replace(contract.passenger_id, passenger_id), list(g.phase_scope), g.logic, [cid.replace(contract.passenger_id, passenger_id) for cid in g.clause_ids], g.hard) for g in contract.groups]
    prof = copy.deepcopy(contract.profile)
    prof["profile_id"] = passenger_id
    prof["archetype"] = f"stricter_{prof.get('archetype', 'profile')}"
    return CapabilityContract(passenger_id, clauses, metadata={**contract.metadata, "profile": "counterfactual_stricter", "base_passenger": contract.passenger_id}, groups=groups, profile=prof)


def sample_contracts(episode_id: str, num_contracts: int = 2, seed: int = 0) -> List[CapabilityContract]:
    """Generate functional archetype contracts and at least one ordered pair."""
    rng = random.Random(seed)
    if num_contracts <= 0:
        return []
    contracts: List[CapabilityContract] = []
    base_profile = make_profile(f"{episode_id}:p0", "standard_ambulatory", seed=seed)
    base = profile_to_contract(base_profile)
    contracts.append(base)
    if num_contracts >= 2:
        strict_profile = make_profile(f"{episode_id}:p1", "manual_wheelchair", seed=seed + 1)
        # Make the first pair ordered for shared numeric resources by tightening the base contract.
        strict = _make_stricter(base, f"{episode_id}:p1", factor=0.72)
        # Add wheelchair interface group to make it also meaningfully stricter.
        wheelchair = profile_to_contract(strict_profile)
        existing = {c.resource_name for c in strict.clauses}
        merged = list(strict.clauses)
        for c in wheelchair.clauses:
            if c.resource_name in {"step_free", "curb_ramp", "ramp", "lift", "low_floor_kneeling", "deployment_clearance_m", "door_width_m"} and c.resource_name not in existing:
                merged.append(CapabilityClause(c.resource_name, c.phase_scope, c.operator, c.threshold, c.kind, c.confidence, c.risk_tolerance, c.source, c.consent_scope, c.clause_id.replace(wheelchair.passenger_id, strict.passenger_id) if c.clause_id else None, c.hard, c.beta_tau, c.missing_policy, c.metadata))
        groups = list(strict.groups) + [RequirementGroup(g.group_id.replace(wheelchair.passenger_id, strict.passenger_id), g.phase_scope, g.logic, [cid.replace(wheelchair.passenger_id, strict.passenger_id) for cid in g.clause_ids], g.hard) for g in wheelchair.groups]
        contracts.append(CapabilityContract(strict.passenger_id, merged, strict.metadata, groups, strict.profile))
    archetypes = ARCHETYPES[2:]
    for i in range(2, num_contracts):
        arch = archetypes[(i - 2) % len(archetypes)]
        mods = {}
        if i % 3 == 0:
            mods["night_trip"] = True
        if i % 4 == 0:
            mods["luggage"] = True
        contracts.append(profile_to_contract(make_profile(f"{episode_id}:p{i}", arch, seed=rng.randint(0, 10_000), trip_modifiers=mods)))
    return contracts[:num_contracts]


def sample_contracts_with_pairs(episode_id: str, num_contracts: int = 2, seed: int = 0) -> Tuple[List[CapabilityContract], List[CounterfactualPair]]:
    contracts = sample_contracts(episode_id, num_contracts, seed)
    pairs: List[CounterfactualPair] = []
    if len(contracts) >= 2:
        pairs.append(CounterfactualPair(f"{episode_id}:cf0", episode_id, contracts[0].passenger_id, contracts[1].passenger_id, "stricter_or_equal", True))
    return contracts, pairs



def _read_profile_records(path: str | Path) -> List[Dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    if p.suffix.lower() in {".yaml", ".yml"}:
        payload = yaml.safe_load(p.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            for key in ["profiles", "capability_profiles", "contracts"]:
                if isinstance(payload.get(key), list):
                    return [dict(x) for x in payload[key]]
            return [payload]
        if isinstance(payload, list):
            return [dict(x) for x in payload]
        return []
    return read_jsonl(p)


def _reject_proxy_profile_source(row: Dict[str, Any]) -> None:
    source = str(row.get("source") or row.get("metadata", {}).get("source") or row.get("profile_source") or "").lower()
    if source.startswith("synthetic") or "proxy" in source or source in {"mock", "toy"}:
        raise ValueError(f"paper-mode capability profile/contract rejects synthetic/proxy source: {row.get('profile_id') or row.get('passenger_id')} source={source}")


def load_profiles(path: str | Path) -> List[Dict[str, Any]]:
    """Load passenger capability profiles from JSONL/YAML without sampling.

    Records may be high-level profiles accepted by ``profile_to_contract`` or
    full ``CapabilityContract`` dictionaries.  The source field must be real or
    calibrated, not synthetic/proxy.
    """
    rows = _read_profile_records(path)
    for row in rows:
        _reject_proxy_profile_source(row)
    return rows


def _contract_from_profile_record(row: Dict[str, Any], passenger_id: str | None = None) -> CapabilityContract:
    if "clauses" in row and "passenger_id" in row:
        c = contract_from_dict(row)
        if passenger_id and c.passenger_id != passenger_id:
            c = CapabilityContract(passenger_id, c.clauses, {**c.metadata, "original_passenger_id": c.passenger_id}, c.groups, {**c.profile, "profile_id": passenger_id})
        return c
    profile = copy.deepcopy(row)
    if passenger_id:
        profile["profile_id"] = passenger_id
    if "profile_id" not in profile:
        raise ValueError(f"capability profile missing profile_id/passenger binding: {row}")
    # Normalize guide.md profile sections into the existing contract compiler schema.
    profile.setdefault("consent_scope", "trip_planning")
    profile.setdefault("capability_version", profile.get("version", "v1"))
    profile.setdefault("archetype", profile.get("source_profile", "real_profile"))
    profile.setdefault("trip_modifiers", profile.get("modifiers", {}))
    profile.setdefault("mobility", profile.get("mobility", {}))
    profile.setdefault("interface", profile.get("interface", {}))
    profile.setdefault("wait", profile.get("wait", profile.get("interface", {})))
    profile.setdefault("ride", profile.get("ride", {}))
    profile.setdefault("uncertainty", profile.get("uncertainty", {}))
    # Required defaults are conservative but explicit, so callers can omit fields
    # that are irrelevant to a profile while still producing executable clauses.
    profile["mobility"].setdefault("max_access_distance_m", profile.get("max_walk_or_roll_distance_m", 250.0))
    profile["mobility"].setdefault("max_egress_distance_m", profile.get("max_walk_or_roll_distance_m", 250.0))
    profile["mobility"].setdefault("max_slope", 0.05)
    profile["mobility"].setdefault("max_cross_slope", 0.02)
    profile["mobility"].setdefault("min_clear_width_m", 1.0)
    profile["mobility"].setdefault("step_free_required", False)
    profile["mobility"].setdefault("curb_ramp_required", False)
    profile["mobility"].setdefault("allowed_surfaces", ["concrete", "asphalt", "paved"])
    profile["wait"].setdefault("max_wait_exposure_s", 600.0)
    profile["wait"].setdefault("shelter_required", False)
    profile["wait"].setdefault("min_lighting", "day")
    profile["wait"].setdefault("identification_modalities_any_of", ["visual", "audio"])
    profile["interface"].setdefault("preferred_door_side", "either")
    profile["interface"].setdefault("min_door_width_m", 0.78)
    profile["interface"].setdefault("min_deployment_clearance_m", 0.8)
    profile["interface"].setdefault("boarding_any_of", [])
    profile["ride"].setdefault("max_ride_time_s", 3600.0)
    profile["ride"].setdefault("max_peak_accel_mps2", 2.5)
    profile["ride"].setdefault("max_peak_jerk_mps3", 4.0)
    profile["ride"].setdefault("max_motion_exposure", 500.0)
    profile["uncertainty"].setdefault("min_map_confidence", 0.70)
    profile["uncertainty"].setdefault("max_blockage_risk", 0.35)
    profile["uncertainty"].setdefault("max_deployment_risk", 0.25)
    profile["uncertainty"].setdefault("beta_tau", 1.0)
    profile["uncertainty"].setdefault("missing_policy", "fail_closed")
    return profile_to_contract(profile)


def load_contracts_from_profiles(path: str | Path, service_requests_by_episode: Dict[str, List[Dict[str, Any]]] | None = None) -> Dict[str, List[CapabilityContract]]:
    rows = load_profiles(path)
    by_profile_id = {str(r.get("profile_id") or r.get("passenger_id")): r for r in rows if r.get("profile_id") or r.get("passenger_id")}
    out: Dict[str, List[CapabilityContract]] = {}
    service_requests_by_episode = service_requests_by_episode or {}
    if service_requests_by_episode:
        for eid, requests in service_requests_by_episode.items():
            for req in requests:
                pid = str(req.get("passenger_profile_id"))
                if pid not in by_profile_id:
                    raise KeyError(f"service request {req.get('request_id')} references missing passenger_profile_id {pid}")
                passenger_id = f"{eid}:{pid}"
                c = _contract_from_profile_record(by_profile_id[pid], passenger_id=passenger_id)
                c.metadata.update({"episode_id": eid, "request_id": req.get("request_id"), "profile_source": by_profile_id[pid].get("source")})
                out.setdefault(eid, []).append(c)
        return out
    for row in rows:
        c = _contract_from_profile_record(row)
        eid = str(row.get("episode_id") or c.metadata.get("episode_id") or c.passenger_id.split(":p")[0])
        out.setdefault(eid, []).append(c)
    return out
