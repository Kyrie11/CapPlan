"""Ablation configurations and runner."""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List

from capplan.evaluation.closed_loop import ClosedLoopRunner
from capplan.planning.planner import PlannerConfig

ABLATION_FLAGS = {
    "full": {},
    "no_capability_compiler": {"no_capability_compiler": True},
    "no_service_automaton": {"no_service_automaton": True},
    "no_casa_net_transitions": {"no_casa_net_transitions": True},
    "no_typed_resource_ledger": {"no_typed_resource_ledger": True},
    "no_conservative_margins": {"no_conservative_margins": True},
    "no_completion_value_guidance": {"no_completion_value_guidance": True},
    "soft_only_capability": {"soft_only_capability": True},
}


def ablation_config(name: str, trajectory_mode: str = "mock_strict") -> PlannerConfig:
    if name not in ABLATION_FLAGS:
        raise KeyError(name)
    return PlannerConfig(**ABLATION_FLAGS[name], trajectory_mode=trajectory_mode)


def run_ablations(dataset_dir: str | Path, output_dir: str | Path, variants: List[str] | None = None, trajectory_mode: str = "mock_strict") -> Dict[str, Dict]:
    output_dir = Path(output_dir)
    variants = variants or list(ABLATION_FLAGS.keys())
    results = {}
    for name in variants:
        cfg = ablation_config(name, trajectory_mode=trajectory_mode)
        runner = ClosedLoopRunner(cfg)
        res = runner.run_dataset(dataset_dir, output_dir / name)
        results[name] = res["metrics"]
    return results
