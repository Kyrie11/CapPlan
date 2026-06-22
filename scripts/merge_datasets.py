#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capplan.data.validate_dataset import validate_dataset
from capplan.utils.serialization import dump_json, read_jsonl, write_jsonl


JSONL_FILES = [
    "scenes.jsonl",
    "episodes.jsonl",
    "entrances.jsonl",
    "pudo_anchors.jsonl",
    "vehicle_interfaces.jsonl",
    "capability_profiles.jsonl",
    "capability_contracts.jsonl",
    "requirement_groups.jsonl",
    "candidate_transitions.jsonl",
    "transition_labels.jsonl",
    "passenger_edge_labels.jsonl",
    "resource_labels.jsonl",
    "skeleton_labels.jsonl",
    "certificate_labels.jsonl",
    "counterfactual_pairs.jsonl",
    "service_requests.jsonl",
]


def _dedupe(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for row in records:
        key = json.dumps(row, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _episode_ids(dataset_dir: Path) -> List[str]:
    return [str(r["episode_id"]) for r in read_jsonl(dataset_dir / "episodes.jsonl") if r.get("episode_id")]


def merge_datasets(input_dirs: List[Path], output_dir: Path, strict: bool = False) -> Dict[str, Any]:
    if not input_dirs:
        raise RuntimeError("at least one input dataset is required")
    for d in input_dirs:
        if not (d / "dataset_manifest.json").exists():
            raise RuntimeError(f"input is not a CapPlan dataset directory: {d}")

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "accessibility_graphs").mkdir(parents=True, exist_ok=True)

    for name in JSONL_FILES:
        rows: List[Dict[str, Any]] = []
        for d in input_dirs:
            rows.extend(read_jsonl(d / name))
        write_jsonl(output_dir / name, _dedupe(rows))

    for d in input_dirs:
        graph_dir = d / "accessibility_graphs"
        if not graph_dir.exists():
            continue
        for f in graph_dir.glob("*"):
            if f.is_file():
                dst = output_dir / "accessibility_graphs" / f.name
                if not dst.exists():
                    shutil.copy2(f, dst)

    split_dir = output_dir / "splits"
    split_dir.mkdir(parents=True, exist_ok=True)
    all_ids: List[str] = []
    for d in input_dirs:
        all_ids.extend(_episode_ids(d))
    all_ids = list(dict.fromkeys(all_ids))
    for split_name in ["train", "val", "test"]:
        merged: List[str] = []
        for d in input_dirs:
            f = d / "splits" / f"{split_name}_episodes.txt"
            if f.exists():
                merged.extend(x.strip() for x in f.read_text(encoding="utf-8").splitlines() if x.strip())
        if not merged and split_name == "train":
            merged = all_ids
        if not merged and all_ids:
            merged = all_ids[-max(1, len(all_ids) // 10):]
        merged = list(dict.fromkeys(merged))
        (split_dir / f"{split_name}_episodes.txt").write_text("\n".join(merged) + ("\n" if merged else ""), encoding="utf-8")

    manifests = []
    for d in input_dirs:
        try:
            manifests.append(json.loads((d / "dataset_manifest.json").read_text(encoding="utf-8")))
        except Exception:
            manifests.append({"dataset_dir": str(d), "manifest_read_error": True})
    manifest = {
        "dataset_name": output_dir.name,
        "mode": "merged_capplan_dataset",
        "input_dirs": [str(d) for d in input_dirs],
        "input_manifests": manifests,
        "num_episodes": len(read_jsonl(output_dir / "episodes.jsonl")),
        "num_contracts": len(read_jsonl(output_dir / "capability_contracts.jsonl")),
        "num_transitions": len(read_jsonl(output_dir / "candidate_transitions.jsonl")),
    }
    dump_json(output_dir / "dataset_manifest.json", manifest)
    validation = validate_dataset(output_dir, strict=strict)
    dump_json(output_dir / "validation_report.json", validation)
    return {"output_dir": str(output_dir), "validation_ok": validation["ok"], **manifest}


def main() -> None:
    p = argparse.ArgumentParser(description="Merge per-city CapPlan/AbilityBench datasets into one canonical dataset directory.")
    p.add_argument("--input_dirs", nargs="+", required=True)
    p.add_argument("--output_dir", required=True)
    p.add_argument("--strict", action="store_true")
    args = p.parse_args()
    report = merge_datasets([Path(x) for x in args.input_dirs], Path(args.output_dir), strict=args.strict)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
