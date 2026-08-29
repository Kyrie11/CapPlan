#!/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import sys
from datetime import datetime
from zoneinfo import ZoneInfo
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

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


def _stable_seed(base: int, *parts: str) -> int:
    payload = "|".join([str(base), *map(str, parts)]).encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") & 0x7FFFFFFF


def _load_scene_metadata(scene_dataset_dir: str | None) -> Dict[str, Dict[str, Any]]:
    """Load compact per-episode nuPlan metadata from extracted ``episodes.jsonl``.

    ``scene_dataset_dir`` may be one city directory or the split-level
    ``scene_contexts`` directory containing city subdirectories.  We read
    ``episodes.jsonl`` rather than the much larger ``scenes.jsonl`` so service
    generation stays cheap even for the paper-scale corpus.
    """
    if not scene_dataset_dir:
        return {}
    root = Path(scene_dataset_dir)
    if not root.exists():
        raise FileNotFoundError(root)
    files = [root / "episodes.jsonl"] if (root / "episodes.jsonl").exists() else sorted(root.glob("**/episodes.jsonl"))
    out: Dict[str, Dict[str, Any]] = {}
    for f in files:
        for row in read_jsonl(f):
            eid = str(row.get("episode_id") or row.get("scenario_id") or "")
            if not eid:
                continue
            rc = row.get("route_corridor") or (row.get("metadata") or {}).get("route_corridor") or {}
            out[eid] = {
                "episode_id": eid,
                "map_name": row.get("map_name"),
                "request_time_s": row.get("request_time_s"),
                "route_corridor": rc,
                "source_file": str(f),
            }
    return out


def _route_polyline(scene_meta: Mapping[str, Any] | None) -> List[Tuple[float, float]]:
    if not scene_meta:
        return []
    rc = scene_meta.get("route_corridor") if isinstance(scene_meta.get("route_corridor"), Mapping) else {}
    out: List[Tuple[float, float]] = []
    for pt in (rc.get("polyline") or []):
        if isinstance(pt, (list, tuple)) and len(pt) >= 2:
            try:
                out.append((float(pt[0]), float(pt[1])))
            except Exception:
                pass
    return out


def _dist(a: AccessibilityNode, xy: Tuple[float, float]) -> float:
    return math.hypot(float(a.x) - float(xy[0]), float(a.y) - float(xy[1]))


def _entrance_nodes(
    nodes: Sequence[AccessibilityNode],
    trusted_source_tokens: Sequence[str] | None = None,
    *,
    require_minimum: bool = True,
) -> List[AccessibilityNode]:
    # A transit stop is a useful service anchor in hybrid/bootstrap mode, but
    # paper mode keeps the stricter entrance-only semantics through
    # trusted-source filtering.
    allowed_kinds = {"entrance", "origin_entrance", "destination_entrance"}
    if trusted_source_tokens is None:
        allowed_kinds.add("transit_stop")
    entrances = [n for n in nodes if n.kind in allowed_kinds]
    if trusted_source_tokens is not None:
        tokens = [str(x).lower() for x in trusted_source_tokens]
        entrances = [n for n in entrances if any(t in str(n.source or "").lower() for t in tokens)]
    if len(entrances) >= 2 or not require_minimum:
        return entrances
    qualifier = "trusted audited " if trusted_source_tokens is not None else "real "
    raise RuntimeError(f"service layer generation requires at least two {qualifier}entrance nodes per episode")


def _entrance_proxy_nodes(nodes: Sequence[AccessibilityNode]) -> List[AccessibilityNode]:
    """Building/address-linked candidate points usable only as hybrid anchors.

    ``entrance_proxy`` nodes are not promoted to observed entrance truth.  They
    merely help choose a nearby connected sidewalk node when the city data lacks
    a verified doorway, keeping the constructed request close to plausible
    building frontage rather than an arbitrary point along the route.
    """
    return [n for n in nodes if n.kind == "entrance_proxy"]


def _frontage_nodes(nodes: Sequence[AccessibilityNode]) -> List[AccessibilityNode]:
    """Return physically plausible *access points* for synthetic hybrid OD.

    We intentionally do not use curb/crossing/PUDO vertices as entrances.  A
    sidewalk vertex is interpreted as the public-realm access point immediately
    outside a constructed building entrance.  The building-door geometry itself
    is not claimed to have been observed.
    """
    good = []
    for n in nodes:
        if n.kind != "sidewalk":
            continue
        src = str(n.source or "").lower()
        # Hybrid source geometry must still be anchored in a real/curated map.
        if src.startswith("synthetic") or "mock" in src or "toy" in src:
            continue
        good.append(n)
    return good


def _nearest_distinct(candidates: Sequence[AccessibilityNode], xy: Tuple[float, float], avoid: str | None = None) -> AccessibilityNode | None:
    pool = [n for n in candidates if n.node_id != avoid]
    return min(pool, key=lambda n: (_dist(n, xy), n.node_id)) if pool else None


def _farthest_from(candidates: Sequence[AccessibilityNode], origin: AccessibilityNode) -> AccessibilityNode | None:
    pool = [n for n in candidates if n.node_id != origin.node_id]
    return max(pool, key=lambda n: ((n.x-origin.x)**2 + (n.y-origin.y)**2, n.node_id)) if pool else None


def _route_local_destination_candidate(
    candidates: Sequence[AccessibilityNode],
    route_xy: Tuple[float, float],
    origin: AccessibilityNode,
    *,
    max_route_distance_m: float,
    min_od_separation_m: float,
    avoid: str | None = None,
) -> AccessibilityNode | None:
    """Choose a non-degenerate destination without abandoning route anchoring.

    The old fallback used the globally farthest pedestrian/entrance node when
    the initially selected OD pair was shorter than ``min_od_separation_m``.
    On a full-city graph that can move the destination more than a kilometre
    away from the nuPlan route endpoint.  Passenger-complete requests should
    stay tied to the traffic episode, so only candidates inside the route-local
    service radius are considered here.
    """
    ranked = []
    for n in candidates:
        if n.node_id == origin.node_id or (avoid is not None and n.node_id == avoid):
            continue
        route_d = _dist(n, route_xy)
        if route_d > max_route_distance_m:
            continue
        separation = math.hypot(n.x-origin.x, n.y-origin.y)
        if separation + 1e-9 < min_od_separation_m:
            continue
        ranked.append((route_d, -separation, n.node_id, n))
    return min(ranked, key=lambda x: (x[0], x[1], x[2]))[3] if ranked else None


def _frontage_near_route_or_proxy(
    nodes: Sequence[AccessibilityNode],
    route_xy: Tuple[float, float],
    *,
    avoid: str | None = None,
    max_proxy_route_distance_m: float = 250.0,
    max_proxy_frontage_distance_m: float = 80.0,
) -> Tuple[AccessibilityNode | None, str, Dict[str, Any]]:
    frontage = _frontage_nodes(nodes)
    proxies = _entrance_proxy_nodes(nodes)
    proxy = _nearest_distinct(proxies, route_xy)
    if proxy is not None and _dist(proxy, route_xy) <= max_proxy_route_distance_m:
        f = _nearest_distinct(frontage, (proxy.x, proxy.y), avoid=avoid)
        if f is not None:
            d = math.hypot(f.x-proxy.x, f.y-proxy.y)
            if d <= max_proxy_frontage_distance_m:
                return f, "simulated_frontage_from_entrance_proxy", {
                    "entrance_proxy_node_id": proxy.node_id,
                    "entrance_proxy_source": proxy.source,
                    "proxy_to_frontage_m": round(d, 3),
                    "proxy_to_route_endpoint_m": round(_dist(proxy, route_xy), 3),
                }
    f = _nearest_distinct(frontage, route_xy, avoid=avoid)
    return f, "simulated_frontage_access_point", {}


def _choose_realistic_od(
    nodes: Sequence[AccessibilityNode],
    scene_meta: Mapping[str, Any] | None,
    rng: random.Random,
    *,
    allow_non_entrance_od: bool,
    trusted_source_tokens: Sequence[str] | None,
    max_entrance_route_distance_m: float,
    min_od_separation_m: float,
) -> Tuple[AccessibilityNode, AccessibilityNode, Dict[str, Any]]:
    """Choose OD anchored to nuPlan route endpoints and mapped pedestrian space.

    Real entrance/transit nodes are preferred when they are close to the route
    endpoints.  When the hybrid branch lacks one, we use the nearest *sidewalk
    frontage access point* as the request-level entrance anchor.  This is a
    transparent simulated service anchor, not a claim that a building doorway
    was measured at that coordinate.
    """
    route = _route_polyline(scene_meta)
    entrances = _entrance_nodes(nodes, trusted_source_tokens=trusted_source_tokens, require_minimum=False)
    if not route:
        if len(entrances) >= 2:
            o = rng.choice(entrances); d = _farthest_from(entrances, o) or rng.choice([x for x in entrances if x.node_id != o.node_id])
            return o, d, {"origin_kind": "observed_entrance", "destination_kind": "observed_entrance", "method": "mapped_entrance_fallback_without_route"}
        if not allow_non_entrance_od:
            raise RuntimeError("service layer requires at least two trusted entrance nodes when route context is unavailable")
        frontage = _frontage_nodes(nodes)
        if len(frontage) < 2:
            raise RuntimeError("hybrid service layer requires at least two real-map sidewalk frontage nodes when mapped entrances are unavailable")
        o = rng.choice(frontage); d = _farthest_from(frontage, o)
        assert d is not None
        return o, d, {"origin_kind": "simulated_frontage_access_point", "destination_kind": "simulated_frontage_access_point", "method": "frontage_fallback_without_route"}

    start_xy, end_xy = route[0], route[-1]
    o_real = _nearest_distinct(entrances, start_xy)
    d_real = _nearest_distinct(entrances, end_xy, avoid=o_real.node_id if o_real else None)
    o_use_real = o_real is not None and _dist(o_real, start_xy) <= max_entrance_route_distance_m
    d_use_real = d_real is not None and _dist(d_real, end_xy) <= max_entrance_route_distance_m

    frontage = _frontage_nodes(nodes) if allow_non_entrance_od and (not o_use_real or not d_use_real) else []
    o_extra: Dict[str, Any] = {}
    d_extra: Dict[str, Any] = {}
    if not o_use_real:
        if not allow_non_entrance_od:
            raise RuntimeError(f"no trusted entrance within {max_entrance_route_distance_m:.1f} m of nuPlan route origin")
        o, o_kind, o_extra = _frontage_near_route_or_proxy(
            nodes, start_xy, max_proxy_route_distance_m=max_entrance_route_distance_m
        )
    else:
        o = o_real; o_kind = "observed_entrance"
    if o is None:
        raise RuntimeError("no physically anchored origin access point available")

    if not d_use_real:
        if not allow_non_entrance_od:
            raise RuntimeError(f"no trusted entrance within {max_entrance_route_distance_m:.1f} m of nuPlan route destination")
        d, d_kind, d_extra = _frontage_near_route_or_proxy(
            nodes, end_xy, avoid=o.node_id, max_proxy_route_distance_m=max_entrance_route_distance_m
        )
    else:
        d = d_real; d_kind = "observed_entrance"
    if d is None:
        pool = entrances if len(entrances) >= 2 else frontage
        d = _farthest_from(pool, o)
        d_kind = "observed_entrance" if d in entrances else "simulated_frontage_access_point"
    if d is None:
        raise RuntimeError("no physically anchored destination access point available")

    sep = math.hypot(d.x-o.x, d.y-o.y)
    separation_adjustment = "none"
    if sep < min_od_separation_m:
        # Do not solve a short OD by jumping to the globally farthest graph node:
        # that breaks the correspondence between the passenger request and the
        # nuPlan route endpoint.  Prefer another route-local mapped entrance, then
        # a route-local real-map sidewalk frontage.  If neither can meet the
        # target, retain the best route-anchored destination and explicitly mark
        # that the target separation was relaxed.
        replacement = _route_local_destination_candidate(
            entrances, end_xy, o,
            max_route_distance_m=max_entrance_route_distance_m,
            min_od_separation_m=min_od_separation_m,
            avoid=o.node_id,
        )
        replacement_kind = "observed_entrance"
        if replacement is None and allow_non_entrance_od:
            frontage_pool = frontage or _frontage_nodes(nodes)
            replacement = _route_local_destination_candidate(
                frontage_pool, end_xy, o,
                max_route_distance_m=max_entrance_route_distance_m,
                min_od_separation_m=min_od_separation_m,
                avoid=o.node_id,
            )
            replacement_kind = "simulated_frontage_access_point"
        if replacement is not None:
            d = replacement
            d_kind = replacement_kind
            d_extra = {
                "reselected_for_min_od_separation": True,
                "route_endpoint_distance_m": round(_dist(d, end_xy), 3),
            }
            sep = math.hypot(d.x-o.x, d.y-o.y)
            separation_adjustment = "route_local_destination_reselection"
        else:
            separation_adjustment = "kept_route_anchored_short_od"

    prov = {
        "origin_kind": o_kind,
        "destination_kind": d_kind,
        "method": "nuplan_route_endpoint_to_mapped_entrance_or_frontage",
        "route_origin_distance_m": round(_dist(o, start_xy), 3),
        "route_destination_distance_m": round(_dist(d, end_xy), 3),
        "od_euclidean_separation_m": round(sep, 3),
        "od_separation_target_m": round(float(min_od_separation_m), 3),
        "od_separation_target_met": bool(sep + 1e-9 >= min_od_separation_m),
        "od_separation_adjustment": separation_adjustment,
        "claim_scope": "request_level_benchmark_anchor_not_measured_building_door" if "simulated" in (o_kind+d_kind) else "mapped_entrance_anchor",
        "origin_proxy_context": o_extra,
        "destination_proxy_context": d_extra,
    }
    return o, d, prov


def _local_time_metadata(request_time_s: float, map_name: str | None) -> Dict[str, Any]:
    tz_name = {
        "us-ma-boston": "America/New_York",
        "us-pa-pittsburgh-hazelwood": "America/New_York",
        "us-nv-las-vegas-strip": "America/Los_Angeles",
        "sg-one-north": "Asia/Singapore",
    }.get(str(map_name or ""))
    if request_time_s >= 10_000_000 and tz_name:
        try:
            dt = datetime.fromtimestamp(request_time_s, ZoneInfo(tz_name))
            return {"timezone": tz_name, "local_hour": round(dt.hour + dt.minute/60.0, 3), "local_iso": dt.isoformat()}
        except Exception:
            pass
    hour = (request_time_s % 86400.0) / 3600.0
    return {"timezone": tz_name or "unknown", "local_hour": round(hour, 3), "local_iso": None}


def _curb_side_for_map(map_name: str | None) -> str:
    return "left" if str(map_name or "") == "sg-one-north" else "right"


def _select_vehicle_id(fleet_jsonl: str | None, eid: str, map_name: str | None, seed: int) -> Tuple[str | None, Dict[str, Any]]:
    if not fleet_jsonl:
        return None, {}
    fleet = load_fleet_interfaces(fleet_jsonl)
    vehicles = fleet.get(eid) or fleet.get("*") or []
    if not vehicles:
        return None, {}
    curb_side = _curb_side_for_map(map_name)
    compatible = [v for v in vehicles if v.door_side in {curb_side, "both"} or curb_side in set(v.boarding_sides or [])]
    pool = compatible or vehicles
    # Deterministic per episode, deliberately diverse across the benchmark.
    idx = _stable_seed(seed, eid, "primary_vehicle") % len(pool)
    v = sorted(pool, key=lambda x: x.vehicle_id)[idx]
    return v.vehicle_id, {
        "vehicle_assignment_method": "deterministic_city_curb_side_compatible_hybrid_fleet",
        "city_curb_side": curb_side,
        "vehicle_door_side": v.door_side,
        "vehicle_fleet_type": v.fleet_type,
        "vehicle_source": (v.metadata or {}).get("source"),
    }

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
                "preferred_door_side": "curb_side",
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
                "preferred_door_side": "curb_side",
                "min_door_width_m": 0.90,
                "min_deployment_clearance_m": 1.4,
                "boarding_any_of": [{"ramp": True}, {"lift": True}],
                "max_dwell_time_s": 210.0,
            },
            "ride": {"max_ride_time_s": 3000.0, "max_peak_accel_mps2": 1.5, "max_peak_jerk_mps3": 2.2, "max_motion_exposure": 2.0},
            "uncertainty": {"min_map_confidence": 0.80, "max_blockage_risk": 0.25, "max_deployment_risk": 0.15, "beta_tau": 1.5, "missing_policy": "fail_closed"},
        },
    ]


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
    profile_mix = [str(x) for x in (cfg.get("profile_mix") or profile_ids)]
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
    max_entrance_route_distance_m = float(cfg.get("max_entrance_route_distance_m", 250.0))
    min_od_separation_m = float(cfg.get("min_od_separation_m", 80.0))
    scene_meta = _load_scene_metadata(args.scene_dataset_dir)
    rows: List[Dict[str, Any]] = []
    trusted_tokens = None
    if args.require_trusted_entrances:
        trusted_tokens = ["reviewed_audit:", "manual_audit:"] + list(args.trusted_entrance_source or [])
    for eid in episode_ids:
        rng = random.Random(_stable_seed(args.seed, eid, "service_request"))
        nodes = _load_nodes(graph_dir, str(eid))
        emeta = scene_meta.get(eid, {})
        o, d, od_prov = _choose_realistic_od(
            nodes, emeta, rng,
            allow_non_entrance_od=args.allow_non_entrance_od,
            trusted_source_tokens=trusted_tokens,
            max_entrance_route_distance_m=max_entrance_route_distance_m,
            min_od_separation_m=min_od_separation_m,
        )
        raw_scene_time = emeta.get("request_time_s")
        try:
            request_time_s = float(raw_scene_time)
            if not math.isfinite(request_time_s) or request_time_s <= 0:
                raise ValueError
            request_time_source = "nuplan_scene_timestamp"
        except Exception:
            request_time_s = round(request_time_start + rng.random() * request_time_span, 3)
            request_time_source = "deterministic_city_agnostic_time_prior"
        time_meta = _local_time_metadata(request_time_s, emeta.get("map_name"))
        vehicle_id, vehicle_meta = _select_vehicle_id(args.fleet_jsonl, eid, emeta.get("map_name"), args.seed)
        cf_group_id = f"{eid}:cf_od0"
        base_profile_id = str(cfg.get("counterfactual_base_profile_id") or profile_mix[0])
        hybrid_proxy = str(od_prov.get("origin_kind") or "").startswith("simulated_frontage") or str(od_prov.get("destination_kind") or "").startswith("simulated_frontage")
        for i in range(args.num_requests_per_episode):
            pid = str(profile_mix[i])
            if pid not in profile_ids:
                raise RuntimeError(f"demand config references missing profile id {pid}")
            pmeta = profile_by_id[pid]
            is_base = pid == base_profile_id
            relation = str(pmeta.get("counterfactual_relation") or "stricter_or_equal")
            if relation not in {"stricter_or_equal", "different_modality", "different_interface"}:
                raise RuntimeError(f"profile {pid} has unsupported counterfactual_relation={relation!r}")
            row = {
                "request_id": f"{eid}:req_{i:04d}",
                "episode_id": str(eid),
                "origin_entrance_id": o.node_id,
                "destination_entrance_id": d.node_id,
                "origin_confidence": min(float(o.confidence), 0.72 if str(od_prov.get("origin_kind") or "").startswith("simulated_frontage") else 1.0),
                "destination_confidence": min(float(d.confidence), 0.72 if str(od_prov.get("destination_kind") or "").startswith("simulated_frontage") else 1.0),
                "request_time_s": request_time_s,
                "request_time_source": request_time_source,
                "request_local_hour": time_meta.get("local_hour"),
                "request_timezone": time_meta.get("timezone"),
                "request_local_iso": time_meta.get("local_iso"),
                "passenger_profile_id": pid,
                "trip_purpose": purposes[i % len(purposes)],
                "party_size": 1,
                "demand_weight": float(cfg.get("default_demand_weight", 1.0)),
                "modifiers": dict(cfg.get("modifiers", {})),
                "source": args.source_name,
                "map_name": emeta.get("map_name"),
                "vehicle_id": vehicle_id,
                "counterfactual_group_id": cf_group_id,
                "counterfactual_role": "base" if is_base else "variant",
                "counterfactual_base_profile_id": base_profile_id,
                "counterfactual_axis": pmeta.get("counterfactual_axis") or ("base" if is_base else pmeta.get("archetype")),
                "counterfactual_relation": relation,
                "expected_monotonic": bool(pmeta.get("expected_monotonic", not is_base and relation == "stricter_or_equal")),
                "bootstrap_non_entrance_od": bool(args.allow_non_entrance_od and hybrid_proxy),
                "hybrid_frontage_proxy_od": bool(args.source_policy == "hybrid" and hybrid_proxy),
                "od_provenance": {**od_prov, "kind": "simulated" if hybrid_proxy else "observed_or_derived", "source": "nuplan_route+prepared_accessibility_graph"},
                **vehicle_meta,
            }
            rows.append(row)
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
        "source_policy": args.source_policy,
        "od_provenance_counts": {k: sum(1 for r in rows if str((r.get("od_provenance") or {}).get("kind")) == k) for k in ["observed_or_derived", "simulated"]},
        "frontage_proxy_request_count": sum(1 for r in rows if bool(r.get("hybrid_frontage_proxy_od"))),
        "request_time_source_counts": {k: sum(1 for r in rows if str(r.get("request_time_source")) == k) for k in sorted({str(r.get("request_time_source")) for r in rows})},
        "vehicle_assignment_counts": {k: sum(1 for r in rows if str(r.get("vehicle_id")) == k) for k in sorted({str(r.get("vehicle_id")) for r in rows if r.get("vehicle_id")})},
    }
    if args.report_json:
        dump_json(args.report_json, report)
    return report


def main() -> None:
    p = argparse.ArgumentParser(description="Build calibrated passenger service requests and three-layer capability profiles for AbilityBench-AV.")
    p.add_argument("--scene_dataset_dir", default=None, help="City scene directory or split-level scene_contexts root; used to anchor OD and request time to nuPlan.")
    p.add_argument("--source_policy", choices=["bootstrap", "hybrid", "paper"], default="bootstrap")
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
    p.add_argument("--allow_non_entrance_od", action="store_true", help="Bootstrap/hybrid only: if mapped entrances are unavailable near route endpoints, use a real-map sidewalk frontage access point with explicit simulated OD provenance. Never valid for paper-mode datasets.")
    p.add_argument("--episode_allowlist", default=None, help="Optional text/JSON episode-id allowlist. Paper builds should use an allowlist selected from audited PUDO + entrance evidence instead of forcing every nuPlan scenario into the main-result set.")
    p.add_argument("--require_trusted_entrances", action="store_true", help="Restrict O/D sampling to independently audited/trusted entrance nodes. Manual/reviewed-audit sources are trusted by default.")
    p.add_argument("--trusted_entrance_source", action="append", default=[], help="Additional trusted entrance source substring; repeat as needed.")
    p.add_argument("--report_json", default=None)
    args = p.parse_args()
    print(json.dumps(build(args), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
