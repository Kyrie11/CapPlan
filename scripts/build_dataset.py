#!/usr/bin/env python
from __future__ import annotations

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

import argparse
import subprocess
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from capplan.data.accessibility_layer import PreparedAccessibilityBuilder, SyntheticAccessibilityBuilder, attach_pudo_nodes_to_graph, write_accessibility_graph
from capplan.data.capability_contracts import load_contracts_from_profiles, sample_contracts_with_pairs
from capplan.data.label_oracle import IndependentLabelOracle
from capplan.data.nuplan_adapter import NuPlanAdapter
from capplan.data.pudo_interface_layer import PUDOGenerator, synthetic_vehicle_interface, vehicle_interface_profiles
from capplan.data.passenger_service_layer import (
    bind_bootstrap_service_request_to_graph,
    bind_service_request_to_graph,
    load_fleet_interfaces,
    load_service_requests_by_episode,
    service_request_to_trip_context,
)
from capplan.data.schemas import EntranceAnchor, Pose2D, PUDOAnchor, to_dict, transition_label_from_transition
from capplan.data.validate_dataset import validate_dataset
from capplan.planning.transition_generator import TransitionGenerator
from capplan.utils.serialization import dump_json, load_json, read_jsonl, write_jsonl


try:
    from tqdm import tqdm
except Exception:  # pragma: no cover - tqdm is an optional UX dependency
    def tqdm(iterable, **kwargs):
        return iterable


def _split_cli_path_list(values: List[str] | str | None) -> List[str]:
    if values is None:
        return []
    raw = values if isinstance(values, list) else [values]
    out: List[str] = []
    for item in raw:
        for piece in str(item).replace(",", "+").split("+"):
            piece = piece.strip()
            if piece:
                out.append(piece)
    return out


def _resolve_db_inputs(args: argparse.Namespace) -> List[str] | str | None:
    """Resolve user-friendly DB-set folder arguments for nuPlan builds.

    Users may pass either the original ``--nuplan_db_files`` argument or the new
    pair ``--nuplan_db_root ... --nuplan_db_dirs train_boston train_pittsburgh``.
    Relative folder names are resolved under ``nuplan_db_root`` when provided,
    otherwise under ``nuplan_data_root`` / ``nuplan_root``.  The adapter expands
    directories into concrete ``.db`` files.
    """
    tokens: List[str] = []
    root = Path(args.nuplan_db_root or args.nuplan_data_root or args.nuplan_root or ".")
    for token in _split_cli_path_list(args.nuplan_db_files):
        p = Path(token)
        tokens.append(str(p if p.is_absolute() else root / p))
    for token in _split_cli_path_list(args.nuplan_db_dirs):
        p = Path(token)
        tokens.append(str(p if p.is_absolute() else root / p))
    if tokens:
        return tokens
    return args.nuplan_db_files



def _assert_path(path: str | None, label: str) -> Path:
    if not path:
        raise RuntimeError(f"paper_mode requires {label}")
    p = Path(path)
    if not p.exists():
        raise RuntimeError(f"paper_mode requires existing {label}: {p}")
    return p


def _assert_paper_mode_config(args: argparse.Namespace) -> None:
    """Fail fast when a command could silently build a proxy dataset.

    Paper mode is intentionally stricter than schema validation: it rejects
    synthetic/proxy layers before any dataset rows are written.  Smoke mode keeps
    the old lightweight pipeline for CI and quick debugging.
    """
    if getattr(args, "source_policy", "bootstrap") == "paper" and not getattr(args, "paper_mode", False):
        raise RuntimeError("--source_policy paper requires --paper_mode")
    if not getattr(args, "paper_mode", False):
        return
    if args.scene_source != "nuplan":
        raise RuntimeError("paper_mode requires --scene_source nuplan")
    if args.accessibility_source in {"synthetic", "synthetic_local"}:
        raise RuntimeError("paper_mode rejects synthetic accessibility; use --accessibility_source prepared_jsonl/geojson/opensidewalks")
    if args.pudo_source in {"synthetic_overlay", "auto"}:
        raise RuntimeError("paper_mode requires audited PUDO evidence; use --pudo_source evidence_jsonl or nuplan_route with --pudo_evidence_jsonl")
    if args.service_layer_source == "synthetic_smoke":
        raise RuntimeError("paper_mode requires --service_layer_source real_jsonl or calibrated_od")
    _assert_path(args.accessibility_graph_dir, "--accessibility_graph_dir")
    _assert_path(args.pudo_evidence_jsonl, "--pudo_evidence_jsonl")
    _assert_path(args.service_requests_jsonl, "--service_requests_jsonl")
    _assert_path(args.capability_profiles_jsonl, "--capability_profiles_jsonl")
    _assert_path(args.fleet_jsonl, "--fleet_jsonl")


def _source_is_synthetic_or_proxy(value: Any) -> bool:
    s = str(value or "").lower()
    return s.startswith("synthetic") or "proxy" in s or s in {"toy", "mock"}


def _enforce_paper_episode_quality(args: argparse.Namespace, eid: str, graph: Any, origin: EntranceAnchor, destination: EntranceAnchor, pudo: List[PUDOAnchor]) -> None:
    if getattr(args, "source_policy", "bootstrap") == "paper" and not getattr(args, "paper_mode", False):
        raise RuntimeError("--source_policy paper requires --paper_mode")
    if not getattr(args, "paper_mode", False):
        return
    if getattr(args, "require_validated_georeference", False) and graph.metadata.get("georeference_validated") is False:
        raise RuntimeError(f"paper_mode requires validated georeference for {eid}; graph metadata georeference_validated=false")
    if args.reject_proxy_entrances and (_source_is_synthetic_or_proxy(origin.source) or _source_is_synthetic_or_proxy(destination.source)):
        raise RuntimeError(f"paper_mode rejects proxy/synthetic entrances for {eid}: {origin.source}, {destination.source}")
    if args.reject_synthetic_accessibility:
        bad_edges = [e.edge_id for e in graph.edges if _source_is_synthetic_or_proxy(e.source)]
        if bad_edges:
            raise RuntimeError(f"paper_mode rejects synthetic/proxy accessibility edges for {eid}; first examples: {bad_edges[:5]}")
        if _source_is_synthetic_or_proxy(graph.metadata.get("source")):
            raise RuntimeError(f"paper_mode rejects synthetic/proxy accessibility graph source for {eid}: {graph.metadata.get('source')}")
    if len(graph.nodes) < args.min_graph_nodes or len(graph.edges) < args.min_graph_edges:
        raise RuntimeError(f"paper_mode graph for {eid} is too small: {len(graph.nodes)} nodes/{len(graph.edges)} edges; required {args.min_graph_nodes}/{args.min_graph_edges}")
    if not pudo:
        raise RuntimeError(f"paper_mode requires audited PUDO candidates for {eid}")
    missing = {"curb_height_m": 0, "deployment_clearance_m": 0, "sidewalk_width_m": 0}
    for a in pudo:
        for k in list(missing):
            if getattr(a, k) is None:
                missing[k] += 1
        if _source_is_synthetic_or_proxy(a.source):
            raise RuntimeError(f"paper_mode rejects synthetic/proxy PUDO source for {eid}: {a.anchor_id} source={a.source}")
    n = max(1, len(pudo))
    for k, count in missing.items():
        rate = count / n
        if rate > args.max_core_pudo_missing_rate:
            raise RuntimeError(f"paper_mode PUDO core evidence missing rate too high for {eid}: {k}={rate:.3f} > {args.max_core_pudo_missing_rate:.3f}")


def _make_accessibility_builder(args: argparse.Namespace):
    if args.accessibility_source in {"synthetic", "synthetic_local"}:
        return SyntheticAccessibilityBuilder(), True
    if not args.accessibility_graph_dir:
        raise RuntimeError(
            f"--accessibility_source {args.accessibility_source} requires --accessibility_graph_dir with prepared node/edge JSONL files"
        )
    return PreparedAccessibilityBuilder(args.accessibility_graph_dir, source=args.accessibility_source), False


def _load_pudo_evidence(path: str | None) -> Dict[tuple[str | None, str], Dict[str, Any]]:
    """Load optional audited curbside/PUDO evidence overrides.

    Keys may be either (episode_id, anchor_id) or global (None, anchor_id).
    Supported fields include curb_height_m, sidewalk_width_m,
    deployment_clearance_m, legal_stop, side, lighting, shelter, map_confidence,
    dynamic_confidence, blockage_risk, and source.
    """
    if not path:
        return {}
    out: Dict[tuple[str | None, str], Dict[str, Any]] = {}
    for row in read_jsonl(path):
        anchor_id = row.get("anchor_id") or row.get("pudo_id")
        if not anchor_id:
            continue
        eid = row.get("episode_id")
        out[(str(eid) if eid else None, str(anchor_id))] = dict(row)
    return out


def _apply_pudo_evidence_overrides(anchors: List[PUDOAnchor], evidence: Dict[tuple[str | None, str], Dict[str, Any]]) -> List[PUDOAnchor]:
    if not evidence:
        return anchors
    fields = {
        "curb_height_m", "sidewalk_width_m", "deployment_clearance_m",
        "blockage_risk", "map_confidence", "dynamic_confidence",
        "lighting", "shelter", "legal_stop", "side", "legal_stop_source", "source",
    }
    updated: List[PUDOAnchor] = []
    for a in anchors:
        row = evidence.get((a.episode_id, a.anchor_id)) or evidence.get((None, a.anchor_id))
        if not row:
            updated.append(a)
            continue
        kwargs = {k: row[k] for k in fields if k in row}
        if kwargs:
            src = kwargs.get("source") or f"{a.source}+pudo_evidence_override"
            kwargs["source"] = src
            updated.append(replace(a, **kwargs))
        else:
            updated.append(a)
    return updated


def _as_bool_field(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    s = str(value).strip().lower()
    if s in {"true", "1", "yes", "y", "t", "legal", "allowed"}:
        return True
    if s in {"false", "0", "no", "n", "f", "illegal", "not_allowed", "disallowed", "none"}:
        return False
    return default


def _pose_from_evidence_row(row: Dict[str, Any], prefix: str = "curb") -> Pose2D:
    """Parse a curb/stop pose from an audited PUDO evidence row.

    Paper-mode ``--pudo_source evidence_jsonl`` should use audited PUDO
    candidates directly instead of regenerating route-lane proxies and hoping
    their IDs match evidence overrides.  This parser accepts common materialized
    schemas: ``curb_pose``/``stop_pose`` dictionaries, ``x``/``y`` or
    ``curb_x``/``curb_y`` scalars, and point/polyline ``geometry``.
    """
    pose_key = f"{prefix}_pose"
    if isinstance(row.get(pose_key), dict):
        d = row[pose_key]
        return Pose2D(float(d.get("x", 0.0)), float(d.get("y", 0.0)), float(d.get("heading", 0.0)), str(d.get("frame", row.get("frame", "map"))))
    x_key = f"{prefix}_x"
    y_key = f"{prefix}_y"
    if row.get(x_key) is not None and row.get(y_key) is not None:
        return Pose2D(float(row[x_key]), float(row[y_key]), float(row.get(f"{prefix}_heading", row.get("heading", 0.0)) or 0.0), str(row.get("frame", "map")))
    if prefix == "curb" and row.get("x") is not None and row.get("y") is not None:
        return Pose2D(float(row["x"]), float(row["y"]), float(row.get("heading", 0.0) or 0.0), str(row.get("frame", "map")))
    geom = row.get("geometry")
    if prefix == "curb" and isinstance(geom, list) and geom:
        first = geom[0]
        if isinstance(first, dict):
            return Pose2D(float(first.get("x", 0.0)), float(first.get("y", 0.0)), float(first.get("heading", row.get("heading", 0.0)) or 0.0), str(first.get("frame", row.get("frame", "map"))))
        if isinstance(first, (list, tuple)) and len(first) >= 2:
            return Pose2D(float(first[0]), float(first[1]), float(row.get("heading", 0.0) or 0.0), str(row.get("frame", "map")))
    raise RuntimeError(f"PUDO evidence row {row.get('anchor_id') or row.get('pudo_id')} is missing {prefix} pose/x/y geometry")


def _pudo_anchors_from_evidence_rows(evidence: Dict[tuple[str | None, str], Dict[str, Any]], episode_id: str) -> List[PUDOAnchor]:
    """Materialize audited PUDO candidates for one episode from evidence JSONL."""
    rows = [r for (eid, _), r in evidence.items() if eid == episode_id]
    anchors: List[PUDOAnchor] = []
    for row in rows:
        anchor_id = str(row.get("anchor_id") or row.get("pudo_id"))
        if not anchor_id:
            continue
        curb_pose = _pose_from_evidence_row(row, "curb")
        try:
            stop_pose = _pose_from_evidence_row(row, "stop")
        except RuntimeError:
            stop_pose = curb_pose
        source = str(row.get("source") or row.get("evidence_source") or "audited_pudo_evidence")
        anchors.append(PUDOAnchor(
            anchor_id=anchor_id,
            episode_id=episode_id,
            kind=str(row.get("kind") or row.get("pudo_kind") or "pickup_dropoff"),
            curb_pose=curb_pose,
            stop_pose=stop_pose,
            side=str(row.get("side", "unknown")),
            legal_stop=_as_bool_field(row.get("legal_stop", row.get("vehicle_stop_feasible", False)), default=False),
            legal_stop_source=str(row.get("legal_stop_source") or row.get("regulation_id") or row.get("curb_regulation_source") or source),
            roadblock_id=row.get("roadblock_id"),
            lane_id=row.get("lane_id"),
            lane_connector_id=row.get("lane_connector_id"),
            adjacent_ped_node_id=row.get("adjacent_ped_node_id") or row.get("ped_node_id"),
            curb_height_m=row.get("curb_height_m"),
            sidewalk_width_m=row.get("sidewalk_width_m"),
            deployment_clearance_m=row.get("deployment_clearance_m"),
            blockage_risk=float(row.get("blockage_risk", row.get("curb_occupancy", 0.0)) or 0.0),
            map_confidence=float(row.get("map_confidence", row.get("confidence", 1.0)) or 1.0),
            dynamic_confidence=float(row.get("dynamic_confidence", row.get("availability", 1.0)) or 1.0),
            lighting=row.get("lighting"),
            shelter=row.get("shelter"),
            timestamp_s=row.get("timestamp_s"),
            source=source,
        ))
    return anchors

def _git_commit() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[1], text=True, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return None


def _write_splits(out: Path, episode_ids: List[str]) -> None:
    split_dir = out / "splits"
    split_dir.mkdir(parents=True, exist_ok=True)
    n = len(episode_ids)
    if n == 0:
        train, val, test = [], [], []
    elif n == 1:
        train, val, test = episode_ids, episode_ids, episode_ids
    else:
        n_train = max(1, int(0.7 * n))
        n_val = max(1, int(0.15 * n)) if n >= 3 else 1
        train = episode_ids[:n_train]
        val = episode_ids[n_train:n_train + n_val] or episode_ids[:1]
        test = episode_ids[n_train + n_val:] or episode_ids[-1:]
    for name, ids in [("train", train), ("val", val), ("test", test)]:
        (split_dir / f"{name}_episodes.txt").write_text("\n".join(ids) + ("\n" if ids else ""), encoding="utf-8")



def _scene_service_entrance_poses(scene: Any) -> tuple[Pose2D, Pose2D, str]:
    """Return passenger service entrance proxies in the scene coordinate frame.

    Standard nuPlan planners receive the ego state, route roadblocks, and mission
    goal rather than passenger entrances.  CapPlan still needs service anchors for
    first/last-meter reasoning, so in nuPlan builds we use the initial ego pose
    and mission goal as map-frame proxy entrances unless a richer service-request
    overlay is provided later.
    """
    if getattr(scene, "source", None) == "nuplan":
        origin = scene.initial_ego_pose
        destination = scene.mission_goal
        if destination is None:
            length = float(scene.route_corridor.get("length_m", 100.0) or 100.0)
            destination = Pose2D(
                origin.x + length * __import__("math").cos(origin.heading),
                origin.y + length * __import__("math").sin(origin.heading),
                origin.heading,
                origin.frame,
            )
        return origin, destination, "nuplan_scene_proxy"
    return Pose2D(0.0, 0.0, 0.0, "local"), Pose2D(160.0, 24.0, 0.0, "local"), "synthetic_service_overlay"

def main() -> None:
    p = argparse.ArgumentParser(description="Build the passenger capability-aware CapPlan dataset.")
    p.add_argument("--scene_source", choices=["synthetic", "nuplan"], default="synthetic")
    p.add_argument("--nuplan_data_root", default=None)
    p.add_argument("--nuplan_map_root", default=None)
    p.add_argument("--nuplan_sensor_root", default=None)
    p.add_argument("--nuplan_db_files", default=None, help="nuPlan .db file, folder, glob, or comma/plus-separated list. Relative entries resolve under --nuplan_db_root or --nuplan_data_root.")
    p.add_argument("--nuplan_db_root", default=None, help="Root directory containing nuPlan DB-set folders such as train_boston, train_pittsburgh, val.")
    p.add_argument("--nuplan_db_dirs", nargs="*", default=None, help="One or more DB-set folder names/paths, e.g. train_boston train_pittsburgh or train_boston+train_pittsburgh.")
    p.add_argument("--nuplan_map_version", default=None)
    p.add_argument("--nuplan_map_names", default=None, help="Optional comma/plus-separated nuPlan map_name filter, e.g. us-ma-boston.")
    p.add_argument("--nuplan_scenario_types", default=None, help="Optional comma/plus-separated nuPlan scenario_type filter.")
    p.add_argument("--nuplan_log_names", default=None, help="Optional comma/plus-separated nuPlan log_name filter.")
    # Backward-compatible alias; mapped to nuplan_data_root only when provided.
    p.add_argument("--nuplan_root", default=None)
    p.add_argument("--split", default="mini")
    p.add_argument("--max_scenarios", type=int, default=4)
    p.add_argument("--output_dir", default="outputs/datasets/synthetic")
    p.add_argument("--paper_mode", action="store_true", help="Enable publication-grade data gates: no synthetic/proxy fallbacks, no missing core evidence, and real service/profile/fleet inputs required.")
    p.add_argument("--source_policy", choices=["bootstrap", "paper"], default="bootstrap", help="Dataset evidence policy recorded in the manifest. paper requires --paper_mode and complete audited evidence.")
    p.add_argument("--external_source_preflight_json", default=None, help="Optional preflight report from prepare_abilitybench_external.py copied into the dataset manifest for auditability.")
    p.add_argument("--require_validated_georeference", action="store_true", help="Paper-mode gate: fail if prepared graph metadata says georeference_validated=false.")
    p.add_argument("--service_layer_source", choices=["synthetic_smoke", "real_jsonl", "calibrated_od"], default="synthetic_smoke", help="Passenger-service request source. Paper mode rejects synthetic_smoke.")
    p.add_argument("--service_requests_jsonl", default=None, help="Real/calibrated service requests JSONL with request_id, episode_id, origin/destination entrance IDs, request time, profile, vehicle/fleet fields.")
    p.add_argument("--fleet_jsonl", default=None, help="Fleet vehicle/interface JSONL. Paper mode requires this instead of the fixed smoke vehicle set.")
    p.add_argument("--capability_profiles_jsonl", default=None, help="Capability profiles in JSONL/YAML form. Paper mode requires this; smoke mode samples archetypes.")
    p.add_argument("--reject_proxy_entrances", action="store_true", help="Fail if entrances are proxy/synthetic sources.")
    p.add_argument("--reject_synthetic_accessibility", action="store_true", help="Fail if accessibility graph nodes/edges have synthetic/proxy provenance.")
    p.add_argument("--min_graph_nodes", type=int, default=100)
    p.add_argument("--min_graph_edges", type=int, default=150)
    p.add_argument("--max_core_pudo_missing_rate", type=float, default=0.05)
    p.add_argument("--min_edge_positive_rate", type=float, default=0.10)
    p.add_argument("--min_skeleton_positive_rate", type=float, default=0.10)
    p.add_argument("--accessibility_source", choices=["synthetic_local", "synthetic", "prepared_jsonl", "geojson", "opensidewalks"], default="synthetic_local")
    p.add_argument("--accessibility_graph_dir", default=None, help="Directory containing prepared accessibility graph JSONL files: <episode_id>.nodes.jsonl/<episode_id>.edges.jsonl or shared nodes.jsonl/edges.jsonl.")
    p.add_argument("--pudo_evidence_jsonl", default=None, help="Optional JSONL with audited PUDO/curbside evidence overrides keyed by episode_id and anchor_id.")
    p.add_argument("--pudo_source", choices=["auto", "synthetic_overlay", "nuplan_route", "evidence_jsonl"], default="auto", help="PUDO generation policy. Paper mode should use evidence_jsonl or nuplan_route plus audited evidence overrides.")
    p.add_argument("--num_contracts_per_scene", type=int, default=2)
    p.add_argument("--num-workers", "--num_workers", dest="num_workers", type=int, default=0, help="Number of worker threads passed to the nuPlan scenario builder. Default 0 keeps the existing sequential behavior.")
    p.add_argument("--seed", type=int, default=13)
    p.add_argument("--strict", action="store_true")
    p.add_argument("--disable_tqdm", action="store_true", help="Disable preprocessing progress bars.")
    p.add_argument("--allow_bootstrap_service_nodes", action="store_true", help="Bootstrap-only: allow service OD anchors on non-entrance graph nodes. Paper mode always rejects this.")
    args = p.parse_args()
    if args.paper_mode and args.allow_bootstrap_service_nodes:
        raise RuntimeError("paper_mode rejects --allow_bootstrap_service_nodes")
    if args.paper_mode:
        # Reject proxy entries by default in paper mode even if the explicit flags were omitted.
        args.reject_proxy_entrances = True
        args.reject_synthetic_accessibility = True
    _assert_paper_mode_config(args)

    service_requests_by_episode = load_service_requests_by_episode(args.service_requests_jsonl) if args.service_requests_jsonl else {}
    fleet_by_episode = load_fleet_interfaces(args.fleet_jsonl) if args.fleet_jsonl else {}
    profile_contracts_by_episode = load_contracts_from_profiles(args.capability_profiles_jsonl, service_requests_by_episode) if args.capability_profiles_jsonl else {}

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "accessibility_graphs").mkdir(parents=True, exist_ok=True)

    resolved_db_files = _resolve_db_inputs(args)
    adapter = NuPlanAdapter(
        scene_source=args.scene_source,
        data_root=args.nuplan_data_root or args.nuplan_root,
        map_root=args.nuplan_map_root,
        sensor_root=args.nuplan_sensor_root,
        db_files=resolved_db_files,
        map_version=args.nuplan_map_version,
        split=args.split,
        seed=args.seed,
        num_workers=args.num_workers,
        scenario_types=args.nuplan_scenario_types,
        map_names=args.nuplan_map_names,
        log_names=args.nuplan_log_names,
    )
    acc_builder, synthetic_accessibility_mode = _make_accessibility_builder(args)
    pudo_evidence_overrides = _load_pudo_evidence(args.pudo_evidence_jsonl)
    pudo_gen = PUDOGenerator()
    trans_gen = TransitionGenerator()
    oracle = IndependentLabelOracle(max_depth=16)

    scenes: List[Dict[str, Any]] = []
    episodes: List[Dict[str, Any]] = []
    entrances: List[Dict[str, Any]] = []
    pudo_records: List[Dict[str, Any]] = []
    vehicle_records: List[Dict[str, Any]] = []
    profiles: List[Dict[str, Any]] = []
    contracts: List[Dict[str, Any]] = []
    req_groups: List[Dict[str, Any]] = []
    transitions: List[Dict[str, Any]] = []
    transition_labels: List[Dict[str, Any]] = []
    passenger_edge_labels: List[Dict[str, Any]] = []
    resource_labels: List[Dict[str, Any]] = []
    skeleton_labels: List[Dict[str, Any]] = []
    certificate_labels: List[Dict[str, Any]] = []
    counterfactual_pairs: List[Dict[str, Any]] = []
    service_request_records: List[Dict[str, Any]] = []

    scenario_iter = adapter.iter_scenarios(args.max_scenarios)
    if not args.disable_tqdm:
        scenario_iter = tqdm(scenario_iter, total=args.max_scenarios, desc=f"build {args.scene_source} dataset", unit="scenario")

    for record in scenario_iter:
        scene = record.scene
        ep = record.episode
        eid = ep.episode_id

        request = None
        if args.service_layer_source in {"real_jsonl", "calibrated_od"}:
            requests = service_requests_by_episode.get(eid, [])
            if not requests:
                raise RuntimeError(f"{args.service_layer_source} service layer has no request for episode {eid}")
            request = requests[0]
            service_request_records.append(dict(request))
            graph = acc_builder.build(eid, seed=ep.seed)
            try:
                origin, destination = bind_service_request_to_graph(request, graph)
            except ValueError:
                if args.paper_mode or not args.allow_bootstrap_service_nodes:
                    raise
                origin, destination = bind_bootstrap_service_request_to_graph(request, graph)
            ep.origin_anchor = origin.anchor_id
            ep.destination_anchor = destination.anchor_id
            ep.request_time_s = float(request.get("request_time_s", ep.request_time_s))
            if request.get("source"):
                ep.metadata = {**ep.metadata, "service_request_source": request.get("source"), "request_id": request.get("request_id"), "service_layer_source": args.service_layer_source}
        else:
            origin_pose, dest_pose, entrance_source = _scene_service_entrance_poses(scene)
            origin = EntranceAnchor("origin", eid, "origin_entrance", origin_pose, "origin", 0.98, entrance_source)
            destination = EntranceAnchor("destination", eid, "destination_entrance", dest_pose, "destination", 0.98, entrance_source)
            graph = acc_builder.build(eid, seed=ep.seed, origin=origin.pose, destination=destination.pose)
            ep.metadata = {**ep.metadata, "service_layer_source": args.service_layer_source}

        entrances.extend([to_dict(origin), to_dict(destination)])
        scenes.append(to_dict(scene))
        episodes.append(to_dict(ep))

        vehicles = fleet_by_episode.get(eid)
        if vehicles is None and "*" in fleet_by_episode:
            vehicles = [replace(v, episode_id=eid) for v in fleet_by_episode["*"]]
        if vehicles is None:
            if args.paper_mode:
                raise RuntimeError(f"paper_mode requires fleet_jsonl vehicles for episode {eid} or global episode_id='*'")
            vehicles = vehicle_interface_profiles(eid)
        vehicle_records.extend(to_dict(v) for v in vehicles)
        requested_vehicle_id = (request or {}).get("vehicle_id") or (request or {}).get("fleet_vehicle_id")
        if requested_vehicle_id:
            primary_vehicle = next((v for v in vehicles if v.vehicle_id == requested_vehicle_id), None)
            if primary_vehicle is None:
                raise RuntimeError(f"service request for {eid} requested vehicle {requested_vehicle_id}, but it is not present in fleet_jsonl")
        else:
            primary_vehicle = next((v for v in vehicles if v.vehicle_id == "wav_ramp_right"), vehicles[0])
        pudo_context = {
            "episode_id": eid,
            "seed": ep.seed,
            "metadata": ep.metadata,
            "scene_source": scene.source,
            "route_roadblock_ids": scene.route_roadblock_ids,
            "route_corridor": scene.route_corridor,
            "agent_history": scene.agent_history,
            "map_context": record.map_context,
        }
        if args.pudo_source == "evidence_jsonl":
            pudo = _pudo_anchors_from_evidence_rows(pudo_evidence_overrides, eid)
            if not pudo:
                raise RuntimeError(f"--pudo_source evidence_jsonl has no PUDO candidates for episode {eid}")
        else:
            pudo = pudo_gen.generate(
                pudo_context,
                graph,
                primary_vehicle,
                {
                    "n_candidates": 4,
                    "pudo_source": "synthetic_overlay" if args.pudo_source == "synthetic_overlay" else ("nuplan_route" if args.pudo_source == "nuplan_route" else "auto"),
                    "strict_nuplan_pudo": args.scene_source == "nuplan" and args.pudo_source == "nuplan_route",
                },
            )
            pudo = _apply_pudo_evidence_overrides(pudo, pudo_evidence_overrides)
        graph, pudo = attach_pudo_nodes_to_graph(graph, pudo)
        _enforce_paper_episode_quality(args, eid, graph, origin, destination, pudo)
        write_accessibility_graph(out, graph)
        pudo_records.extend(to_dict(x) for x in pudo)

        trip_context = {**to_dict(ep), "route_corridor": scene.route_corridor, "trip_modifiers": {}}
        if request:
            trip_context.update(service_request_to_trip_context(request))
        ts = trans_gen.generate(eid, graph, pudo, primary_vehicle, origin.anchor_id, destination.anchor_id, scene_context=trip_context)
        transitions.extend(to_dict(t) for t in ts)
        transition_labels.extend(to_dict(transition_label_from_transition(t)) for t in ts)
        for t in ts:
            for ev in t.resource_evidence:
                resource_labels.append({"episode_id": eid, "transition_id": t.transition_id, **to_dict(ev)})

        if args.capability_profiles_jsonl:
            episode_contracts = profile_contracts_by_episode.get(eid, [])
            if not episode_contracts:
                raise RuntimeError(f"no capability profile/contract available for episode {eid}")
            pairs = []
        else:
            episode_contracts, pairs = sample_contracts_with_pairs(eid, args.num_contracts_per_scene, seed=ep.seed)
        counterfactual_pairs.extend(to_dict(pair) for pair in pairs)
        for contract in episode_contracts:
            profiles.append(contract.profile)
            contracts.append(to_dict(contract))
            if args.service_layer_source == "synthetic_smoke":
                service_request_records.append({
                    "request_id": f"{contract.passenger_id}:request",
                    "episode_id": eid,
                    "origin_entrance_id": origin.anchor_id,
                    "destination_entrance_id": destination.anchor_id,
                    "request_time_s": ep.request_time_s,
                    "passenger_profile_id": str(contract.passenger_id).split(":")[-1],
                    "passenger_id": contract.passenger_id,
                    "vehicle_id": primary_vehicle.vehicle_id,
                    "source": "synthetic_smoke",
                })
            req_groups.extend(to_dict(g) | {"episode_id": eid, "passenger_id": contract.passenger_id} for g in contract.groups)
            labels = oracle.verify_episode(eid, contract, graph, pudo, primary_vehicle, ts)
            passenger_edge_labels.extend(to_dict(v) for v in labels["passenger_edge_labels"].values())
            skel = labels.get("skeleton")
            cert = labels.get("certificate")
            if skel:
                skeleton_labels.append(to_dict(skel))
            if cert:
                certificate_labels.append(to_dict(cert))

    write_jsonl(out / "scenes.jsonl", scenes)
    write_jsonl(out / "episodes.jsonl", episodes)
    write_jsonl(out / "entrances.jsonl", entrances)
    write_jsonl(out / "pudo_anchors.jsonl", pudo_records)
    write_jsonl(out / "vehicle_interfaces.jsonl", vehicle_records)
    write_jsonl(out / "capability_profiles.jsonl", profiles)
    write_jsonl(out / "capability_contracts.jsonl", contracts)
    write_jsonl(out / "requirement_groups.jsonl", req_groups)
    write_jsonl(out / "candidate_transitions.jsonl", transitions)
    write_jsonl(out / "transition_labels.jsonl", transition_labels)
    write_jsonl(out / "passenger_edge_labels.jsonl", passenger_edge_labels)
    write_jsonl(out / "resource_labels.jsonl", resource_labels)
    write_jsonl(out / "skeleton_labels.jsonl", skeleton_labels)
    write_jsonl(out / "certificate_labels.jsonl", certificate_labels)
    write_jsonl(out / "counterfactual_pairs.jsonl", counterfactual_pairs)
    write_jsonl(out / "service_requests.jsonl", service_request_records)
    _write_splits(out, [e["episode_id"] for e in episodes])

    preflight = load_json(args.external_source_preflight_json) if args.external_source_preflight_json else None
    manifest = {
        "dataset_name": out.name,
        "version": "0.1.0",
        "scene_source": args.scene_source,
        "nuplan": {"data_root": args.nuplan_data_root or args.nuplan_root, "map_root": args.nuplan_map_root, "sensor_root": args.nuplan_sensor_root, "db_files_requested": resolved_db_files, "db_files_expanded": adapter.db_files if args.scene_source == "nuplan" else [], "map_version": args.nuplan_map_version},
        "accessibility_source": args.accessibility_source, "accessibility_graph_dir": args.accessibility_graph_dir, "pudo_source": args.pudo_source, "pudo_evidence_jsonl": args.pudo_evidence_jsonl,
        "paper_mode": bool(args.paper_mode), "source_policy": args.source_policy, "publication_ready": bool(args.paper_mode and args.source_policy == "paper"), "external_source_preflight": preflight, "service_layer_source": args.service_layer_source, "service_requests_jsonl": args.service_requests_jsonl, "capability_profiles_jsonl": args.capability_profiles_jsonl, "fleet_jsonl": args.fleet_jsonl,
        "builder_git_commit": _git_commit(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "strict_mode": bool(args.strict),
        "num_episodes": len(episodes),
        "num_contracts": len(contracts),
        "num_transitions": len(transitions),
    }
    dump_json(out / "dataset_manifest.json", manifest)
    validation = validate_dataset(out, strict=args.strict)
    if args.paper_mode:
        pos = sum(1 for r in passenger_edge_labels if bool(to_dict(r).get("y_e_p")))
        rate = pos / max(1, len(passenger_edge_labels))
        skel_rate = len(skeleton_labels) / max(1, len(skeleton_labels) + len(certificate_labels))
        if rate < args.min_edge_positive_rate:
            raise RuntimeError(f"paper_mode passenger edge positive rate too low: {rate:.4f} < {args.min_edge_positive_rate:.4f}")
        if skel_rate < args.min_skeleton_positive_rate:
            raise RuntimeError(f"paper_mode skeleton positive rate too low: {skel_rate:.4f} < {args.min_skeleton_positive_rate:.4f}")
    dump_json(out / "validation_report.json", validation)
    print(f"Wrote dataset to {out} with {len(episodes)} episodes, {len(contracts)} contracts, {len(transitions)} transitions")
    print(f"Validation ok={validation['ok']} errors={len(validation['errors'])} warnings={len(validation['warnings'])}")


if __name__ == "__main__":
    main()
