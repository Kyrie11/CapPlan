#!/usr/bin/env python
"""Summarize CapPlan runtime sampler logs while excluding post-build idle time."""
from __future__ import annotations

import argparse
import json
import math
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from typing import Any, Dict, List, Optional

SAMPLE_RE = re.compile(r"^===== SAMPLE (.+) =====$")
DIRECT_STAGE = {
    "extract_nuplan_scenes.py": "extract",
    "build_accessibility_graphs.py": "graphs",
    "build_pudo_evidence.py": "pudo",
}


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s.strip())


def _city_from_cmd(cmd: str, stage: str) -> str:
    patterns = []
    if stage == "extract":
        patterns = [r"/scene_contexts/([a-z_]+)(?:\s|$)"]
    elif stage == "graphs":
        patterns = [r"--source_name\s+([a-z_]+)_fused_external_accessibility", r"graph_(?:source|quality|timing)\.([a-z_]+)\.json"]
    elif stage == "pudo":
        patterns = [r"/pudo/([a-z_]+)\.jsonl", r"--source_name\s+([a-z_]+)_(?:bootstrap|city)"]
    for pattern in patterns:
        m = re.search(pattern, cmd)
        if m:
            return m.group(1)
    return "unknown"


def _stage_from_cmd(cmd: str) -> Optional[str]:
    for needle, stage in DIRECT_STAGE.items():
        if needle in cmd:
            return stage
    return None


def _parse_samples(text: str) -> List[Dict[str, Any]]:
    lines = text.splitlines()
    samples: List[Dict[str, Any]] = []
    i = 0
    while i < len(lines):
        m = SAMPLE_RE.match(lines[i])
        if not m:
            i += 1
            continue
        sample: Dict[str, Any] = {"timestamp": m.group(1), "processes": [], "vmstat_wa": None, "device_util": {}}
        i += 1
        section = ""
        vm_data: List[List[str]] = []
        iostat_rows: Dict[str, float] = {}
        while i < len(lines) and not SAMPLE_RE.match(lines[i]):
            line = lines[i]
            if line.startswith("CAPPLAN_ACTIVE="):
                mm = re.search(r"CAPPLAN_ACTIVE=(\d+)", line)
                sample["explicit_active"] = bool(mm and mm.group(1) == "1")
            if line.startswith("--- "):
                section = line.strip("- ")
            elif section == "CapPlan processes" and line.strip() and not line.lstrip().startswith("PID"):
                parts = line.split(None, 8)
                if len(parts) >= 9 and parts[0].isdigit():
                    try:
                        proc = {"pid": int(parts[0]), "ppid": int(parts[1]), "psr": int(parts[2]), "pcpu": float(parts[3]), "pmem": float(parts[4]), "rss_kb": int(parts[5]), "etime": parts[6], "stat": parts[7], "cmd": parts[8]}
                        stage = _stage_from_cmd(proc["cmd"])
                        if stage:
                            proc["stage"] = stage
                            proc["city"] = _city_from_cmd(proc["cmd"], stage)
                        sample["processes"].append(proc)
                    except Exception:
                        pass
            elif section == "vmstat":
                parts = line.split()
                if len(parts) >= 17 and all(re.fullmatch(r"-?\d+(?:\.\d+)?", x) for x in parts[:17]):
                    vm_data.append(parts)
            elif section == "iostat":
                parts = line.split()
                if len(parts) >= 2 and re.fullmatch(r"[A-Za-z0-9_.\-/]+", parts[0]):
                    try:
                        util = float(parts[-1])
                        if parts[0].lower() != "device":
                            iostat_rows[parts[0]] = util
                    except Exception:
                        pass
            i += 1
        if vm_data:
            try:
                sample["vmstat_wa"] = float(vm_data[-1][15])
            except Exception:
                pass
        sample["device_util"] = iostat_rows
        direct = [p for p in sample["processes"] if p.get("stage")]
        sample["active"] = bool(direct) if "explicit_active" not in sample else bool(sample["explicit_active"])
        sample["direct_processes"] = direct
        samples.append(sample)
    return samples


def _safe_stats(values: List[float]) -> Dict[str, Any]:
    if not values:
        return {"n": 0}
    vals = [float(x) for x in values if math.isfinite(float(x))]
    return {"n": len(vals), "mean": mean(vals), "median": median(vals), "min": min(vals), "max": max(vals)} if vals else {"n": 0}


def summarize(path: Path) -> Dict[str, Any]:
    samples = _parse_samples(path.read_text(encoding="utf-8", errors="replace"))
    active = [s for s in samples if s.get("active") and s.get("direct_processes")]
    result: Dict[str, Any] = {
        "status": "PASS",
        "input": str(path),
        "samples_total": len(samples),
        "samples_active_direct_stage": len(active),
        "samples_excluded_idle_or_orchestrator_only": len(samples) - len(active),
        "idle_samples_are_excluded_from_stage_statistics": True,
        "stages": {},
    }
    if samples:
        result["sampler_first_timestamp"] = samples[0]["timestamp"]
        result["sampler_last_timestamp"] = samples[-1]["timestamp"]
    if active:
        result["active_first_timestamp"] = active[0]["timestamp"]
        result["active_last_timestamp"] = active[-1]["timestamp"]
        result["active_window_s"] = (_dt(active[-1]["timestamp"]) - _dt(active[0]["timestamp"])).total_seconds()
        if samples:
            result["post_active_tail_s"] = max(0.0, (_dt(samples[-1]["timestamp"]) - _dt(active[-1]["timestamp"])).total_seconds())

    groups: Dict[tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)
    for sample in active:
        # In the current orchestrator there is one direct stage process at a
        # time. If future parallel mode has several, attribute the same system
        # iowait/device observation to each active stage/city, and process CPU
        # only to its own process.
        for proc in sample["direct_processes"]:
            groups[(proc["stage"], proc.get("city", "unknown"))].append({"sample": sample, "proc": proc})
    for (stage, city), rows in sorted(groups.items()):
        cpu = [r["proc"]["pcpu"] for r in rows]
        rss = [r["proc"]["rss_kb"] / 1024.0 for r in rows]
        wa = [r["sample"]["vmstat_wa"] for r in rows if r["sample"].get("vmstat_wa") is not None]
        # Preserve all observed devices. The user can identify /data0's backing
        # device from the profiler's storage-context header in future logs.
        devices: Dict[str, List[float]] = defaultdict(list)
        for r in rows:
            for dev, util in (r["sample"].get("device_util") or {}).items():
                devices[dev].append(float(util))
        first = rows[0]["sample"]["timestamp"]; last = rows[-1]["sample"]["timestamp"]
        result["stages"].setdefault(city, {})[stage] = {
            "samples": len(rows),
            "first_timestamp": first,
            "last_timestamp": last,
            "sample_span_s": (_dt(last) - _dt(first)).total_seconds(),
            "process_cpu_percent": _safe_stats(cpu),
            "process_rss_mib": _safe_stats(rss),
            "vmstat_iowait_percent": _safe_stats(wa),
            "device_util_percent": {dev: _safe_stats(vals) for dev, vals in sorted(devices.items())},
        }
    return result


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    args = p.parse_args()
    result = summarize(Path(args.input))
    out = Path(args.output); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
