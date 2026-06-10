#!/usr/bin/env python
from __future__ import annotations

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

import argparse
from dataclasses import asdict
from pathlib import Path

from capplan.data.accessibility_layer import synthetic_accessibility_graph
from capplan.data.capability_contracts import default_contract
from capplan.data.pudo_interface_layer import synthetic_pudo_anchors, synthetic_vehicle_interface
from capplan.planning.planner import CapPlanPlanner, PlannerConfig
from capplan.utils.serialization import dump_json


def add_flags(p: argparse.ArgumentParser) -> None:
    for flag in ["no_capability_compiler", "no_service_automaton", "no_casa_net_transitions", "no_typed_resource_ledger", "no_conservative_margins", "no_completion_value_guidance", "soft_only_capability"]:
        p.add_argument(f"--{flag}", action="store_true")


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--episode_id", default="demo_episode")
    p.add_argument("--output", default="outputs/plans/demo_plan.json")
    add_flags(p)
    args = p.parse_args()
    cfg = PlannerConfig(**{k: getattr(args, k) for k in PlannerConfig.__dataclass_fields__ if hasattr(args, k)})
    graph = synthetic_accessibility_graph(args.episode_id)
    pudo = synthetic_pudo_anchors(args.episode_id)
    vehicle = synthetic_vehicle_interface(args.episode_id)
    contract = default_contract(f"{args.episode_id}:p0")
    result = CapPlanPlanner(cfg).plan(args.episode_id, contract, graph, pudo, vehicle)
    dump_json(args.output, asdict(result))
    print(f"success={result.success}; wrote {args.output}")

if __name__ == "__main__":
    main()
