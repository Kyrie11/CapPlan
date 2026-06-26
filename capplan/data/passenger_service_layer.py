"""Passenger-service layer ingestion for paper-mode datasets.

This module deliberately separates real/calibrated service requests from the
synthetic smoke layer.  It only normalizes explicitly provided JSONL/YAML data;
it does not infer missing entrances or vehicles from nuPlan ego poses.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, Iterable, List

import yaml

from capplan.data.schemas import AccessibilityGraph, EntranceAnchor, Pose2D, VehicleInterface, vehicle_from_dict
from capplan.utils.serialization import read_jsonl

_REQUIRED_REQUEST_FIELDS = {
    "request_id",
    "episode_id",
    "origin_entrance_id",
    "destination_entrance_id",
    "request_time_s",
    "passenger_profile_id",
    "source",
}


def _read_records(path: str | Path) -> List[Dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    if p.suffix.lower() in {".yaml", ".yml"}:
        payload = yaml.safe_load(p.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            for key in ["service_requests", "fleet", "profiles", "vehicles"]:
                if isinstance(payload.get(key), list):
                    return [dict(x) for x in payload[key]]
            return [payload]
        if isinstance(payload, list):
            return [dict(x) for x in payload]
        return []
    return read_jsonl(p)


def validate_service_request(row: Dict[str, Any]) -> Dict[str, Any]:
    missing = sorted(k for k in _REQUIRED_REQUEST_FIELDS if row.get(k) in (None, ""))
    if missing:
        raise ValueError(f"service request missing required fields {missing}: {row.get('request_id') or row.get('episode_id')}")
    source = str(row.get("source", "")).lower()
    if source.startswith("synthetic") or "proxy" in source or source in {"mock", "toy"}:
        raise ValueError(f"real service layer rejects synthetic/proxy source for request {row.get('request_id')}: {row.get('source')}")
    out = dict(row)
    out["episode_id"] = str(out["episode_id"])
    out["origin_entrance_id"] = str(out["origin_entrance_id"])
    out["destination_entrance_id"] = str(out["destination_entrance_id"])
    out["request_time_s"] = float(out["request_time_s"])
    out.setdefault("trip_purpose", "other")
    out.setdefault("party_size", 1)
    out.setdefault("demand_weight", 1.0)
    out.setdefault("modifiers", {})
    return out


def load_service_requests_by_episode(path: str | Path) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for row in _read_records(path):
        req = validate_service_request(row)
        out.setdefault(req["episode_id"], []).append(req)
    return out


def _node_by_id(graph: AccessibilityGraph, node_id: str):
    for n in graph.nodes:
        if n.node_id == node_id:
            return n
    return None


def bind_service_request_to_graph(request: Dict[str, Any], graph: AccessibilityGraph) -> tuple[EntranceAnchor, EntranceAnchor]:
    """Bind a real request to entrance nodes in the prepared accessibility graph.

    Missing entrance nodes are hard errors in paper mode.  The caller may build
    or repair the accessibility graph beforehand, but this function never snaps
    to the nearest synthetic proxy.
    """
    eid = str(request["episode_id"])
    if eid != graph.episode_id:
        raise ValueError(f"service request episode {eid} does not match graph {graph.episode_id}")
    oid = str(request["origin_entrance_id"])
    did = str(request["destination_entrance_id"])
    on = _node_by_id(graph, oid)
    dn = _node_by_id(graph, did)
    if on is None or dn is None:
        missing = [x for x, n in [(oid, on), (did, dn)] if n is None]
        raise ValueError(f"service request {request.get('request_id')} references entrance node(s) not present in graph {graph.episode_id}: {missing}")
    for node, label in [(on, "origin"), (dn, "destination")]:
        if node.kind not in {"entrance", "origin_entrance", "destination_entrance", "transit_stop"}:
            raise ValueError(f"service request {request.get('request_id')} {label} node {node.node_id} is not an entrance/transit_stop: kind={node.kind}")
        src = str(node.source or request.get("source", ""))
        if src.startswith("synthetic") or "proxy" in src.lower():
            raise ValueError(f"service request {request.get('request_id')} rejects proxy/synthetic entrance source: {node.node_id} source={node.source}")
    origin = EntranceAnchor(oid, eid, "origin_entrance", Pose2D(on.x, on.y, on.pose.heading if on.pose else 0.0, on.pose.frame if on.pose else "map"), oid, min(float(on.confidence), float(request.get("origin_confidence", 1.0))), str(on.source or request.get("source")))
    destination = EntranceAnchor(did, eid, "destination_entrance", Pose2D(dn.x, dn.y, dn.pose.heading if dn.pose else 0.0, dn.pose.frame if dn.pose else "map"), did, min(float(dn.confidence), float(request.get("destination_confidence", 1.0))), str(dn.source or request.get("source")))
    return origin, destination




def bind_bootstrap_service_request_to_graph(request: Dict[str, Any], graph: AccessibilityGraph) -> tuple[EntranceAnchor, EntranceAnchor]:
    """Bind diagnostic OD requests to graph nodes even when they are not entrances.

    This is only for bootstrap/debugging runs with incomplete city entrance data.
    Paper-mode callers must use ``bind_service_request_to_graph``.
    """
    eid = str(request["episode_id"])
    oid = str(request["origin_entrance_id"])
    did = str(request["destination_entrance_id"])
    on = _node_by_id(graph, oid)
    dn = _node_by_id(graph, did)
    if on is None or dn is None:
        missing = [x for x, n in [(oid, on), (did, dn)] if n is None]
        raise ValueError(f"bootstrap service request {request.get('request_id')} references missing node(s) in graph {graph.episode_id}: {missing}")
    origin = EntranceAnchor(oid, eid, "origin_entrance", Pose2D(on.x, on.y, on.pose.heading if on.pose else 0.0, on.pose.frame if on.pose else "map"), oid, min(float(on.confidence), float(request.get("origin_confidence", 1.0))), str(on.source or request.get("source")))
    destination = EntranceAnchor(did, eid, "destination_entrance", Pose2D(dn.x, dn.y, dn.pose.heading if dn.pose else 0.0, dn.pose.frame if dn.pose else "map"), did, min(float(dn.confidence), float(request.get("destination_confidence", 1.0))), str(dn.source or request.get("source")))
    return origin, destination


def load_fleet_interfaces(path: str | Path) -> Dict[str, List[VehicleInterface]]:
    by_ep: Dict[str, List[VehicleInterface]] = {}
    for row in _read_records(path):
        d = dict(row)
        if not d.get("episode_id"):
            raise ValueError(f"fleet row missing episode_id: {d.get('vehicle_id')}")
        source = str(d.get("source") or d.get("metadata", {}).get("source") or "").lower()
        if source.startswith("synthetic") or "proxy" in source or source in {"mock", "toy"}:
            raise ValueError(f"real fleet rejects synthetic/proxy source for vehicle {d.get('vehicle_id')}: {source}")
        meta = dict(d.get("metadata") or {})
        if d.get("source"):
            meta["source"] = d.get("source")
        d["metadata"] = meta
        # Accept service-layer names as aliases.
        if "interface_spec_id" in d and "vehicle_id" not in d:
            d["vehicle_id"] = d["interface_spec_id"]
        if "vehicle_type" in d and "fleet_type" not in d:
            d["fleet_type"] = d["vehicle_type"]
        allowed = set(VehicleInterface.__dataclass_fields__.keys())
        v = vehicle_from_dict({k: v for k, v in d.items() if k in allowed})
        by_ep.setdefault(v.episode_id, []).append(v)
    return by_ep


def service_request_to_trip_context(request: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "request_id": request.get("request_id"),
        "request_time_s": float(request.get("request_time_s", 0.0)),
        "trip_purpose": request.get("trip_purpose", "other"),
        "party_size": int(request.get("party_size", 1) or 1),
        "passenger_profile_id": request.get("passenger_profile_id"),
        "vehicle_id": request.get("vehicle_id") or request.get("fleet_vehicle_id"),
        "demand_weight": float(request.get("demand_weight", 1.0) or 1.0),
        "trip_modifiers": dict(request.get("modifiers") or {}),
        "service_request_source": request.get("source"),
    }
