#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import os
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


def _link_or_copy(src: Path, dst: Path) -> str:
    """Reuse immutable graph files without duplicating tens of GB of JSONL."""
    try:
        os.link(src, dst)
        return "hardlink"
    except OSError:
        shutil.copy2(src, dst)
        return "copy"


def _manifest_leaf_values(manifest: Dict[str, Any], key: str) -> List[Any]:
    """Collect a semantic manifest field through nested merged datasets.

    Split-level city merges wrap the original manifests under ``input_manifests``.
    A second merge (train+val+test) used to lose ``scene_source`` and the other
    provenance/source-policy fields, which made paper-safe evaluators reject an
    otherwise valid nuPlan dataset.
    """
    vals: List[Any] = []
    if key in manifest and manifest.get(key) is not None:
        vals.append(manifest.get(key))
    for child in manifest.get("input_manifests") or []:
        if isinstance(child, dict):
            vals.extend(_manifest_leaf_values(child, key))
    return vals


def _consensus_manifest_field(manifests: List[Dict[str, Any]], key: str) -> Any | None:
    vals: List[Any] = []
    for manifest in manifests:
        vals.extend(_manifest_leaf_values(manifest, key))
    canonical: Dict[str, Any] = {}
    for v in vals:
        token = json.dumps(v, sort_keys=True, default=str)
        canonical[token] = v
    return next(iter(canonical.values())) if len(canonical) == 1 else None


def merge_datasets(input_dirs: List[Path], output_dir: Path, strict: bool = False, clean_output: bool = False) -> Dict[str, Any]:
    if not input_dirs:
        raise RuntimeError("at least one input dataset is required")
    for d in input_dirs:
        if not (d / "dataset_manifest.json").exists():
            raise RuntimeError(f"input is not a CapPlan dataset directory: {d}")

    if clean_output and output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "accessibility_graphs").mkdir(parents=True, exist_ok=True)

    for name in JSONL_FILES:
        rows: List[Dict[str, Any]] = []
        for d in input_dirs:
            rows.extend(read_jsonl(d / name))
        write_jsonl(output_dir / name, _dedupe(rows))

    graph_storage = {"hardlink": 0, "copy": 0}
    for d in input_dirs:
        graph_dir = d / "accessibility_graphs"
        if not graph_dir.exists():
            continue
        for f in graph_dir.glob("*"):
            if f.is_file():
                dst = output_dir / "accessibility_graphs" / f.name
                if not dst.exists():
                    graph_storage[_link_or_copy(f, dst)] += 1

    split_dir = output_dir / "splits"
    split_dir.mkdir(parents=True, exist_ok=True)
    all_ids: List[str] = []
    for d in input_dirs:
        all_ids.extend(_episode_ids(d))
    all_ids = list(dict.fromkeys(all_ids))
    merged_splits: Dict[str, List[str]] = {}
    seen_split: Dict[str, str] = {}
    for split_name in ["train", "val", "test"]:
        merged: List[str] = []
        for d in input_dirs:
            f = d / "splits" / f"{split_name}_episodes.txt"
            if f.exists():
                merged.extend(x.strip() for x in f.read_text(encoding="utf-8").splitlines() if x.strip())
        merged = list(dict.fromkeys(merged))
        for eid in merged:
            prev = seen_split.get(eid)
            if prev is not None and prev != split_name:
                raise RuntimeError(f"split leakage while merging: episode {eid} appears in both {prev} and {split_name}")
            seen_split[eid] = split_name
        merged_splits[split_name] = merged
        (split_dir / f"{split_name}_episodes.txt").write_text("\n".join(merged) + ("\n" if merged else ""), encoding="utf-8")
    unassigned = sorted(set(all_ids) - set(seen_split))
    if unassigned:
        raise RuntimeError(
            f"merge would leave {len(unassigned)} episodes unassigned to an upstream split; "
            f"first examples: {unassigned[:5]}. Do not fabricate fallback split membership."
        )
    dump_json(split_dir / "split_manifest.json", {
        "policy": "merge_preserved_upstream_splits",
        "episode_counts": {k: len(v) for k, v in merged_splits.items()},
        "overlap_checked": True,
    })

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
        "graph_storage": graph_storage,
    }
    # Preserve consensus source semantics across city-level and split-level
    # merges.  This does not upgrade hybrid simulated fields to measured truth;
    # it simply keeps the original nuPlan/source-policy identity visible to
    # downstream paper-safe training/evaluation code.
    for key in (
        "scene_source", "accessibility_source", "pudo_source",
        "service_layer_source", "source_policy", "benchmark_ready",
    ):
        value = _consensus_manifest_field(manifests, key)
        if value is not None:
            manifest[key] = value
    dump_json(output_dir / "dataset_manifest.json", manifest)
    validation = validate_dataset(output_dir, strict=strict)
    dump_json(output_dir / "validation_report.json", validation)
    return {"output_dir": str(output_dir), "validation_ok": validation["ok"], **manifest}


def main() -> None:
    p = argparse.ArgumentParser(description="Merge per-city CapPlan/AbilityBench datasets into one canonical dataset directory.")
    p.add_argument("--input_dirs", nargs="+", required=True)
    p.add_argument("--output_dir", required=True)
    p.add_argument("--strict", action="store_true")
    p.add_argument("--clean_output", action="store_true", help="Remove an existing merged target before copying graphs, preventing stale episodes after allowlist changes.")
    args = p.parse_args()
    report = merge_datasets([Path(x) for x in args.input_dirs], Path(args.output_dir), strict=args.strict, clean_output=args.clean_output)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
