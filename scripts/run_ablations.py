#!/usr/bin/env python
from __future__ import annotations

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

import argparse
from pathlib import Path

from capplan.evaluation.ablations import ABLATION_FLAGS, MAIN_ABLATIONS, ablation_config
from capplan.evaluation.experiment_runner import write_csv
from capplan.evaluation.closed_loop import ClosedLoopRunner
from capplan.utils.serialization import load_json


def _bad_source(v: object) -> bool:
    s = str(v or "").lower()
    return (not s) or any(tok in s for tok in ["synthetic", "smoke", "mock", "proxy", "toy"])


def _validate_paper(dataset_dir: Path, trajectory_mode: str, casa_mode: str, casa_checkpoint: str | None, nuplan_sim_config: str | None, allow_posthoc_episode_vehicle_metrics: bool) -> None:
    if trajectory_mode != "nuplan_closed_loop":
        raise RuntimeError("paper_mode ablations require --trajectory_mode nuplan_closed_loop; mock_strict is smoke-only")
    if casa_mode != "learned" or not casa_checkpoint:
        raise RuntimeError("paper_mode ablations require --casa_mode learned and --casa_checkpoint")
    metrics_path = dataset_dir / "nuplan_vehicle_metrics.jsonl"
    if not metrics_path.exists():
        raise RuntimeError(
            "paper_mode ablations cannot execute nuPlan Hydra from this wrapper. Import real nuPlan metrics into "
            "dataset_dir/nuplan_vehicle_metrics.jsonl first; --nuplan_sim_config alone is not sufficient."
        )
    if not allow_posthoc_episode_vehicle_metrics:
        raise RuntimeError(
            "current ablations reuse imported episode-level nuPlan metrics and do not run method-specific integrated "
            "closed-loop simulations for each variant. Pass --allow_posthoc_episode_vehicle_metrics only when "
            "explicitly reporting this as post-hoc development analysis rather than final paper closed-loop evidence."
        )
    manifest = load_json(dataset_dir / "dataset_manifest.json")
    if manifest.get("scene_source") != "nuplan":
        raise RuntimeError(f"paper_mode ablations require scene_source=nuplan; got {manifest.get('scene_source')!r}")
    for key in ["accessibility_source", "pudo_source", "service_layer_source"]:
        if _bad_source(manifest.get(key)):
            raise RuntimeError(f"paper_mode ablations reject {key}={manifest.get(key)!r}")


def main() -> None:
    p = argparse.ArgumentParser(description="Run CapPlan ablations over a saved dataset.")
    p.add_argument("--dataset_dir", default="outputs/datasets/synthetic")
    p.add_argument("--output_dir", default="outputs/eval/ablations")
    p.add_argument("--trajectory_mode", choices=["mock_strict", "nuplan_closed_loop"], default="mock_strict")
    p.add_argument("--variants", nargs="*", choices=list(ABLATION_FLAGS.keys()), default=None)
    p.add_argument("--casa_mode", choices=["heuristic_oracle_baseline", "learned"], default="heuristic_oracle_baseline")
    p.add_argument("--casa_checkpoint", default=None)
    p.add_argument("--casa_device", default="auto", help="Device for learned CASA inference, e.g. cuda:0.")
    p.add_argument("--algorithm_version", default="V1")
    p.add_argument("--evidence_grounded_runtime", action="store_true", help="V2+ dual-channel hard-evidence semantics.")
    p.add_argument("--frontier_ranker_checkpoint", default=None, help="V3 Executable Capability Frontier ranker checkpoint.")
    p.add_argument("--frontier_ranker_device", default="auto", help="Device for V3 frontier ranker inference.")
    p.add_argument("--frontier_ranker_weight", type=float, default=0.35)
    p.add_argument("--episode_limit", type=int, default=None)
    p.add_argument("--episode_seed", type=int, default=13)
    p.add_argument("--progress", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--progress_update_interval", type=int, default=25)
    p.add_argument("--paper_mode", action="store_true")
    p.add_argument("--nuplan_sim_config", default=None, help="Optional provenance path for the external nuPlan simulation config; this wrapper does not execute Hydra.")
    p.add_argument("--allow_posthoc_episode_vehicle_metrics", action="store_true")
    args = p.parse_args()
    dataset_dir = Path(args.dataset_dir)
    output_dir = Path(args.output_dir)
    if args.paper_mode:
        _validate_paper(dataset_dir, args.trajectory_mode, args.casa_mode, args.casa_checkpoint, args.nuplan_sim_config, args.allow_posthoc_episode_vehicle_metrics)
    variants = args.variants or list(MAIN_ABLATIONS)
    from tqdm.auto import tqdm
    rows = {}
    shared_data = None
    variant_bar = tqdm(variants, desc="CapPlan ablations", unit="variant", dynamic_ncols=True, disable=not args.progress)
    for name in variant_bar:
        variant_bar.set_postfix({"variant": name}, refresh=False)
        cfg = ablation_config(name, trajectory_mode=args.trajectory_mode)
        cfg.casa_mode = args.casa_mode
        cfg.casa_checkpoint = args.casa_checkpoint
        cfg.casa_device = args.casa_device
        cfg.algorithm_version = args.algorithm_version
        cfg.frontier_ranker_checkpoint = args.frontier_ranker_checkpoint
        cfg.frontier_ranker_device = args.frontier_ranker_device
        cfg.frontier_ranker_weight = float(args.frontier_ranker_weight)
        if name != "no_evidence_grounding":
            cfg.evidence_grounded_runtime = bool(args.evidence_grounded_runtime)
        runner = ClosedLoopRunner(cfg)
        if shared_data is None:
            shared_data = runner._load_dataset(dataset_dir, episode_limit=args.episode_limit, episode_seed=args.episode_seed)
        res = runner.run_dataset(
            dataset_dir, output_dir / name,
            show_progress=args.progress, progress_update_interval=args.progress_update_interval,
            progress_desc=f"Ablation {name}",
            episode_limit=args.episode_limit, episode_seed=args.episode_seed, preloaded_data=shared_data,
        )
        rows[name] = res["metrics"]
    write_csv(output_dir / "ablation_results.csv", rows)
    for k, v in rows.items():
        print(k, v)


if __name__ == "__main__":
    main()
