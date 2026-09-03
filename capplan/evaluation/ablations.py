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
    # Engineering diagnostics (not paper main-table ablations): isolate learned
    # CASA heads while keeping the rest of the V1 planner unchanged.
    "no_learned_demand": {"no_learned_demand": True},
    "no_learned_uncertainty": {"no_learned_uncertainty": True},
    # Factorial control: saved symbolic demand mean + saved symbolic sigma.  This
    # is intentionally distinct from ``no_learned_demand`` after reviewfix12 so
    # mean and uncertainty contributions can be identified without leakage.
    "no_learned_demand_uncertainty": {"no_learned_demand": True, "no_learned_uncertainty": True},
    "no_learned_availability": {"no_learned_availability": True},
    # V2 mechanism ablations.
    "no_evidence_grounding": {"evidence_grounded_runtime": False},
    "no_learned_feasibility_guidance": {"no_learned_feasibility_guidance": True},
    # V3 mechanism controls.
    "no_frontier_ranker": {"no_frontier_ranker": True},
    "v2_reference_runtime": {"v2_reference_runtime": True, "no_frontier_ranker": True, "no_continuation_envelope": True, "no_viability_kernel": True},
    # V4 mechanism controls.
    "no_continuation_envelope": {"no_continuation_envelope": True},
    "no_continuation_pruning": {"no_continuation_pruning": True},
    "no_continuation_priority": {"no_continuation_priority": True},
    # V5 proof-carrying capability viability controls.
    "no_viability_kernel": {"no_viability_kernel": True},
    "no_typed_viability": {"no_typed_viability": True},
    "generic_viability_certificates": {"generic_viability_certificates": True},
    # V6 weakest-precondition antichain / proof-envelope controls.
    "v5_reference_runtime": {"v5_reference_runtime": True},
    "no_precondition_antichain": {"no_precondition_antichain": True},
    "no_viability_proof_envelope": {"no_viability_proof_envelope": True},
    # V7 direct asymmetric dual-kernel controls.
    "v6_reference_runtime": {"v6_reference_runtime": True},
    "no_rejection_kernel": {"no_rejection_kernel": True},
}

MAIN_ABLATIONS = [
    "full",
    "no_capability_compiler",
    "no_service_automaton",
    "no_casa_net_transitions",
    "no_typed_resource_ledger",
    "no_conservative_margins",
    "no_completion_value_guidance",
    "soft_only_capability",
]

DIAGNOSTIC_ABLATIONS = [
    "no_learned_demand",
    "no_learned_uncertainty",
    "no_learned_demand_uncertainty",
    "no_learned_availability",
]


def ablation_config(name: str, trajectory_mode: str = "mock_strict") -> PlannerConfig:
    if name not in ABLATION_FLAGS:
        raise KeyError(name)
    return PlannerConfig(**ABLATION_FLAGS[name], trajectory_mode=trajectory_mode)


def run_ablations(dataset_dir: str | Path, output_dir: str | Path, variants: List[str] | None = None, trajectory_mode: str = "mock_strict") -> Dict[str, Dict]:
    output_dir = Path(output_dir)
    variants = variants or list(MAIN_ABLATIONS)
    results = {}
    for name in variants:
        cfg = ablation_config(name, trajectory_mode=trajectory_mode)
        runner = ClosedLoopRunner(cfg)
        res = runner.run_dataset(dataset_dir, output_dir / name)
        results[name] = res["metrics"]
    return results
