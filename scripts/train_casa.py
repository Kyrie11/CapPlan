#!/usr/bin/env python
from __future__ import annotations

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

import argparse
from pathlib import Path
from capplan.utils.serialization import dump_json


def main() -> None:
    p = argparse.ArgumentParser(description="Train or calibrate CASA-Net predictors. The default command fits a deterministic baseline manifest.")
    p.add_argument("--dataset_dir", default="outputs/datasets/synthetic")
    p.add_argument("--output_dir", default="outputs/checkpoints/casa_baseline")
    p.add_argument("--train_phase_predictor", action="store_true")
    p.add_argument("--train_transition_predictor", action="store_true")
    p.add_argument("--train_resource_predictor", action="store_true")
    p.add_argument("--calibrate_uncertainty", action="store_true")
    p.add_argument("--train_completion_value", action="store_true")
    args = p.parse_args()
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    manifest = {
        "dataset_dir": args.dataset_dir,
        "mode": "heuristic_baseline",
        "trained_components": {
            "phase": args.train_phase_predictor,
            "transition": args.train_transition_predictor,
            "resource": args.train_resource_predictor,
            "uncertainty": args.calibrate_uncertainty,
            "completion_value": args.train_completion_value,
        },
        "note": "The interface is CASA-Net compatible; replace with a torch module by preserving CASAInput/CASAOutput.",
    }
    dump_json(out / "manifest.json", manifest)
    print(f"wrote {out / 'manifest.json'}")

if __name__ == "__main__":
    main()
