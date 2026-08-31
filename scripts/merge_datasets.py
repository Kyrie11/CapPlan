#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, TextIO

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capplan.data.validate_dataset import validate_dataset
from capplan.utils.serialization import dump_json, iter_jsonl, read_jsonl, write_jsonl

try:
    from tqdm.auto import tqdm  # type: ignore
except Exception:  # pragma: no cover
    class _NoTqdm:
        def __init__(self, iterable=None, total=None, **kwargs):
            self.iterable = iterable
        def __iter__(self):
            return iter(self.iterable or [])
        def update(self, n=1):
            return None
        def close(self):
            return None
        def set_postfix_str(self, *args, **kwargs):
            return None
    def tqdm(iterable=None, **kwargs):  # type: ignore
        return _NoTqdm(iterable=iterable, **kwargs)


VERSION = "capplan_merge_datasets_v2_streaming_20260831"

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

# These rows are intentionally repeated for every episode/city and must stay
# globally deduplicated.  All other canonical JSONLs are episode-scoped in the
# current AbilityBench layout, so pairwise-disjoint input episode sets permit a
# byte-preserving raw concatenation fast path.
GLOBAL_DEDUPE_FILES = {"capability_profiles.jsonl"}


def _dedupe(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for row in records:
        key = json.dumps(row, sort_keys=True, separators=(",", ":"), default=str)
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _episode_ids(dataset_dir: Path) -> List[str]:
    return [str(r["episode_id"]) for r in iter_jsonl(dataset_dir / "episodes.jsonl") if r.get("episode_id")]


def _episode_sets_are_disjoint(input_dirs: List[Path]) -> tuple[bool, Dict[str, set[str]], list[dict[str, str]]]:
    sets: Dict[str, set[str]] = {}
    owner: Dict[str, str] = {}
    overlaps: list[dict[str, str]] = []
    for d in input_dirs:
        ids = set(_episode_ids(d))
        sets[str(d)] = ids
        for eid in ids:
            prev = owner.get(eid)
            if prev is not None and prev != str(d):
                overlaps.append({"episode_id": eid, "first": prev, "second": str(d)})
                if len(overlaps) >= 20:
                    return False, sets, overlaps
            owner[eid] = str(d)
    return not overlaps, sets, overlaps


def _link_or_copy(src: Path, dst: Path) -> str:
    """Reuse immutable graph files without duplicating tens of GB of JSONL."""
    try:
        os.link(src, dst)
        return "hardlink"
    except OSError:
        shutil.copy2(src, dst)
        return "copy"


def _manifest_leaf_values(manifest: Dict[str, Any], key: str) -> List[Any]:
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


def _validation_ok(dataset_dir: Path) -> bool:
    p = dataset_dir / "validation_report.json"
    if not p.is_file():
        return False
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return False
    if not isinstance(obj, dict):
        return False
    return bool(obj.get("ok", obj.get("valid", False)))


def _raw_concat_jsonl(
    sources: list[Path],
    dst: Path,
    *,
    progress: bool,
    desc: str,
    chunk_bytes: int = 8 * 1024 * 1024,
) -> int:
    """Concatenate JSONLs byte-for-byte and return the exact row count.

    This is safe only when input episode sets are pairwise disjoint and the file
    is episode-scoped.  It avoids JSON decoding, row hashing, re-encoding and a
    second in-memory copy of multi-million-row label files.
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    existing = [p for p in sources if p.is_file()]
    total = sum(p.stat().st_size for p in existing)
    bar = tqdm(total=total, desc=desc, unit="B", unit_scale=True, disable=not progress)
    rows = 0
    with dst.open("wb") as out:
        for src in existing:
            last = b""
            with src.open("rb") as f:
                while True:
                    block = f.read(chunk_bytes)
                    if not block:
                        break
                    out.write(block)
                    rows += block.count(b"\n")
                    last = block[-1:]
                    bar.update(len(block))
            if src.stat().st_size > 0 and last != b"\n":
                out.write(b"\n")
                rows += 1
    bar.close()
    return rows


def _merge_deduped_jsonl(sources: list[Path], dst: Path, *, progress: bool, desc: str) -> int:
    rows: list[dict[str, Any]] = []
    for src in tqdm(sources, desc=desc, unit="input", disable=not progress):
        rows.extend(read_jsonl(src))
    deduped = _dedupe(rows)
    write_jsonl(dst, deduped)
    return len(deduped)


def merge_datasets(
    input_dirs: List[Path],
    output_dir: Path,
    strict: bool = False,
    clean_output: bool = False,
    *,
    progress: bool = True,
    deep_validation: bool = False,
) -> Dict[str, Any]:
    if not input_dirs:
        raise RuntimeError("at least one input dataset is required")
    for d in input_dirs:
        if not (d / "dataset_manifest.json").exists():
            raise RuntimeError(f"input is not a CapPlan dataset directory: {d}")

    print(f"[CAPPLAN_MERGE] version={VERSION} inputs={len(input_dirs)} output={output_dir}", flush=True)
    disjoint, episode_sets, overlaps = _episode_sets_are_disjoint(input_dirs)
    if overlaps:
        print(f"[CAPPLAN_MERGE] episode_overlap_detected examples={overlaps[:3]}", flush=True)
    upstream_validation_ok = all(_validation_ok(d) for d in input_dirs)
    fast_raw = disjoint
    print(
        f"[CAPPLAN_MERGE] episode_sets_disjoint={disjoint} upstream_validation_ok={upstream_validation_ok} "
        f"strategy={'stream_raw_concat' if fast_raw else 'parse_dedupe_fallback'}",
        flush=True,
    )

    if clean_output and output_dir.exists():
        print(f"[CAPPLAN_MERGE] removing existing output {output_dir}", flush=True)
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "accessibility_graphs").mkdir(parents=True, exist_ok=True)

    merged_counts: Dict[str, int] = {}
    for name in JSONL_FILES:
        sources = [d / name for d in input_dirs]
        dst = output_dir / name
        if fast_raw and name not in GLOBAL_DEDUPE_FILES:
            count = _raw_concat_jsonl(sources, dst, progress=progress, desc=f"merge {name}")
        else:
            count = _merge_deduped_jsonl(sources, dst, progress=progress, desc=f"merge {name}")
        merged_counts[name] = count
        print(f"[CAPPLAN_MERGE] file={name} rows={count}", flush=True)

    graph_storage = {"hardlink": 0, "copy": 0}
    graph_files: list[Path] = []
    for d in input_dirs:
        graph_dir = d / "accessibility_graphs"
        if graph_dir.exists():
            graph_files.extend(f for f in graph_dir.glob("*") if f.is_file())
    for f in tqdm(graph_files, desc="merge accessibility graphs", unit="file", disable=not progress):
        dst = output_dir / "accessibility_graphs" / f.name
        if not dst.exists():
            graph_storage[_link_or_copy(f, dst)] += 1
    print(f"[CAPPLAN_MERGE] graph_storage={graph_storage} source_files={len(graph_files)}", flush=True)

    split_dir = output_dir / "splits"
    split_dir.mkdir(parents=True, exist_ok=True)
    all_ids: List[str] = []
    for d in input_dirs:
        all_ids.extend(sorted(episode_sets[str(d)]))
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
        "merge_version": VERSION,
        "merge_strategy": "stream_raw_concat_disjoint_episodes" if fast_raw else "parse_dedupe_fallback",
        "input_dirs": [str(d) for d in input_dirs],
        "input_manifests": manifests,
        "input_validation_ok": upstream_validation_ok,
        "episode_sets_disjoint": disjoint,
        "num_episodes": merged_counts.get("episodes.jsonl", 0),
        "num_contracts": merged_counts.get("capability_contracts.jsonl", 0),
        "num_transitions": merged_counts.get("candidate_transitions.jsonl", 0),
        "graph_storage": graph_storage,
        "graph_membership_preserved_from_validated_inputs": bool(fast_raw and upstream_validation_ok),
        "jsonl_row_counts": merged_counts,
    }
    for key in (
        "scene_source", "accessibility_source", "pudo_source",
        "service_layer_source", "source_policy", "benchmark_ready",
    ):
        value = _consensus_manifest_field(manifests, key)
        if value is not None:
            manifest[key] = value
    dump_json(output_dir / "dataset_manifest.json", manifest)

    skip_graph_membership = bool(fast_raw and upstream_validation_ok and not deep_validation)
    print(
        f"[CAPPLAN_MERGE] validation_start strict={strict} deep_validation={deep_validation} "
        f"reuse_upstream_graph_membership={skip_graph_membership}",
        flush=True,
    )
    validation = validate_dataset(
        output_dir,
        strict=strict,
        progress=progress,
        skip_graph_membership=skip_graph_membership,
    )
    validation["validation_mode"] = "merged_fast" if skip_graph_membership else "deep"
    validation["merge_version"] = VERSION
    dump_json(output_dir / "validation_report.json", validation)
    print(
        f"[CAPPLAN_MERGE] validation_done ok={validation.get('ok')} mode={validation['validation_mode']} "
        f"errors={len(validation.get('errors') or [])} warnings={len(validation.get('warnings') or [])}",
        flush=True,
    )
    return {"output_dir": str(output_dir), "validation_ok": validation["ok"], **manifest}


def main() -> None:
    p = argparse.ArgumentParser(description="Merge per-city CapPlan/AbilityBench datasets into one canonical dataset directory.")
    p.add_argument("--input_dirs", nargs="+", required=True)
    p.add_argument("--output_dir", required=True)
    p.add_argument("--strict", action="store_true")
    p.add_argument("--clean_output", action="store_true", help="Remove an existing merged target before copying graphs, preventing stale episodes after allowlist changes.")
    p.add_argument("--no_progress", action="store_true", help="Disable merge/validation progress bars and stage messages.")
    p.add_argument("--deep_validation", action="store_true", help="Re-parse merged graph node files instead of reusing strict validation from byte-preserved, disjoint inputs. Much slower.")
    args = p.parse_args()
    report = merge_datasets(
        [Path(x) for x in args.input_dirs], Path(args.output_dir), strict=args.strict,
        clean_output=args.clean_output, progress=not args.no_progress,
        deep_validation=args.deep_validation,
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
