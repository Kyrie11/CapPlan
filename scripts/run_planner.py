#!/usr/bin/env python
from __future__ import annotations

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

import argparse
from pathlib import Path

from capplan.data.accessibility_layer import load_accessibility_graph, synthetic_accessibility_graph
from capplan.data.capability_contracts import default_contract
from capplan.data.pudo_interface_layer import synthetic_pudo_anchors, synthetic_vehicle_interface
from capplan.data.schemas import contract_from_dict, pudo_from_dict, transition_from_dict, vehicle_from_dict, to_dict
from capplan.planning.planner import CapPlanPlanner, PlannerConfig
from capplan.utils.serialization import dump_json, read_jsonl


def add_flags(p: argparse.ArgumentParser) -> None:
    for flag in ["no_capability_compiler", "no_service_automaton", "no_casa_net_transitions", "no_typed_resource_ledger", "no_conservative_margins", "no_completion_value_guidance", "soft_only_capability"]:
        p.add_argument(f"--{flag}", action="store_true")


def main() -> None:
    p = argparse.ArgumentParser(description="Run CapPlan on one episode/passenger from a dataset or synthetic demo.")
    p.add_argument("--dataset_dir", default=None)
    p.add_argument("--episode_id", default="demo_episode")
    p.add_argument("--passenger_id", default=None)
    p.add_argument("--model_dir", default=None)
    p.add_argument("--output", default="outputs/plans/demo_plan.json")
    p.add_argument("--trajectory_mode", choices=["mock_strict", "nuplan_closed_loop"], default="mock_strict")
    p.add_argument("--casa_mode", choices=["heuristic_oracle_baseline", "learned"], default="heuristic_oracle_baseline")
    add_flags(p)
    args = p.parse_args()
    cfg_kwargs = {k: getattr(args, k) for k in PlannerConfig.__dataclass_fields__ if hasattr(args, k)}
    cfg_kwargs["trajectory_mode"] = args.trajectory_mode
    cfg_kwargs["casa_mode"] = args.casa_mode
    cfg = PlannerConfig(**cfg_kwargs)
    if args.dataset_dir:
        root = Path(args.dataset_dir)
        episodes = {e["episode_id"]: e for e in read_jsonl(root / "episodes.jsonl")}
        if args.episode_id not in episodes:
            raise KeyError(f"episode_id {args.episode_id} not found in {root}")
        contracts = [contract_from_dict(d) for d in read_jsonl(root / "capability_contracts.jsonl")]
        contract = next((c for c in contracts if c.passenger_id == (args.passenger_id or "")), None)
        if contract is None:
            contract = next(c for c in contracts if c.passenger_id.split(":p")[0] == args.episode_id)
        graph = load_accessibility_graph(root, args.episode_id)
        pudo = [pudo_from_dict(d) for d in read_jsonl(root / "pudo_anchors.jsonl") if d.get("episode_id") == args.episode_id]
        vehicles = [vehicle_from_dict(d) for d in read_jsonl(root / "vehicle_interfaces.jsonl") if d.get("episode_id") == args.episode_id]
        vehicle = next((v for v in vehicles if v.vehicle_id == "wav_ramp_right"), vehicles[0])
        transitions = [transition_from_dict(d) for d in read_jsonl(root / "candidate_transitions.jsonl") if d.get("episode_id") == args.episode_id]
        meta = episodes[args.episode_id]
        result = CapPlanPlanner(cfg).plan(args.episode_id, contract, graph, pudo, vehicle, transitions=transitions, trip_context=meta)
    else:
        graph = synthetic_accessibility_graph(args.episode_id)
        pudo = synthetic_pudo_anchors(args.episode_id, graph=graph)
        vehicle = synthetic_vehicle_interface(args.episode_id)
        contract = default_contract(args.passenger_id or f"{args.episode_id}:p0")
        result = CapPlanPlanner(cfg).plan(args.episode_id, contract, graph, pudo, vehicle)
    dump_json(args.output, to_dict(result))
    print(f"success={result.success}; wrote {args.output}")


if __name__ == "__main__":
    main()
