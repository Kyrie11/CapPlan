#!/usr/bin/env python
from __future__ import annotations

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

import argparse
from pathlib import Path

from capplan.evaluation.ablations import ABLATION_FLAGS, ablation_config
from capplan.evaluation.closed_loop import ClosedLoopRunner
from capplan.planning.planner import PlannerConfig
from capplan.utils.serialization import dump_json, load_json, read_jsonl
from scripts.import_nuplan_vehicle_metrics import import_metrics

# Publication guard: this wrapper does not run a method-specific integrated nuPlan simulation;
# final paper vehicle metrics must come from an external/integrated runner and be explicitly tagged.


def _fail_paper_if_mock(args: argparse.Namespace) -> None:
    if args.paper_mode and args.trajectory_mode != "nuplan_closed_loop":
        raise RuntimeError("paper_mode closed-loop evaluation requires --trajectory_mode nuplan_closed_loop; mock_strict is smoke-only")
    if args.paper_mode:
        dataset_dir = Path(args.dataset_dir)
        metrics_path = dataset_dir / "nuplan_vehicle_metrics.jsonl"
        if not metrics_path.exists():
            raise RuntimeError(
                "paper_mode cannot execute nuPlan Hydra from this wrapper. Import real nuPlan closed-loop metrics first "
                "with --import_nuplan_metrics_from (or materialize dataset_dir/nuplan_vehicle_metrics.jsonl). "
                "--nuplan_sim_config is metadata only and is not sufficient."
            )
        rows = read_jsonl(metrics_path)
        integrated = bool(rows) and all(bool(r.get("capplan_method_specific_closed_loop", False)) for r in rows)
        if not integrated:
            raise RuntimeError(
                "paper_mode requires method-specific integrated nuPlan closed-loop metrics tagged "
                "capplan_method_specific_closed_loop=true for every imported episode. The current episode-level post-hoc metrics "
                "are valid for development/TSPIR analysis but cannot support the paper's final vehicle closed-loop claim. "
                "Run without --paper_mode for explicitly labeled post-hoc analysis."
            )
    if args.paper_mode and args.casa_mode != "learned":
        raise RuntimeError("paper_mode closed-loop evaluation requires --casa_mode learned with a trained CASA checkpoint")
    if args.paper_mode and not args.casa_checkpoint:
        raise RuntimeError("paper_mode closed-loop evaluation requires --casa_checkpoint")


def _validate_dataset_manifest(dataset_dir: Path, paper_mode: bool) -> None:
    manifest_path = dataset_dir / "dataset_manifest.json"
    if not manifest_path.exists():
        raise RuntimeError(f"dataset manifest is missing: {manifest_path}")
    manifest = load_json(manifest_path)
    if not paper_mode:
        return
    if manifest.get("scene_source") != "nuplan":
        raise RuntimeError(f"paper_mode evaluation requires a nuPlan dataset; got scene_source={manifest.get('scene_source')!r}")
    bad = {"synthetic", "synthetic_local", "synthetic_smoke", "mock", "mock_strict", "proxy", "toy"}
    for key in ["accessibility_source", "pudo_source", "service_layer_source"]:
        val = str(manifest.get(key, "")).lower()
        if not val or any(tok in val for tok in bad):
            raise RuntimeError(f"paper_mode evaluation rejects {key}={manifest.get(key)!r}")


def main() -> None:
    p = argparse.ArgumentParser(description="Run CapPlan closed-loop/strict mock evaluation over a saved dataset.")
    p.add_argument("--dataset_dir", default="outputs/datasets/synthetic")
    p.add_argument("--output_dir", default="outputs/eval/closed_loop")
    p.add_argument("--planner", choices=["capplan"], default="capplan")
    p.add_argument("--ablation", choices=list(ABLATION_FLAGS.keys()), default="full")
    p.add_argument("--trajectory_mode", choices=["mock_strict", "nuplan_closed_loop"], default="mock_strict")
    p.add_argument("--casa_mode", choices=["heuristic_oracle_baseline", "learned"], default="heuristic_oracle_baseline")
    p.add_argument("--casa_checkpoint", default=None, help="Checkpoint produced by scripts.train_casa; required for a meaningful learned CASA run.")
    p.add_argument("--paper_mode", action="store_true", help="Fail if the run would use smoke/mock/proxy components.")
    p.add_argument("--nuplan_sim_config", default=None, help="Optional provenance path for the external nuPlan simulation configuration. This wrapper does not execute Hydra itself.")
    p.add_argument("--allow_posthoc_episode_vehicle_metrics", action="store_true", help="Deprecated compatibility flag. Post-hoc episode vehicle metrics are development-only and no longer override the paper_mode integrated-simulation guard.")
    p.add_argument("--vehicle_metrics", default=None, help="Optional output JSON path for vehicle-only metrics copied from aggregate metrics.")
    p.add_argument("--passenger_metrics", default=None, help="Optional output JSON path for passenger-complete metrics copied from aggregate metrics.")
    p.add_argument("--import_nuplan_metrics_from", default=None, help="Optional nuPlan metrics file/dir to import into dataset_dir/nuplan_vehicle_metrics.jsonl before evaluation.")
    args = p.parse_args()

    dataset_dir = Path(args.dataset_dir)
    if args.import_nuplan_metrics_from:
        import_metrics(dataset_dir, args.import_nuplan_metrics_from, dataset_dir / "nuplan_vehicle_metrics.jsonl", Path(args.output_dir) / "nuplan_vehicle_metrics_import_report.json")
    _fail_paper_if_mock(args)
    _validate_dataset_manifest(dataset_dir, args.paper_mode)

    cfg: PlannerConfig
    if args.ablation == "full":
        cfg = PlannerConfig(trajectory_mode=args.trajectory_mode, casa_mode=args.casa_mode, casa_checkpoint=args.casa_checkpoint)
    else:
        cfg = ablation_config(args.ablation, trajectory_mode=args.trajectory_mode)
        cfg.casa_mode = args.casa_mode
        cfg.casa_checkpoint = args.casa_checkpoint
    res = ClosedLoopRunner(cfg).run_dataset(dataset_dir, args.output_dir)
    metrics = res["metrics"]
    out_dir = Path(args.output_dir)
    dump_json(out_dir / "run_config.json", {**vars(args), "planner_config": cfg.__dict__})
    if args.vehicle_metrics:
        vehicle_keys = ["CR", "RC", "TRV", "TT", "DR"] + sorted(k for k in metrics if k.startswith("nuplan::"))
        vehicle_subset = {k: metrics.get(k) for k in vehicle_keys if k in metrics}
        dump_json(args.vehicle_metrics, vehicle_subset)
    if args.passenger_metrics:
        passenger_keys = [
            "PCR", "TSPIR", "PAR", "CVR", "CVR_all_evaluated", "CSM", "FLF", "BAF",
            "MER", "MVR", "SBR", "IR", "DF", "DF_phase_accuracy", "DF_resource_macro_f1",
            "DF_source_macro_f1", "SME", "CRsp", "ECA", "ECA_evaluable_count",
            "TSBS_expansions_mean", "TSBS_expansions_p95", "PlannerLatency_ms_mean", "PlannerLatency_ms_p95",
        ]
        passenger_keys += [k for k in metrics if k.startswith("CRsp_axis::")]
        passenger_subset = {k: metrics.get(k) for k in passenger_keys if k in metrics}
        dump_json(args.passenger_metrics, passenger_subset)
    print(metrics)


if __name__ == "__main__":
    main()
