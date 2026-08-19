#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capplan.data.capability_contracts import load_profiles
from capplan.data.passenger_service_layer import load_fleet_interfaces, validate_service_request
from capplan.data.schemas import AccessibilityNode, node_from_dict
from capplan.utils.serialization import dump_json, read_jsonl, write_jsonl


def _read_records(path: str | None, key_hint: str | None = None) -> List[Dict[str, Any]]:
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    if p.suffix.lower() in {".yaml", ".yml"}:
        payload = yaml.safe_load(p.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            if key_hint and isinstance(payload.get(key_hint), list):
                return [dict(x) for x in payload[key_hint]]
            for key in ["service_requests", "requests", "records", "profiles"]:
                if isinstance(payload.get(key), list):
                    return [dict(x) for x in payload[key]]
            return [payload]
        return [dict(x) for x in payload or []]
    if p.suffix.lower() == ".json":
        payload = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            if key_hint and isinstance(payload.get(key_hint), list):
                return [dict(x) for x in payload[key_hint]]
            for key in ["service_requests", "requests", "records", "profiles"]:
                if isinstance(payload.get(key), list):
                    return [dict(x) for x in payload[key]]
            return [payload]
        return [dict(x) for x in payload]
    return read_jsonl(p)


def _demand_config(path: str | None) -> Dict[str, Any]:
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    if p.suffix.lower() in {".yaml", ".yml"}:
        return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return json.loads(p.read_text(encoding="utf-8"))


def _graph_episode_ids(graph_dir: Path) -> List[str]:
    return sorted({p.name.split(".nodes.jsonl")[0] for p in graph_dir.glob("*.nodes.jsonl") if p.name != "nodes.jsonl"}) or ["shared"]


def _load_episode_allowlist(path: str | None) -> set[str] | None:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    if p.suffix.lower() == ".json":
        payload = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(payload, list):
            return {str(x) for x in payload}
        if isinstance(payload, dict):
            for key in ["episode_ids", "allowed_episode_ids", "paper_episode_ids"]:
                if isinstance(payload.get(key), list):
                    return {str(x) for x in payload[key]}
        raise RuntimeError(f"episode allowlist JSON has no supported episode-id list: {p}")
    return {line.strip() for line in p.read_text(encoding="utf-8").splitlines() if line.strip() and not line.lstrip().startswith("#")}


def _load_nodes(graph_dir: Path, eid: str) -> List[AccessibilityNode]:
    f = graph_dir / f"{eid}.nodes.jsonl"
    if not f.exists():
        f = graph_dir / "nodes.jsonl"
    if not f.exists():
        raise FileNotFoundError(f"missing nodes JSONL for {eid} in {graph_dir}")
    return [node_from_dict(x) for x in read_jsonl(f)]


def _entrance_nodes(
    nodes: Sequence[AccessibilityNode],
    allow_non_entrance_od: bool = False,
    trusted_source_tokens: Sequence[str] | None = None,
) -> List[AccessibilityNode]:
    # Bootstrap diagnostics may use a mapped transit stop as an OD-like anchor,
    # but paper mode is explicitly entrance-to-entrance.  When trusted-source
    # filtering is enabled (the paper path), do not silently promote a
    # transit_stop into origin/destination entrance truth.
    allowed_kinds = {"entrance", "origin_entrance", "destination_entrance"}
    if trusted_source_tokens is None:
        allowed_kinds.add("transit_stop")
    entrances = [n for n in nodes if n.kind in allowed_kinds]
    if trusted_source_tokens is not None:
        tokens = [str(x).lower() for x in trusted_source_tokens]
        entrances = [n for n in entrances if any(t in str(n.source or "").lower() for t in tokens)]
    if len(entrances) >= 2:
        return entrances
    if allow_non_entrance_od:
        fallback = [n for n in nodes if n.kind in {"sidewalk", "crossing", "curb", "curb_ramp", "pudo"}]
        if len(fallback) >= 2:
            return fallback
    qualifier = "trusted audited " if trusted_source_tokens is not None else "real "
    raise RuntimeError(f"service layer generation requires at least two {qualifier}entrance nodes per episode; use --allow_non_entrance_od only for bootstrap diagnostics, not paper experiments")


def _three_layer_profiles(source: str = "calibrated_three_layer_profiles") -> List[Dict[str, Any]]:
    """Functional planning profiles, not demographic labels."""
    return [
        {
            "profile_id": "basic_service_complete",
            "source": source,
            "archetype": "basic_service_complete",
            "consent_scope": "trip_planning",
            "capability_version": "abilitybench_av_v1",
            "mobility": {
                "device_type": "none",
                "max_access_distance_m": 500.0,
                "max_egress_distance_m": 500.0,
                "max_slope": 0.08,
                "max_cross_slope": 0.04,
                "min_clear_width_m": 0.815,
                "step_free_required": False,
                "curb_ramp_required": False,
                "allowed_surfaces": ["concrete", "asphalt", "paved", "compacted_gravel"],
            },
            "wait": {"max_wait_exposure_s": 900.0, "shelter_required": False, "min_lighting": "day_or_lit", "identification_modalities_any_of": ["visual", "audio", "app", "haptic"]},
            "interface": {"preferred_door_side": "either", "min_door_width_m": 0.72, "min_deployment_clearance_m": 0.7, "boarding_any_of": [], "max_dwell_time_s": None},
            "ride": {"max_ride_time_s": 5400.0, "max_peak_accel_mps2": 2.6, "max_peak_jerk_mps3": 4.5, "max_motion_exposure": 5.0},
            "uncertainty": {"min_map_confidence": 0.60, "max_blockage_risk": 0.45, "max_deployment_risk": 0.30, "beta_tau": 1.0, "missing_policy": "fail_closed"},
        },
        {
            "profile_id": "mobility_interface_constrained",
            "source": source,
            "archetype": "mobility_interface_constrained",
            "consent_scope": "trip_planning",
            "capability_version": "abilitybench_av_v1",
            "mobility": {
                "device_type": "wheeled_mobility_device",
                "max_access_distance_m": 240.0,
                "max_egress_distance_m": 240.0,
                "max_slope": 0.05,
                "max_cross_slope": 0.0208,
                "min_clear_width_m": 0.915,
                "step_free_required": True,
                "curb_ramp_required": True,
                "allowed_surfaces": ["concrete", "asphalt", "paved"],
            },
            "wait": {"max_wait_exposure_s": 600.0, "shelter_required": False, "min_lighting": "day_or_lit", "identification_modalities_any_of": ["visual", "audio", "app", "haptic"]},
            "interface": {
                "preferred_door_side": "right",
                "min_door_width_m": 0.82,
                "min_deployment_clearance_m": 1.2,
                "boarding_any_of": [{"ramp": True}, {"lift": True}, {"low_floor": True, "kneeling": True, "curb_height_m_max": 0.06}],
                "max_dwell_time_s": 180.0,
            },
            "ride": {"max_ride_time_s": 3600.0, "max_peak_accel_mps2": 2.0, "max_peak_jerk_mps3": 3.0, "max_motion_exposure": 3.0},
            "uncertainty": {"min_map_confidence": 0.70, "max_blockage_risk": 0.35, "max_deployment_risk": 0.20, "beta_tau": 1.2, "missing_policy": "fail_closed"},
        },
        {
            "profile_id": "compound_uncertainty_sensitive",
            "source": source,
            "archetype": "compound_uncertainty_sensitive",
            "consent_scope": "trip_planning",
            "capability_version": "abilitybench_av_v1",
            "trip_modifiers": {"night_trip": True, "luggage": True, "rain_or_snow": False, "temporary_assistance_required": False},
            "mobility": {
                "device_type": "wheeled_mobility_device_plus_low_vision",
                "max_access_distance_m": 180.0,
                "max_egress_distance_m": 180.0,
                "max_slope": 0.04,
                "max_cross_slope": 0.015,
                "min_clear_width_m": 1.10,
                "step_free_required": True,
                "curb_ramp_required": True,
                "allowed_surfaces": ["concrete", "asphalt", "paved"],
            },
            "wait": {"max_wait_exposure_s": 420.0, "shelter_required": True, "min_lighting": "lit", "identification_modalities_any_of": ["audio", "haptic", "app"]},
            "interface": {
                "preferred_door_side": "right",
                "min_door_width_m": 0.90,
                "min_deployment_clearance_m": 1.4,
                "boarding_any_of": [{"ramp": True}, {"lift": True}],
                "max_dwell_time_s": 210.0,
            },
            "ride": {"max_ride_time_s": 3000.0, "max_peak_accel_mps2": 1.5, "max_peak_jerk_mps3": 2.2, "max_motion_exposure": 2.0},
            "uncertainty": {"min_map_confidence": 0.80, "max_blockage_risk": 0.25, "max_deployment_risk": 0.15, "beta_tau": 1.5, "missing_policy": "fail_closed"},
        },
    ]


def _choose_od(entrances: Sequence[AccessibilityNode], rng: random.Random) -> Tuple[AccessibilityNode, AccessibilityNode]:
    if len(entrances) < 2:
        raise RuntimeError("at least two entrances are required")
    origin = rng.choice(list(entrances))
    candidates = [e for e in entrances if e.node_id != origin.node_id]
    destination = max(candidates, key=lambda e: (e.x - origin.x) ** 2 + (e.y - origin.y) ** 2) if rng.random() < 0.5 else rng.choice(candidates)
    return origin, destination


def _generate_requests(args: argparse.Namespace, profiles: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not args.accessibility_graph_dir:
        raise RuntimeError("calibrated OD service generation requires --accessibility_graph_dir")
    cfg = _demand_config(args.demand_sources_config)
    graph_dir = Path(args.accessibility_graph_dir)
    episode_ids = [str(x) for x in (cfg.get("episode_ids") or _graph_episode_ids(graph_dir))]
    allowlist = _load_episode_allowlist(args.episode_allowlist)
    if allowlist is not None:
        episode_ids = [eid for eid in episode_ids if eid in allowlist]
        if not episode_ids:
            raise RuntimeError(f"episode allowlist {args.episode_allowlist} has no episodes present in {graph_dir}")
    profile_ids = [str(p["profile_id"]) for p in profiles]
    profile_mix = cfg.get("profile_mix") or profile_ids
    profile_mix = [str(x) for x in profile_mix]
    if len(set(profile_mix)) != len(profile_mix):
        raise RuntimeError("profile_mix must contain unique profile ids; duplicate profiles would create duplicate passenger contracts within an episode")
    if args.num_requests_per_episode > len(profile_mix):
        raise RuntimeError(
            f"num_requests_per_episode={args.num_requests_per_episode} exceeds the {len(profile_mix)} unique profiles in profile_mix. "
            "Provide additional counterfactual capability profiles instead of repeating a profile id."
        )
    profile_by_id = {str(p["profile_id"]): p for p in profiles}
    purposes = cfg.get("trip_purposes") or ["medical", "work", "shopping", "social", "other"]
    request_time_start = float(cfg.get("request_time_start_s", 8 * 3600))
    request_time_span = float(cfg.get("request_time_span_s", 12 * 3600))
    rng = random.Random(args.seed)
    rows: List[Dict[str, Any]] = []
    trusted_tokens = None
    if args.require_trusted_entrances:
        trusted_tokens = ["reviewed_audit:", "manual_audit:"] + list(args.trusted_entrance_source or [])
    for eid in episode_ids:
        nodes = _load_nodes(graph_dir, str(eid))
        entrances = _entrance_nodes(nodes, allow_non_entrance_od=args.allow_non_entrance_od, trusted_source_tokens=trusted_tokens)
        # AbilityBench counterfactuals intentionally keep the traffic scene and
        # passenger OD fixed while changing only the capability contract. The
        # previous code sampled a fresh OD for every profile, which made T4
        # impossible to interpret and conflicted with build_dataset.py (which
        # binds one OD per scene episode).
        o, d = _choose_od(entrances, rng)
        request_time_s = round(request_time_start + rng.random() * request_time_span, 3)
        cf_group_id = f"{eid}:cf_od0"
        base_profile_id = str(cfg.get("counterfactual_base_profile_id") or profile_mix[0])
        for i in range(args.num_requests_per_episode):
            pid = str(profile_mix[i])
            if pid not in profile_ids:
                raise RuntimeError(f"demand config references missing profile id {pid}")
            pmeta = profile_by_id[pid]
            is_base = pid == base_profile_id
            relation = str(pmeta.get("counterfactual_relation") or "stricter_or_equal")
            if relation not in {"stricter_or_equal", "different_modality", "different_interface"}:
                raise RuntimeError(f"profile {pid} has unsupported counterfactual_relation={relation!r}")
            rows.append({
                "request_id": f"{eid}:req_{i:04d}",
                "episode_id": str(eid),
                "origin_entrance_id": o.node_id,
                "destination_entrance_id": d.node_id,
                "origin_confidence": o.confidence,
                "destination_confidence": d.confidence,
                "request_time_s": request_time_s,
                "passenger_profile_id": pid,
                "trip_purpose": purposes[i % len(purposes)],
                "party_size": 1,
                "demand_weight": float(cfg.get("default_demand_weight", 1.0)),
                "modifiers": dict(cfg.get("modifiers", {})),
                "source": args.source_name,
                "counterfactual_group_id": cf_group_id,
                "counterfactual_role": "base" if is_base else "variant",
                "counterfactual_base_profile_id": base_profile_id,
                "counterfactual_axis": pmeta.get("counterfactual_axis") or ("base" if is_base else pmeta.get("archetype")),
                "counterfactual_relation": relation,
                "expected_monotonic": bool(pmeta.get("expected_monotonic", not is_base and relation == "stricter_or_equal")),
                "bootstrap_non_entrance_od": bool(args.allow_non_entrance_od and o.kind not in {"entrance", "origin_entrance", "destination_entrance", "transit_stop"}),
            })
    return rows


def _validate_refs(rows: List[Dict[str, Any]], profiles: List[Dict[str, Any]], fleet_jsonl: str | None) -> tuple[int, int]:
    profile_ids = {str(p.get("profile_id") or p.get("passenger_id")) for p in profiles}
    missing_profiles = sorted({str(r.get("passenger_profile_id")) for r in rows} - profile_ids)
    if missing_profiles:
        raise RuntimeError(f"service requests reference missing capability profile ids: {missing_profiles}")
    if fleet_jsonl:
        fleet = load_fleet_interfaces(fleet_jsonl)
        vehicle_ids = {v.vehicle_id for vs in fleet.values() for v in vs}
        missing_vehicles = sorted({str(r.get("vehicle_id") or r.get("fleet_vehicle_id")) for r in rows if r.get("vehicle_id") or r.get("fleet_vehicle_id")} - vehicle_ids)
        if missing_vehicles:
            raise RuntimeError(f"service requests reference missing fleet vehicle ids: {missing_vehicles}")
        return len(profiles), len(fleet)
    return len(profiles), 0


def build(args: argparse.Namespace) -> Dict[str, Any]:
    generated_profiles: List[Dict[str, Any]] = []
    if args.capability_profiles_jsonl:
        profiles = load_profiles(args.capability_profiles_jsonl)
    else:
        profiles = _three_layer_profiles(args.source_name)
        generated_profiles = list(profiles)
        if not args.output_capability_profiles_jsonl:
            # Sidecar default keeps old CLI compatible but makes generated passenger info reusable.
            args.output_capability_profiles_jsonl = str(Path(args.output_service_requests_jsonl).with_name("capability_profiles.generated.jsonl"))
    if args.output_capability_profiles_jsonl:
        # Materialize the exact profile set used by this build even when the
        # source was YAML/JSONL supplied by the caller. This keeps downstream
        # dataset manifests self-contained and reproducible.
        write_jsonl(args.output_capability_profiles_jsonl, profiles)

    allowlist = _load_episode_allowlist(args.episode_allowlist)
    input_rows_before_allowlist = None
    if args.service_requests_jsonl:
        materialized = _read_records(args.service_requests_jsonl, "service_requests")
        input_rows_before_allowlist = len(materialized)
        if allowlist is not None:
            materialized = [r for r in materialized if str(r.get("episode_id")) in allowlist]
            if not materialized:
                raise RuntimeError(f"episode allowlist {args.episode_allowlist} removed every materialized service request")
        rows = [validate_service_request(r) for r in materialized]
        mode = "real_jsonl_validator"
    else:
        cfg = _demand_config(args.demand_sources_config)
        if isinstance(cfg.get("service_requests"), list):
            rows = [validate_service_request(r) for r in cfg["service_requests"]]
            mode = "materialized_calibrated_requests"
        else:
            rows = [validate_service_request(r) for r in _generate_requests(args, profiles)]
            mode = "calibrated_od_sampler"
    profiles_checked, fleet_eps = _validate_refs(rows, profiles, args.fleet_jsonl)
    write_jsonl(args.output_service_requests_jsonl, rows)
    report = {
        "service_requests": len(rows),
        "profiles_checked": profiles_checked,
        "fleet_episodes_checked": fleet_eps,
        "source": args.source_name,
        "mode": mode,
        "materialized_capability_profiles_jsonl": args.output_capability_profiles_jsonl,
        "episode_allowlist": args.episode_allowlist,
        "allowed_episode_count": len(allowlist) if allowlist is not None else None,
        "input_rows_before_allowlist": input_rows_before_allowlist,
        "unique_output_episodes": len({str(r.get("episode_id")) for r in rows}),
        "require_trusted_entrances": bool(args.require_trusted_entrances),
        "trusted_entrance_source_tokens": (["reviewed_audit:", "manual_audit:"] + list(args.trusted_entrance_source or [])) if args.require_trusted_entrances else [],
    }
    if args.report_json:
        dump_json(args.report_json, report)
    return report


def main() -> None:
    p = argparse.ArgumentParser(description="Build calibrated passenger service requests and three-layer capability profiles for AbilityBench-AV.")
    p.add_argument("--scene_dataset_dir", default=None)
    p.add_argument("--accessibility_graph_dir", default=None)
    p.add_argument("--demand_sources_config", default=None, help="YAML/JSON with optional service_requests or OD sampling settings/profile mix.")
    p.add_argument("--service_requests_jsonl", default=None, help="Materialized real/calibrated service requests; if omitted, requests are sampled from graph entrances.")
    p.add_argument("--capability_profiles_jsonl", default=None, help="Existing capability profiles. If omitted, the three AbilityBench layers are generated.")
    p.add_argument("--output_capability_profiles_jsonl", default=None, help="Where to write generated three-layer profiles when --capability_profiles_jsonl is omitted.")
    p.add_argument("--fleet_jsonl", default=None)
    p.add_argument("--output_service_requests_jsonl", required=True)
    p.add_argument("--num_requests_per_episode", type=int, default=3)
    p.add_argument("--seed", type=int, default=13)
    p.add_argument("--source_name", default="calibrated_service_layer")
    p.add_argument("--allow_non_entrance_od", action="store_true", help="Bootstrap-only: sample OD from sidewalk/curb nodes if entrance nodes are absent. Not valid for paper-mode datasets.")
    p.add_argument("--episode_allowlist", default=None, help="Optional text/JSON episode-id allowlist. Paper builds should use an allowlist selected from audited PUDO + entrance evidence instead of forcing every nuPlan scenario into the main-result set.")
    p.add_argument("--require_trusted_entrances", action="store_true", help="Restrict O/D sampling to independently audited/trusted entrance nodes. Manual/reviewed-audit sources are trusted by default.")
    p.add_argument("--trusted_entrance_source", action="append", default=[], help="Additional trusted entrance source substring; repeat as needed.")
    p.add_argument("--report_json", default=None)
    args = p.parse_args()
    print(json.dumps(build(args), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
