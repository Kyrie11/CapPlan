"""Experiment orchestration utilities."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict

from capplan.evaluation.ablations import run_ablations
from capplan.evaluation.closed_loop import ClosedLoopRunner


def write_csv(path: str | Path, rows: Dict[str, Dict[str, float]]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = sorted({k for row in rows.values() for k in row})
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["method"] + keys)
        w.writeheader()
        for name, row in rows.items():
            w.writerow({"method": name, **row})


def run_main(dataset_dir: str | Path, output_dir: str | Path, trajectory_mode: str = "mock_strict") -> Dict[str, float]:
    res = ClosedLoopRunner(trajectory_mode=trajectory_mode).run_dataset(dataset_dir, Path(output_dir) / "main")
    write_csv(Path(output_dir) / "main_results.csv", {"CapPlan": res["metrics"]})
    return res["metrics"]


def run_ablation_table(dataset_dir: str | Path, output_dir: str | Path, trajectory_mode: str = "mock_strict") -> Dict[str, Dict[str, float]]:
    rows = run_ablations(dataset_dir, Path(output_dir) / "ablations", trajectory_mode=trajectory_mode)
    write_csv(Path(output_dir) / "ablation_results.csv", rows)
    return rows
