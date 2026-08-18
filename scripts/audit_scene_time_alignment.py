#!/usr/bin/env python
"""Audit nuPlan scene clock semantics before time-dependent evidence fusion."""
from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from capplan.utils.serialization import dump_json, read_jsonl


def _iso_utc(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    try:
        return dt.datetime.fromtimestamp(float(seconds), tz=dt.timezone.utc).isoformat()
    except Exception:
        return None


def audit(scene_dir: str | Path, require_absolute: bool = False) -> Dict[str, Any]:
    root = Path(scene_dir)
    rows = read_jsonl(root / "scenes.jsonl")
    issues: List[Dict[str, Any]] = []
    sources: Counter[str] = Counter()
    starts: List[float] = []
    absolute = 0
    monotonic = 0
    plausible_epoch = 0
    for row in rows:
        eid = str(row.get("episode_id") or row.get("scenario_token") or "unknown")
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        src = str(meta.get("time_source") or "missing")
        sources[src] += 1
        is_abs = meta.get("absolute_timestamp_available") is True
        if is_abs:
            absolute += 1
        ts = [float(x) for x in (row.get("timestamps_s") or [])]
        is_monotonic = bool(ts) and all(b >= a for a, b in zip(ts, ts[1:]))
        if is_monotonic:
            monotonic += 1
        else:
            issues.append({"episode_id": eid, "issue": "timestamps_missing_or_nonmonotonic"})
        start = ts[0] if ts else None
        if start is not None:
            starts.append(start)
            # Epoch seconds after 2000-01-01 and before 2100-01-01. This is a
            # representation sanity check, not an assertion about nuPlan year.
            if 946684800.0 <= start <= 4102444800.0:
                plausible_epoch += 1
            elif is_abs:
                issues.append({"episode_id": eid, "issue": "absolute_timestamp_not_plausible_epoch", "start_s": start})
        if is_abs and meta.get("scene_start_time_us") is None:
            issues.append({"episode_id": eid, "issue": "absolute_timestamp_missing_scene_start_time_us"})
        if require_absolute and not is_abs:
            issues.append({"episode_id": eid, "issue": "absolute_timestamp_required", "time_source": src})
    n = len(rows)
    report = {
        "ok": n > 0 and monotonic == n and (not require_absolute or absolute == n) and not issues,
        "scene_dir": str(root),
        "num_scenes": n,
        "absolute_timestamp_scenes": absolute,
        "absolute_timestamp_rate": absolute / max(1, n),
        "monotonic_timestamp_scenes": monotonic,
        "plausible_epoch_scenes": plausible_epoch,
        "time_source_counts": dict(sources),
        "min_scene_time_utc": _iso_utc(min(starts) if starts else None),
        "max_scene_time_utc": _iso_utc(max(starts) if starts else None),
        "issues": issues[:100],
        "require_absolute": bool(require_absolute),
    }
    return report


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--scene_dir", required=True)
    p.add_argument("--require_absolute", action="store_true")
    p.add_argument("--output", required=True)
    args = p.parse_args()
    report = audit(args.scene_dir, args.require_absolute)
    dump_json(args.output, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    print("CAPPLAN_SCENE_TIME_CHECK", "PASS" if report["ok"] else "FAIL")
    if not report["ok"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
