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
from capplan.data.capability_contracts import sample_contracts_with_pairs
from capplan.data.label_oracle import IndependentLabelOracle
from capplan.data.nuplan_adapter import NuPlanAdapter
from capplan.data.pudo_interface_layer import PUDOGenerator, synthetic_vehicle_interface, vehicle_interface_profiles
from capplan.data.schemas import EntranceAnchor, Pose2D, PUDOAnchor, to_dict, transition_label_from_transition
from capplan.data.validate_dataset import validate_dataset
from capplan.planning.transition_generator import TransitionGenerator
from capplan.utils.serialization import dump_json, read_jsonl, write_jsonl


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
    # Backward-compatible alias; mapped to nuplan_data_root only when provided.
    p.add_argument("--nuplan_root", default=None)
    p.add_argument("--split", default="mini")
    p.add_argument("--max_scenarios", type=int, default=4)
    p.add_argument("--output_dir", default="outputs/datasets/synthetic")
    p.add_argument("--accessibility_source", choices=["synthetic_local", "synthetic", "prepared_jsonl", "geojson", "opensidewalks"], default="synthetic_local")
    p.add_argument("--accessibility_graph_dir", default=None, help="Directory containing prepared accessibility graph JSONL files: <episode_id>.nodes.jsonl/<episode_id>.edges.jsonl or shared nodes.jsonl/edges.jsonl.")
    p.add_argument("--pudo_evidence_jsonl", default=None, help="Optional JSONL with audited PUDO/curbside evidence overrides keyed by episode_id and anchor_id.")
    p.add_argument("--pudo_source", choices=["auto", "synthetic_overlay", "nuplan_route"], default="auto", help="PUDO generation policy. Use nuplan_route to require route/lane-derived anchors in nuPlan mode.")
    p.add_argument("--num_contracts_per_scene", type=int, default=2)
    p.add_argument("--num-workers", "--num_workers", dest="num_workers", type=int, default=0, help="Number of worker threads passed to the nuPlan scenario builder. Default 0 keeps the existing sequential behavior.")
    p.add_argument("--seed", type=int, default=13)
    p.add_argument("--strict", action="store_true")
    p.add_argument("--disable_tqdm", action="store_true", help="Disable preprocessing progress bars.")
    args = p.parse_args()

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

    scenario_iter = adapter.iter_scenarios(args.max_scenarios)
    if not args.disable_tqdm:
        scenario_iter = tqdm(scenario_iter, total=args.max_scenarios, desc=f"build {args.scene_source} dataset", unit="scenario")

    for record in scenario_iter:
        scene = record.scene
        ep = record.episode
        scenes.append(to_dict(scene))
        episodes.append(to_dict(ep))
        eid = ep.episode_id

        origin_pose, dest_pose, entrance_source = _scene_service_entrance_poses(scene)
        origin = EntranceAnchor("origin", eid, "origin_entrance", origin_pose, "origin", 0.98, entrance_source)
        destination = EntranceAnchor("destination", eid, "destination_entrance", dest_pose, "destination", 0.98, entrance_source)
        entrances.extend([to_dict(origin), to_dict(destination)])

        graph = acc_builder.build(eid, seed=ep.seed, origin=origin_pose, destination=dest_pose)

        vehicles = vehicle_interface_profiles(eid)
        vehicle_records.extend(to_dict(v) for v in vehicles)
        primary_vehicle = next(v for v in vehicles if v.vehicle_id == "wav_ramp_right")
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
        write_accessibility_graph(out, graph)
        pudo_records.extend(to_dict(x) for x in pudo)

        trip_context = {**to_dict(ep), "route_corridor": scene.route_corridor, "trip_modifiers": {}}
        ts = trans_gen.generate(eid, graph, pudo, primary_vehicle, origin.anchor_id, destination.anchor_id, scene_context=trip_context)
        transitions.extend(to_dict(t) for t in ts)
        transition_labels.extend(to_dict(transition_label_from_transition(t)) for t in ts)
        for t in ts:
            for ev in t.resource_evidence:
                resource_labels.append({"episode_id": eid, "transition_id": t.transition_id, **to_dict(ev)})

        episode_contracts, pairs = sample_contracts_with_pairs(eid, args.num_contracts_per_scene, seed=ep.seed)
        counterfactual_pairs.extend(to_dict(pair) for pair in pairs)
        for contract in episode_contracts:
            profiles.append(contract.profile)
            contracts.append(to_dict(contract))
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
    _write_splits(out, [e["episode_id"] for e in episodes])

    manifest = {
        "dataset_name": out.name,
        "version": "0.1.0",
        "scene_source": args.scene_source,
        "nuplan": {"data_root": args.nuplan_data_root or args.nuplan_root, "map_root": args.nuplan_map_root, "sensor_root": args.nuplan_sensor_root, "db_files_requested": resolved_db_files, "db_files_expanded": adapter.db_files if args.scene_source == "nuplan" else [], "map_version": args.nuplan_map_version},
        "accessibility_source": args.accessibility_source, "accessibility_graph_dir": args.accessibility_graph_dir, "pudo_source": args.pudo_source, "pudo_evidence_jsonl": args.pudo_evidence_jsonl,
        "builder_git_commit": _git_commit(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "strict_mode": bool(args.strict),
        "num_episodes": len(episodes),
        "num_contracts": len(contracts),
        "num_transitions": len(transitions),
    }
    dump_json(out / "dataset_manifest.json", manifest)
    validation = validate_dataset(out, strict=args.strict)
    dump_json(out / "validation_report.json", validation)
    print(f"Wrote dataset to {out} with {len(episodes)} episodes, {len(contracts)} contracts, {len(transitions)} transitions")
    print(f"Validation ok={validation['ok']} errors={len(validation['errors'])} warnings={len(validation['warnings'])}")


if __name__ == "__main__":
    main()
