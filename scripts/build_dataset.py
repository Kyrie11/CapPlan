#!/usr/bin/env python
from __future__ import annotations

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

import argparse
from dataclasses import asdict
from pathlib import Path

from capplan.data.accessibility_layer import synthetic_accessibility_graph
from capplan.data.capability_contracts import sample_contracts
from capplan.data.label_oracle import LabelOracle
from capplan.data.nuplan_adapter import NuPlanAdapter
from capplan.data.pudo_interface_layer import synthetic_pudo_anchors, synthetic_vehicle_interface
from capplan.planning.transition_generator import TransitionGenerator
from capplan.utils.serialization import write_jsonl


def main() -> None:
    p = argparse.ArgumentParser(description="Build AbilityBench-AV style passenger-complete benchmark.")
    p.add_argument("--nuplan_root", default=None)
    p.add_argument("--split", default="mini")
    p.add_argument("--max_scenarios", type=int, default=4)
    p.add_argument("--output_dir", default="outputs/datasets/synthetic")
    p.add_argument("--accessibility_source", choices=["synthetic", "osm", "file"], default="synthetic")
    p.add_argument("--num_contracts_per_scene", type=int, default=2)
    p.add_argument("--seed", type=int, default=13)
    args = p.parse_args()

    out = Path(args.output_dir)
    (out / "accessibility_graphs").mkdir(parents=True, exist_ok=True)
    adapter = NuPlanAdapter(args.nuplan_root, split=args.split, seed=args.seed)
    gen = TransitionGenerator()
    oracle = LabelOracle()

    episodes = []
    pudo_records = []
    vehicle_records = []
    contracts = []
    transitions = []
    resource_labels = []
    skeleton_labels = []
    certificate_labels = []

    for record in adapter.iter_scenarios(args.max_scenarios):
        meta = asdict(record.episode)
        episodes.append(meta)
        eid = record.episode.episode_id
        graph = synthetic_accessibility_graph(eid, seed=record.episode.seed)
        write_jsonl(out / "accessibility_graphs" / f"{eid}.jsonl", [asdict(graph)])
        pudo = synthetic_pudo_anchors(eid, seed=record.episode.seed)
        vehicle = synthetic_vehicle_interface(eid)
        pudo_records.extend(asdict(x) for x in pudo)
        vehicle_records.append(asdict(vehicle))
        ts = gen.generate(eid, graph, pudo, vehicle)
        transitions.extend(asdict(t) for t in ts)
        for t in ts:
            for ev in t.resource_evidence:
                resource_labels.append({"episode_id": eid, "transition_id": t.transition_id, **asdict(ev)})
        for contract in sample_contracts(eid, args.num_contracts_per_scene, seed=record.episode.seed):
            contracts.append(asdict(contract))
            labels = oracle.verify_episode(eid, contract, graph, pudo, vehicle, ts)
            skel = labels.get("skeleton")
            cert = labels.get("certificate")
            if skel:
                skeleton_labels.append(asdict(skel))
            if cert:
                certificate_labels.append(asdict(cert))

    write_jsonl(out / "episodes.jsonl", episodes)
    write_jsonl(out / "pudo_anchors.jsonl", pudo_records)
    write_jsonl(out / "vehicle_interfaces.jsonl", vehicle_records)
    write_jsonl(out / "capability_contracts.jsonl", contracts)
    write_jsonl(out / "candidate_transitions.jsonl", transitions)
    write_jsonl(out / "resource_labels.jsonl", resource_labels)
    write_jsonl(out / "skeleton_labels.jsonl", skeleton_labels)
    write_jsonl(out / "certificate_labels.jsonl", certificate_labels)
    print(f"Wrote dataset to {out} with {len(episodes)} episodes, {len(contracts)} contracts, {len(transitions)} transitions")


if __name__ == "__main__":
    main()
