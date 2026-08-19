#!/usr/bin/env python
"""Create a compact progress/performance snapshot for full bootstrap builds."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict


def _load(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception as exc:
        return {"status": "INVALID_JSON", "error": str(exc), "path": str(path)}



def _graph_markers_for_source(graph_dir: Path, source_name: str) -> int:
    count = 0
    if not graph_dir.exists():
        return 0
    for marker in graph_dir.glob("*.build.json"):
        try:
            payload = json.loads(marker.read_text(encoding="utf-8"))
        except Exception:
            continue
        if payload.get("status") == "PASS" and payload.get("source") == source_name:
            count += 1
    return count

def _pudo_shards(pudo_file: Path) -> int:
    d = pudo_file.parent / f"{pudo_file.stem}.shards"
    if not d.exists():
        return 0
    return sum(1 for _ in d.glob("*.build.json"))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--data_root", required=True)
    p.add_argument("--external_root", required=True)
    p.add_argument("--output", required=True)
    args = p.parse_args()

    data_root = Path(args.data_root)
    external_root = Path(args.external_root)
    cities = ["boston", "pittsburgh", "vegas", "singapore"]
    splits = ["train", "val", "test"]
    snapshot: Dict[str, Any] = {"status": "PASS", "splits": {}}

    for split in splits:
        prepared = data_root / "outputs" / "prepared" / split
        reports = external_root / "reports" / "build" / split
        split_row: Dict[str, Any] = {"cities": {}}
        for city in cities:
            scene_manifest = _load(prepared / "scene_contexts" / city / "scene_context_manifest.json")
            graph_timing = _load(reports / f"graph_timing.{city}.json")
            graph_summary = graph_timing.get("summary", {}) if isinstance(graph_timing.get("summary"), dict) else {}
            pudo_report = _load(reports / f"pudo.{city}.json")
            pudo_timing = _load(reports / f"pudo_timing.{city}.json")
            pudo_perf = pudo_timing.get("performance", {}) if isinstance(pudo_timing.get("performance"), dict) else {}
            pudo_file = prepared / "pudo" / f"{city}.jsonl"
            pudo_inprogress = pudo_file.with_suffix(pudo_file.suffix + ".inprogress.json")
            pudo_running = _load(pudo_inprogress)
            graph_dir = prepared / "accessibility_graphs"
            scene_count = scene_manifest.get("num_scenes")
            graph_done = int(graph_summary.get("episode_count", 0) or 0)
            pudo_done = int(pudo_report.get("episode_count", 0) or pudo_perf.get("episodes", 0) or 0)
            shard_done = _pudo_shards(pudo_file)
            split_row["cities"][city] = {
                "scene_extract": {
                    "complete": bool(scene_manifest.get("status") == "PASS"),
                    "scenes": scene_count,
                    "elapsed_s": scene_manifest.get("elapsed_s"),
                    "scenes_per_s": scene_manifest.get("scenes_per_s"),
                    "db_files_expanded_count": (scene_manifest.get("nuplan") or {}).get("db_files_expanded_count") if isinstance(scene_manifest.get("nuplan"), dict) else None,
                },
                "graphs": {
                    "complete": bool(graph_timing.get("status") == "PASS"),
                    "episodes": graph_done,
                    "elapsed_s": graph_summary.get("elapsed_s"),
                    "episodes_per_s": graph_summary.get("episodes_per_s"),
                    "features_loaded": graph_summary.get("features_loaded"),
                    "resumed_episodes": graph_summary.get("resumed_episodes"),
                    "build_markers_on_disk_for_city": _graph_markers_for_source(graph_dir, f"{city}_fused_external_accessibility"),
                    "slowest_episodes": graph_timing.get("slowest_episodes", [])[:10] if isinstance(graph_timing.get("slowest_episodes"), list) else [],
                },
                "pudo": {
                    "complete": bool(not pudo_inprogress.exists() and pudo_report.get("status") == "PASS" and pudo_file.exists()),
                    "in_progress": bool(pudo_inprogress.exists()),
                    "running_marker": pudo_running if pudo_inprogress.exists() else None,
                    "canonical_output_may_be_stale": bool(pudo_inprogress.exists() and pudo_file.exists()),
                    "episodes": pudo_done,
                    "rows": pudo_report.get("rows"),
                    "rows_per_episode": pudo_report.get("rows_per_episode"),
                    "elapsed_s": pudo_perf.get("elapsed_s"),
                    "episodes_per_s": pudo_perf.get("episodes_per_s"),
                    "resumed_episodes": pudo_perf.get("resumed_episodes"),
                    "shard_markers_on_disk": shard_done,
                    "slowest_episodes": pudo_perf.get("slowest_episodes", [])[:10] if isinstance(pudo_perf.get("slowest_episodes"), list) else [],
                },
            }
        snapshot["splits"][split] = split_row

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snapshot, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(snapshot, indent=2, sort_keys=True))
    print("BOOTSTRAP_PERFORMANCE_SNAPSHOT_CHECK=PASS")


if __name__ == "__main__":
    main()
