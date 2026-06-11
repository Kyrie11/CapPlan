#!/usr/bin/env python
from __future__ import annotations

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

import argparse
import subprocess
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

from capplan.data.accessibility_layer import SyntheticAccessibilityBuilder, write_accessibility_graph
from capplan.data.capability_contracts import sample_contracts_with_pairs
from capplan.data.label_oracle import IndependentLabelOracle
from capplan.data.nuplan_adapter import NuPlanAdapter
from capplan.data.pudo_interface_layer import PUDOGenerator, synthetic_vehicle_interface, vehicle_interface_profiles
from capplan.data.schemas import EntranceAnchor, Pose2D, to_dict, transition_label_from_transition
from capplan.data.validate_dataset import validate_dataset
from capplan.planning.transition_generator import TransitionGenerator
from capplan.utils.serialization import dump_json, write_jsonl


def _git_commit() -> str | None:
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[1], text=True).strip()
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


def main() -> None:
    p = argparse.ArgumentParser(description="Build the passenger capability-aware CapPlan dataset.")
    p.add_argument("--scene_source", choices=["synthetic", "nuplan"], default="synthetic")
    p.add_argument("--nuplan_data_root", default=None)
    p.add_argument("--nuplan_map_root", default=None)
    p.add_argument("--nuplan_sensor_root", default=None)
    p.add_argument("--nuplan_db_files", default=None)
    p.add_argument("--nuplan_map_version", default=None)
    # Backward-compatible alias; mapped to nuplan_data_root only when provided.
    p.add_argument("--nuplan_root", default=None)
    p.add_argument("--split", default="mini")
    p.add_argument("--max_scenarios", type=int, default=4)
    p.add_argument("--output_dir", default="outputs/datasets/synthetic")
    p.add_argument("--accessibility_source", choices=["synthetic_local", "synthetic", "geojson", "opensidewalks"], default="synthetic_local")
    p.add_argument("--num_contracts_per_scene", type=int, default=2)
    p.add_argument("--seed", type=int, default=13)
    p.add_argument("--strict", action="store_true")
    args = p.parse_args()

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "accessibility_graphs").mkdir(parents=True, exist_ok=True)

    adapter = NuPlanAdapter(
        scene_source=args.scene_source,
        data_root=args.nuplan_data_root or args.nuplan_root,
        map_root=args.nuplan_map_root,
        sensor_root=args.nuplan_sensor_root,
        db_files=args.nuplan_db_files,
        map_version=args.nuplan_map_version,
        split=args.split,
        seed=args.seed,
    )
    acc_builder = SyntheticAccessibilityBuilder()
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

    for record in adapter.iter_scenarios(args.max_scenarios):
        scene = record.scene
        ep = record.episode
        scenes.append(to_dict(scene))
        episodes.append(to_dict(ep))
        eid = ep.episode_id

        origin_pose = Pose2D(0.0, 0.0, 0.0, "local")
        dest_pose = Pose2D(160.0, 24.0, 0.0, "local")
        origin = EntranceAnchor("origin", eid, "origin_entrance", origin_pose, "origin", 0.98, "synthetic_service_overlay")
        destination = EntranceAnchor("destination", eid, "destination_entrance", dest_pose, "destination", 0.98, "synthetic_service_overlay")
        entrances.extend([to_dict(origin), to_dict(destination)])

        if args.accessibility_source not in {"synthetic", "synthetic_local"}:
            raise RuntimeError("Only synthetic_local accessibility overlays are available in this repository smoke implementation; provide prepared GeoJSON through the builder API for real overlays")
        graph = acc_builder.build(eid, seed=ep.seed, origin=origin_pose, destination=dest_pose)
        write_accessibility_graph(out, graph)

        vehicles = vehicle_interface_profiles(eid)
        vehicle_records.extend(to_dict(v) for v in vehicles)
        primary_vehicle = next(v for v in vehicles if v.vehicle_id == "wav_ramp_right")
        pudo = pudo_gen.generate({"episode_id": eid, "seed": ep.seed, "metadata": ep.metadata}, graph, primary_vehicle)
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
        "nuplan": {"data_root": args.nuplan_data_root or args.nuplan_root, "map_root": args.nuplan_map_root, "sensor_root": args.nuplan_sensor_root, "db_files": args.nuplan_db_files, "map_version": args.nuplan_map_version},
        "accessibility_source": args.accessibility_source,
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
