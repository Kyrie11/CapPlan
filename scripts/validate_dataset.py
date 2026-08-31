#!/usr/bin/env python
from __future__ import annotations

import sys
from pathlib import Path as _Path
sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

import argparse
from capplan.data.validate_dataset import validate_dataset
from capplan.utils.serialization import dump_json, load_json


def _can_reuse_merged_graph_validation(dataset_dir: _Path) -> bool:
    manifest_path = dataset_dir / "dataset_manifest.json"
    if not manifest_path.is_file():
        return False
    try:
        manifest = load_json(manifest_path)
    except Exception:
        return False
    if not isinstance(manifest, dict):
        return False
    if manifest.get("mode") != "merged_capplan_dataset":
        return False
    if not manifest.get("episode_sets_disjoint"):
        return False
    if not manifest.get("input_validation_ok"):
        return False
    if not manifest.get("graph_membership_preserved_from_validated_inputs"):
        return False
    # Re-check that upstream validation reports are still present and PASS.
    for raw in manifest.get("input_dirs") or []:
        p = _Path(str(raw)) / "validation_report.json"
        if not p.is_file():
            return False
        try:
            obj = load_json(p)
        except Exception:
            return False
        if not isinstance(obj, dict) or not bool(obj.get("ok", obj.get("valid", False))):
            return False
    return True


def main() -> None:
    p = argparse.ArgumentParser(description="Validate a canonical CapPlan dataset.")
    p.add_argument("--dataset_dir", required=True)
    p.add_argument("--strict", action="store_true")
    p.add_argument("--no_progress", action="store_true", help="Disable validation progress bars/stage output.")
    p.add_argument(
        "--deep_graph_validation", action="store_true",
        help="Force re-parsing every merged accessibility node file. By default a trusted merged dataset reuses strict upstream graph membership validation.",
    )
    args = p.parse_args()
    root = _Path(args.dataset_dir)
    reuse = (not args.deep_graph_validation) and _can_reuse_merged_graph_validation(root)
    if reuse:
        print("[CAPPLAN_VALIDATE] merged fast path: reusing strict graph-membership validation from byte-preserved disjoint inputs", flush=True)
    elif not args.deep_graph_validation:
        print("[CAPPLAN_VALIDATE] merged fast path unavailable; falling back to deep graph validation", flush=True)
    result = validate_dataset(
        root,
        strict=args.strict,
        progress=not args.no_progress,
        skip_graph_membership=reuse,
    )
    result["validation_mode"] = "merged_fast" if reuse else "deep"
    dump_json(root / "validation_report.json", result)
    print(result)
    errors = result.get("errors", []) if isinstance(result, dict) else []
    print(f"DATASET_SCHEMA_CHECK={'PASS' if not errors else 'FAIL'}")


if __name__ == "__main__":
    main()
