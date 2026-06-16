#!/usr/bin/env python
from __future__ import annotations

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

import argparse
from pathlib import Path

from capplan.evaluation.ablations import ABLATION_FLAGS, ablation_config
from capplan.evaluation.experiment_runner import write_csv
from capplan.evaluation.closed_loop import ClosedLoopRunner
from capplan.utils.serialization import load_json


def _bad_source(v: object) -> bool:
    s = str(v or "").lower()
    return (not s) or any(tok in s for tok in ["synthetic", "smoke", "mock", "proxy", "toy"])


def _validate_paper(dataset_dir: Path, trajectory_mode: str, casa_mode: str, casa_checkpoint: str | None, nuplan_sim_config: str | None) -> None:
    if trajectory_mode != "nuplan_closed_loop":
        raise RuntimeError("paper_mode ablations require --trajectory_mode nuplan_closed_loop; mock_strict is smoke-only")
    if casa_mode != "learned" or not casa_checkpoint:
        raise RuntimeError("paper_mode ablations require --casa_mode learned and --casa_checkpoint")
    if not nuplan_sim_config:
        raise RuntimeError("paper_mode ablations require --nuplan_sim_config")
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
    p.add_argument("--paper_mode", action="store_true")
    p.add_argument("--nuplan_sim_config", default=None)
    args = p.parse_args()
    dataset_dir = Path(args.dataset_dir)
    output_dir = Path(args.output_dir)
    if args.paper_mode:
        _validate_paper(dataset_dir, args.trajectory_mode, args.casa_mode, args.casa_checkpoint, args.nuplan_sim_config)
    variants = args.variants or list(ABLATION_FLAGS.keys())
    rows = {}
    for name in variants:
        cfg = ablation_config(name, trajectory_mode=args.trajectory_mode)
        cfg.casa_mode = args.casa_mode
        cfg.casa_checkpoint = args.casa_checkpoint
        res = ClosedLoopRunner(cfg).run_dataset(dataset_dir, output_dir / "ablations" / name)
        rows[name] = res["metrics"]
    write_csv(output_dir / "ablation_results.csv", rows)
    for k, v in rows.items():
        print(k, v)


if __name__ == "__main__":
    main()
