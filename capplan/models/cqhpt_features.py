"""Capability-Query Heterogeneous Polyline Transformer features (V14).

The feature policy is intentionally leakage-aware.  The evidence-token branch
uses lower-level geometry, topology, provenance and dynamic context.  It does
not feed verifier-level processed accessibility resources (e.g. final slope,
path width, curb height or deployment clearance) back as trivial token values.
Those authoritative fields remain in the exact typed ledger and may influence
only the *capability-state query* through residual/margin state.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Dict, Iterable, List, Mapping, Sequence, Tuple

from capplan.data.schemas import AccessibilityGraph, CandidateTransition, PUDOAnchor, SceneRecord, VehicleInterface
from capplan.semantics.capability_compiler import CompiledContract, PHASE_VOCAB, RESOURCE_VOCAB
from capplan.semantics.resource_registry import DEFAULT_REGISTRY, ResourceRegistry
from capplan.semantics.typed_resource_algebra import is_missing, signed_margin

CQHPT_FEATURE_VERSION = "cqhpt_raw_evidence_v1_20260906"

TOKEN_TYPES = ["path", "pudo", "vehicle", "agent", "route", "rule", "transition", "provenance"]
TOKEN_TYPE_TO_ID = {n: i for i, n in enumerate(TOKEN_TYPES)}
RAW_TOKEN_DIM = 18


@dataclass(frozen=True)
class CQHPTFeatureConfig:
    max_tokens: int = 48
    max_path_tokens: int = 20
    max_agent_tokens: int = 12
    max_pudo_tokens: int = 8
    query_clip: float = 4.0


def _f(v: Any, default: float = 0.0) -> float:
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def _source_flags(source: str, metadata: Mapping[str, Any] | None = None) -> Tuple[float, float, float]:
    s = (str(source or "") + " " + repr(dict(metadata or {}))).lower()
    observed = 1.0 if any(k in s for k in ("observed", "municipal", "osm", "gis", "sensor", "nuplan")) else 0.0
    derived = 1.0 if any(k in s for k in ("derived", "dem", "inferred", "computed")) else 0.0
    simulated = 1.0 if any(k in s for k in ("simulated", "synthetic", "prior", "proxy", "benchmark")) else 0.0
    if observed + derived + simulated <= 0:
        derived = 1.0
    return observed, derived, simulated


def _pose_xy(obj: Any) -> Tuple[float, float, float]:
    if obj is None:
        return 0.0, 0.0, 0.0
    if hasattr(obj, "x") and hasattr(obj, "y"):
        return _f(obj.x), _f(obj.y), _f(getattr(obj, "heading", 0.0))
    if isinstance(obj, Mapping):
        pose = obj.get("pose") if isinstance(obj.get("pose"), Mapping) else obj
        return _f(pose.get("x")), _f(pose.get("y")), _f(pose.get("heading", pose.get("yaw", 0.0)))
    return 0.0, 0.0, 0.0


def _raw_elevation_pair(metadata: Mapping[str, Any] | None) -> Tuple[float, float, float]:
    md = dict(metadata or {})
    keys_a = ["elevation_start_m", "from_elevation_m", "start_elevation_m", "raw_elevation_start_m"]
    keys_b = ["elevation_end_m", "to_elevation_m", "end_elevation_m", "raw_elevation_end_m"]
    a = next((_f(md[k], float("nan")) for k in keys_a if k in md), float("nan"))
    b = next((_f(md[k], float("nan")) for k in keys_b if k in md), float("nan"))
    if math.isfinite(a) and math.isfinite(b):
        return a / 100.0, b / 100.0, (b - a) / 5.0
    # Some builders retain rejected/raw evidence as nested dictionaries.
    rej = md.get("rejected_field_evidence")
    if isinstance(rej, Mapping):
        vals = []
        for v in rej.values():
            if isinstance(v, Mapping):
                for kk in ("elevation_m", "value", "original_value"):
                    if kk in v:
                        val = _f(v[kk], float("nan"))
                        if math.isfinite(val):
                            vals.append(val)
                            break
        if len(vals) >= 2:
            return vals[0] / 100.0, vals[-1] / 100.0, (vals[-1] - vals[0]) / 5.0
    return 0.0, 0.0, 0.0


def _token(
    token_type: str,
    *,
    x: float = 0.0,
    y: float = 0.0,
    heading: float = 0.0,
    ref: Tuple[float, float] = (0.0, 0.0),
    length: float = 0.0,
    dx: float = 0.0,
    dy: float = 0.0,
    confidence: float = 0.0,
    source: str = "",
    metadata: Mapping[str, Any] | None = None,
    dynamic1: float = 0.0,
    dynamic2: float = 0.0,
    binary1: float = 0.0,
    binary2: float = 0.0,
    elev_a: float = 0.0,
    elev_b: float = 0.0,
    elev_delta: float = 0.0,
) -> Dict[str, Any]:
    rx = (x - ref[0]) / 100.0
    ry = (y - ref[1]) / 100.0
    obs, der, sim = _source_flags(source, metadata)
    raw = [
        rx, ry, math.sin(heading), math.cos(heading),
        length / 100.0, dx / 100.0, dy / 100.0,
        max(0.0, min(1.0, confidence)),
        obs, der, sim,
        max(-4.0, min(4.0, dynamic1)), max(-4.0, min(4.0, dynamic2)),
        float(bool(binary1)), float(bool(binary2)),
        max(-4.0, min(4.0, elev_a)), max(-4.0, min(4.0, elev_b)), max(-4.0, min(4.0, elev_delta)),
    ]
    assert len(raw) == RAW_TOKEN_DIM
    return {"type_id": TOKEN_TYPE_TO_ID[token_type], "raw": raw, "rel_xy": [rx, ry], "abs_xy": [float(x), float(y)]}


def _transition_reference(transition: CandidateTransition, graph: AccessibilityGraph, pudos: Sequence[PUDOAnchor]) -> Tuple[float, float]:
    pmap = {p.anchor_id: p for p in pudos}
    for aid in (transition.to_anchor, transition.from_anchor):
        if aid in pmap:
            return _f(pmap[aid].curb_pose.x), _f(pmap[aid].curb_pose.y)
    nmap = {n.node_id: n for n in graph.nodes}
    for aid in (transition.to_anchor, transition.from_anchor, str(transition.metadata.get("adjacent_ped_node_id") or "")):
        if aid in nmap:
            return _f(nmap[aid].x), _f(nmap[aid].y)
    return 0.0, 0.0


def build_raw_evidence_tokens(
    *,
    transition: CandidateTransition,
    graph: AccessibilityGraph,
    pudos: Sequence[PUDOAnchor],
    vehicle: VehicleInterface,
    scene: Mapping[str, Any] | SceneRecord | None,
    config: CQHPTFeatureConfig | None = None,
) -> List[Dict[str, Any]]:
    """Build leakage-aware heterogeneous evidence tokens for one transition."""
    cfg = config or CQHPTFeatureConfig()
    ref = _transition_reference(transition, graph, pudos)
    tokens: List[Dict[str, Any]] = []
    edges = {e.edge_id: e for e in graph.edges}
    nodes = {n.node_id: n for n in graph.nodes}

    # Polyline/path primitives.  Geometry and raw elevation/provenance are used;
    # final width/slope/cross-slope resource values are intentionally excluded.
    path_ids = list(transition.metadata.get("path_edge_ids") or [])[: cfg.max_path_tokens]
    for eid in path_ids:
        edge = edges.get(str(eid))
        if edge is None:
            continue
        pts = list(edge.geometry or [])
        if len(pts) >= 2:
            ax, ay = _f(pts[0][0]), _f(pts[0][1])
            bx, by = _f(pts[-1][0]), _f(pts[-1][1])
        else:
            a, b = nodes.get(edge.from_node), nodes.get(edge.to_node)
            ax, ay = (_f(a.x), _f(a.y)) if a else (0.0, 0.0)
            bx, by = (_f(b.x), _f(b.y)) if b else (ax, ay)
        mx, my = 0.5 * (ax + bx), 0.5 * (ay + by)
        heading = math.atan2(by - ay, bx - ax) if abs(bx - ax) + abs(by - ay) > 1e-9 else 0.0
        ea, eb, ed = _raw_elevation_pair(edge.metadata)
        tokens.append(_token(
            "path", x=mx, y=my, heading=heading, ref=ref,
            length=_f(edge.length_m), dx=bx-ax, dy=by-ay,
            confidence=_f(edge.confidence), source=edge.source, metadata=edge.metadata,
            dynamic1=1.0 if edge.obstacle else 0.0,
            dynamic2=1.0 if str(edge.obstacle_state or "").lower() in {"blocked", "closed"} else 0.0,
            binary1=bool(edge.geometry),
            binary2=bool(edge.crossing_type),
            elev_a=ea, elev_b=eb, elev_delta=ed,
        ))

    # Relevant and nearby PUDO geometry.  Do not feed curb height / sidewalk
    # width / deployment-clearance benchmark truth into the raw token branch.
    anchor_ids = {transition.from_anchor, transition.to_anchor, str(transition.metadata.get("pickup_anchor") or ""), str(transition.metadata.get("dropoff_anchor") or "")}
    ordered_pudos = sorted(pudos, key=lambda p: (0 if p.anchor_id in anchor_ids else 1, (p.curb_pose.x-ref[0])**2 + (p.curb_pose.y-ref[1])**2))
    for p in ordered_pudos[: cfg.max_pudo_tokens]:
        dx = _f(p.stop_pose.x) - _f(p.curb_pose.x)
        dy = _f(p.stop_pose.y) - _f(p.curb_pose.y)
        heading = math.atan2(dy, dx) if abs(dx)+abs(dy) > 1e-9 else _f(p.curb_pose.heading)
        tokens.append(_token(
            "pudo", x=_f(p.curb_pose.x), y=_f(p.curb_pose.y), heading=heading, ref=ref,
            length=math.hypot(dx, dy), dx=dx, dy=dy,
            confidence=min(_f(p.map_confidence), _f(p.dynamic_confidence)), source=p.source,
            metadata=p.field_provenance,
            dynamic1=_f(p.blockage_risk), dynamic2=_f(p.dynamic_confidence),
            binary1=bool(p.legal_stop), binary2=(str(p.side) in {"left", "right", "both"}),
        ))
        tokens.append(_token(
            "rule", x=_f(p.stop_pose.x), y=_f(p.stop_pose.y), heading=_f(p.stop_pose.heading), ref=ref,
            confidence=_f(p.map_confidence), source=p.legal_stop_source,
            dynamic1=_f(p.blockage_risk), binary1=bool(p.legal_stop),
            binary2=str(p.side) == str(vehicle.door_side) or str(vehicle.door_side) == "both" or str(p.side) == "both",
        ))

    # Vehicle interface as a heterogeneous token.  These are service-interface
    # facts, not learned hard authority; CQ-HPT can use them only to rank exact-
    # feasible successors.
    side_code = {"left": -1.0, "right": 1.0, "both": 0.0}.get(str(vehicle.door_side), 0.0)
    tokens.append(_token(
        "vehicle", ref=ref, confidence=1.0, source="vehicle_interface",
        dynamic1=_f(vehicle.dwell_time_s) / 60.0, dynamic2=side_code,
        binary1=bool(vehicle.ramp or vehicle.lift), binary2=bool(vehicle.low_floor or vehicle.kneeling),
    ))

    # Dynamic agents: latest available states only.  The loader is schema-
    # tolerant because nuPlan-derived scene histories may use different keys.
    scene_dict = scene.__dict__ if hasattr(scene, "__dict__") else dict(scene or {})
    agent_history = list(scene_dict.get("agent_history") or [])
    latest: Dict[str, Mapping[str, Any]] = {}
    for idx, a in enumerate(agent_history):
        if not isinstance(a, Mapping):
            continue
        aid = str(a.get("track_token") or a.get("agent_id") or a.get("id") or idx)
        latest[aid] = a
    agents = list(latest.values())
    agents.sort(key=lambda a: (_pose_xy(a)[0]-ref[0])**2 + (_pose_xy(a)[1]-ref[1])**2)
    for a in agents[: cfg.max_agent_tokens]:
        x, y, hd = _pose_xy(a)
        vx = _f(a.get("vx", a.get("velocity_x", 0.0))) if isinstance(a, Mapping) else 0.0
        vy = _f(a.get("vy", a.get("velocity_y", 0.0))) if isinstance(a, Mapping) else 0.0
        speed = math.hypot(vx, vy)
        tokens.append(_token(
            "agent", x=x, y=y, heading=hd, ref=ref, length=speed,
            dx=vx, dy=vy, confidence=_f(a.get("confidence", 1.0)) if isinstance(a, Mapping) else 1.0,
            source="nuplan_agent_history", dynamic1=speed/15.0,
            dynamic2=_f(a.get("acceleration", 0.0))/5.0 if isinstance(a, Mapping) else 0.0,
            binary1=True,
        ))

    route = scene_dict.get("route_corridor") if isinstance(scene_dict.get("route_corridor"), Mapping) else {}
    route_length = _f((route or {}).get("length_m", transition.metadata.get("route_length_m", 0.0)))
    tokens.append(_token("route", ref=ref, length=route_length, confidence=1.0, source="route_corridor", dynamic1=route_length/5000.0))

    # Candidate structural token; this contains no oracle labels or processed
    # resource targets.
    tokens.append(_token(
        "transition", ref=ref, length=max(0.0, _f(transition.cost)), confidence=_f(transition.map_confidence),
        source="candidate_transition", dynamic1=max(0.0, min(1.0, _f(transition.availability))),
        binary1=bool(transition.tests.legal_lifecycle), binary2=bool(transition.tests.dynamically_available),
    ))

    # Stable deterministic truncation: relevant path/PUDO tokens were inserted
    # first; retain one transition token by replacing the final slot if needed.
    if len(tokens) > cfg.max_tokens:
        tokens = tokens[: cfg.max_tokens - 1] + [tokens[-1]]
    return tokens


def _phase_indices(scopes: Sequence[str]) -> List[int]:
    if "all" in scopes:
        return list(range(len(PHASE_VOCAB)))
    return [PHASE_VOCAB.index(q) for q in scopes if q in PHASE_VOCAB]


def cqhpt_query_feature_names() -> List[str]:
    names = [f"phase::{q}" for q in PHASE_VOCAB] + [f"action::{a}" for a in ("access","wait","board","ride","alight","egress","replan")]
    names += ["history_frac", "remaining_phase_frac", "log1p_transition_cost", "hard_clause_count", "group_count", "group_any_of", "group_all_of"]
    for r in RESOURCE_VOCAB:
        names += [f"ledger_observed::{r}", f"ledger_margin::{r}", f"active_now::{r}", f"active_future::{r}"]
    return names


def build_capability_query_features(
    *, successor_label: Any, transition: CandidateTransition, compiled: CompiledContract,
    registry: ResourceRegistry = DEFAULT_REGISTRY, neutral_query: bool = False,
    drop_groups: bool = False,
) -> List[float]:
    phase = str(getattr(successor_label, "phase", transition.to_phase))
    phase_idx = PHASE_VOCAB.index(phase) if phase in PHASE_VOCAB else 0
    ledger = dict(getattr(successor_label, "resource_ledger", {}) or {})
    history = list(getattr(successor_label, "history", []) or [])
    actions = ["access","wait","board","ride","alight","egress","replan"]
    x: List[float] = [1.0 if q == phase else 0.0 for q in PHASE_VOCAB]
    x += [1.0 if a == transition.action else 0.0 for a in actions]
    hard = [c for c in compiled.clauses if c.hard]
    groups = [] if drop_groups else [g for g in compiled.groups if g.hard]
    x += [
        min(1.0, len(history)/7.0), max(0.0, (7-phase_idx)/7.0), math.log1p(max(0.0, _f(transition.cost)))/8.0,
        min(1.0, len(hard)/32.0), min(1.0, len(groups)/8.0),
        min(1.0, sum(1 for g in groups if g.logic == "any_of")/4.0),
        min(1.0, sum(1 for g in groups if g.logic == "all_of")/4.0),
    ]
    by_resource: Dict[str, List[Any]] = {}
    for c in hard:
        by_resource.setdefault(c.resource_name, []).append(c)
    for r in RESOURCE_VOCAB:
        state = ledger.get(r)
        observed = state is not None and not is_missing(state)
        margin = 0.0
        if observed and r in by_resource:
            vals = []
            for c in by_resource[r]:
                try:
                    m = _f(signed_margin(ledger, c, registry))
                    vals.append(max(-4.0, min(4.0, m))/4.0)
                except Exception:
                    pass
            if vals:
                margin = min(vals)
        now = 0.0; future = 0.0
        for c in by_resource.get(r, []):
            inds = _phase_indices(c.phase_scope)
            if phase_idx in inds:
                now = 1.0
            if any(i > phase_idx for i in inds):
                future = 1.0
        if neutral_query:
            observed = False; margin = 0.0; now = 0.0; future = 0.0
        x += [1.0 if observed else 0.0, margin, now, future]
    if neutral_query:
        # Keep only structural phase/action/cost/history. Clause/group scalars are
        # also removed so the matched-capacity control is genuinely passenger-
        # neutral at the query-routing stage.
        base = len(PHASE_VOCAB) + len(actions)
        x[base+3:base+7] = [0.0, 0.0, 0.0, 0.0]
    names = cqhpt_query_feature_names()
    if len(x) != len(names):
        raise AssertionError(f"CQ-HPT query length mismatch: {len(x)} != {len(names)}")
    return x


CQHPT_EVIDENCE_CACHE_VERSION = "cqhpt_episode_evidence_cache_v1_20260906"


def build_episode_evidence_bank(
    *, graph: AccessibilityGraph, pudos: Sequence[PUDOAnchor], scene: Mapping[str, Any] | SceneRecord | None,
    transitions: Sequence[CandidateTransition], max_path_bank: int = 0, max_agents: int = 24,
) -> Dict[str, Any]:
    """Build one passenger-independent compact evidence bank per episode.

    Only path edges referenced by saved service transitions are retained.  This
    avoids reloading the full accessibility graph during every evaluation while
    preserving the lower-level geometry/elevation/provenance that CQ-HPT needs.
    """
    edge_map = {e.edge_id: e for e in graph.edges}
    node_map = {n.node_id: n for n in graph.nodes}
    path_ids: List[str] = []
    seen = set()
    for t in transitions:
        for eid in list(t.metadata.get("path_edge_ids") or []):
            eid = str(eid)
            if eid not in seen:
                path_ids.append(eid); seen.add(eid)
    # Keep the complete service-referenced path universe by default.  A positive
    # explicit cap is available only as an engineering stress option and its
    # truncation is recorded; publication/default evidence routing is lossless
    # with respect to the service transitions already frozen in the benchmark.
    retained = path_ids if max_path_bank <= 0 else path_ids[: max_path_bank]
    tokens: List[Dict[str, Any]] = []
    for eid in retained:
        edge = edge_map.get(eid)
        if edge is None:
            continue
        pts = list(edge.geometry or [])
        if len(pts) >= 2:
            ax, ay = _f(pts[0][0]), _f(pts[0][1]); bx, by = _f(pts[-1][0]), _f(pts[-1][1])
        else:
            a, b = node_map.get(edge.from_node), node_map.get(edge.to_node)
            ax, ay = (_f(a.x), _f(a.y)) if a else (0.0, 0.0)
            bx, by = (_f(b.x), _f(b.y)) if b else (ax, ay)
        mx, my = 0.5*(ax+bx), 0.5*(ay+by)
        hd = math.atan2(by-ay, bx-ax) if abs(bx-ax)+abs(by-ay) > 1e-9 else 0.0
        ea, eb, ed = _raw_elevation_pair(edge.metadata)
        tok = _token(
            "path", x=mx, y=my, heading=hd, ref=(0.0, 0.0), length=_f(edge.length_m),
            dx=bx-ax, dy=by-ay, confidence=_f(edge.confidence), source=edge.source,
            metadata=edge.metadata, dynamic1=1.0 if edge.obstacle else 0.0,
            dynamic2=1.0 if str(edge.obstacle_state or "").lower() in {"blocked","closed"} else 0.0,
            binary1=bool(edge.geometry),
            binary2=bool(edge.crossing_type),
            elev_a=ea, elev_b=eb, elev_delta=ed,
        )
        tok["source_id"] = eid
        tokens.append(tok)

    for p in pudos:
        dx = _f(p.stop_pose.x)-_f(p.curb_pose.x); dy = _f(p.stop_pose.y)-_f(p.curb_pose.y)
        hd = math.atan2(dy, dx) if abs(dx)+abs(dy)>1e-9 else _f(p.curb_pose.heading)
        pt = _token(
            "pudo", x=_f(p.curb_pose.x), y=_f(p.curb_pose.y), heading=hd, ref=(0.0,0.0),
            length=math.hypot(dx,dy), dx=dx, dy=dy,
            confidence=min(_f(p.map_confidence),_f(p.dynamic_confidence)), source=p.source,
            metadata=p.field_provenance, dynamic1=_f(p.blockage_risk), dynamic2=_f(p.dynamic_confidence),
            binary1=bool(p.legal_stop), binary2=(str(p.side) in {"left","right","both"}),
        )
        pt["source_id"] = str(p.anchor_id); tokens.append(pt)
        rt = _token(
            "rule", x=_f(p.stop_pose.x), y=_f(p.stop_pose.y), heading=_f(p.stop_pose.heading), ref=(0.0,0.0),
            confidence=_f(p.map_confidence), source=p.legal_stop_source,
            dynamic1=_f(p.blockage_risk), binary1=bool(p.legal_stop),
        )
        rt["source_id"] = str(p.anchor_id); tokens.append(rt)

    scene_dict = scene.__dict__ if hasattr(scene, "__dict__") else dict(scene or {})
    latest: Dict[str, Mapping[str, Any]] = {}
    for idx, a in enumerate(list(scene_dict.get("agent_history") or [])):
        if isinstance(a, Mapping):
            aid = str(a.get("track_token") or a.get("agent_id") or a.get("id") or idx); latest[aid] = a
    ego = _pose_xy(scene_dict.get("initial_ego_pose") or {})
    agents = list(latest.items())
    agents.sort(key=lambda kv: (_pose_xy(kv[1])[0]-ego[0])**2 + (_pose_xy(kv[1])[1]-ego[1])**2)
    for aid, a in agents[:max_agents]:
        x,y,hd=_pose_xy(a); vx=_f(a.get("vx",a.get("velocity_x",0.0))); vy=_f(a.get("vy",a.get("velocity_y",0.0)))
        at=_token("agent",x=x,y=y,heading=hd,ref=(0.0,0.0),length=math.hypot(vx,vy),dx=vx,dy=vy,
                  confidence=_f(a.get("confidence",1.0)),source="nuplan_agent_history",dynamic1=math.hypot(vx,vy)/15.0,
                  dynamic2=_f(a.get("acceleration",0.0))/5.0,binary1=True)
        at["source_id"] = aid; tokens.append(at)
    route = scene_dict.get("route_corridor") if isinstance(scene_dict.get("route_corridor"), Mapping) else {}
    route_length = _f((route or {}).get("length_m", 0.0))
    tokens.append(_token("route", ref=(0.0,0.0), length=route_length, confidence=1.0, source="route_corridor", dynamic1=route_length/5000.0))
    refs = {str(t.transition_id): list(_transition_reference(t, graph, pudos)) for t in transitions}
    return {
        "version": CQHPT_EVIDENCE_CACHE_VERSION,
        "episode_id": graph.episode_id,
        "tokens": tokens,
        "transition_refs": refs,
        "path_edge_count_raw": len(path_ids),
        "path_edge_count_retained": len(retained),
        "path_bank_truncated": len(retained) < len(path_ids),
    }


def select_transition_tokens_from_bank(
    *, bank: Mapping[str, Any], transition: CandidateTransition, vehicle: VehicleInterface,
    config: CQHPTFeatureConfig | None = None,
) -> List[Dict[str, Any]]:
    """Select a transition-local KNN view from the compact episode evidence bank."""
    cfg = config or CQHPTFeatureConfig()
    ref_raw = (bank.get("transition_refs") or {}).get(str(transition.transition_id), [0.0,0.0])
    ref = (_f(ref_raw[0]) if len(ref_raw)>0 else 0.0, _f(ref_raw[1]) if len(ref_raw)>1 else 0.0)
    source = list(bank.get("tokens") or [])
    globals_ = [t for t in source if int(t.get("type_id",-1)) == TOKEN_TYPE_TO_ID["route"]]
    local = [t for t in source if int(t.get("type_id",-1)) != TOKEN_TYPE_TO_ID["route"]]
    def d2(t):
        xy=t.get("abs_xy") or [0.0,0.0]
        return (_f(xy[0])-ref[0])**2+(_f(xy[1])-ref[1])**2
    # Type-aware quotas keep one modality from crowding out all others.
    selected: List[Mapping[str,Any]]=[]
    quotas={TOKEN_TYPE_TO_ID["path"]:cfg.max_path_tokens,TOKEN_TYPE_TO_ID["agent"]:cfg.max_agent_tokens,
            TOKEN_TYPE_TO_ID["pudo"]:cfg.max_pudo_tokens,TOKEN_TYPE_TO_ID["rule"]:cfg.max_pudo_tokens}
    bytype: Dict[int,List[Mapping[str,Any]]]={}
    for t in local: bytype.setdefault(int(t.get("type_id",0)),[]).append(t)
    for tid, vals in bytype.items():
        vals=sorted(vals,key=d2); selected.extend(vals[:quotas.get(tid,8)])
    selected=sorted(selected,key=d2)[:max(0,cfg.max_tokens-3)]
    out=[]
    for src in selected + globals_[:1]:
        t={k:(list(v) if isinstance(v,list) else v) for k,v in dict(src).items()}
        xy=t.get("abs_xy") or [0.0,0.0]
        rx=(_f(xy[0])-ref[0])/100.0; ry=(_f(xy[1])-ref[1])/100.0
        raw=list(t.get("raw") or [0.0]*RAW_TOKEN_DIM); raw[0]=rx; raw[1]=ry
        t["raw"]=raw; t["rel_xy"]=[rx,ry]; out.append(t)
    side_code={"left":-1.0,"right":1.0,"both":0.0}.get(str(vehicle.door_side),0.0)
    out.append(_token("vehicle",ref=ref,confidence=1.0,source="vehicle_interface",
                      dynamic1=_f(vehicle.dwell_time_s)/60.0,dynamic2=side_code,
                      binary1=bool(vehicle.ramp or vehicle.lift),binary2=bool(vehicle.low_floor or vehicle.kneeling)))
    out.append(_token("transition",ref=ref,length=max(0.0,_f(transition.cost)),confidence=_f(transition.map_confidence),
                      source="candidate_transition",dynamic1=max(0.0,min(1.0,_f(transition.availability))),
                      binary1=bool(transition.tests.legal_lifecycle),binary2=bool(transition.tests.dynamically_available)))
    return out[:cfg.max_tokens]
