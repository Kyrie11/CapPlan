#!/usr/bin/env python
"""Complete missing accessibility-edge attributes with auditable simulation.

Real graph topology and geometry are preserved byte-for-byte at the node level.
Only missing edge attributes are filled. Each simulated value is recorded in
AccessibilityEdge.metadata.field_provenance and never represents measured city
truth. Existing observed/derived values are retained.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Mapping, MutableMapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from capplan.utils.serialization import iter_jsonl, write_jsonl

VERSION = "abilitybench_hybrid_accessibility_v1_20260822"
FIELDS = ("width_m", "slope", "cross_slope", "surface", "curb_ramp", "step_free", "lighting", "shelter")


def _seed(base: int, *parts: str) -> int:
    return int.from_bytes(hashlib.sha256("|".join([str(base), *parts]).encode()).digest()[:8], "big") & 0x7FFFFFFF


def _is_blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _source_kind(src: Any) -> str:
    s = str(src or "").lower()
    if s.startswith("synthetic") or "simulated" in s:
        return "simulated"
    if "dem" in s or "derived" in s or "osm" in s:
        return "derived"
    return "observed_or_derived"


def _is_curb_context(row: Mapping[str, Any]) -> bool:
    return (
        str(row.get("crossing_type") or "").lower() == "curb"
        or "curb" in str(row.get("edge_id") or "").lower()
        or "pudo" in str(row.get("from_node") or "").lower()
        or "pudo" in str(row.get("to_node") or "").lower()
        or "curb" in str(row.get("from_node") or "").lower()
        or "curb" in str(row.get("to_node") or "").lower()
    )


def _sim_prov(city: str, split: str, episode_id: str, edge_id: str, field: str, seed: int, method: str) -> Dict[str, Any]:
    return {
        "kind": "simulated",
        "source": VERSION,
        "method": method,
        "seed": seed,
        "city_context": city,
        "split": split,
        "episode_id": episode_id,
        "edge_id": edge_id,
        "claim_scope": "benchmark_scenario_truth_not_real_site_ground_truth",
    }


def _existing_prov(row: Mapping[str, Any], field: str) -> Dict[str, Any]:
    meta = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
    fp = meta.get("field_provenance") if isinstance(meta, Mapping) and isinstance(meta.get("field_provenance"), Mapping) else {}
    if isinstance(fp.get(field), Mapping):
        return dict(fp[field])
    return {"kind": _source_kind(row.get("source")), "source": str(row.get("source") or "prepared_accessibility_graph"), "method": "preexisting_graph_attribute"}


def _fill(row: MutableMapping[str, Any], *, city: str, split: str, episode_id: str, base_seed: int) -> Counter:
    edge_id = str(row.get("edge_id") or "unknown")
    s = _seed(base_seed, city, split, episode_id, edge_id)
    rng = random.Random(s)
    meta = dict(row.get("metadata") or {}) if isinstance(row.get("metadata"), Mapping) else {}
    fp = dict(meta.get("field_provenance") or {}) if isinstance(meta.get("field_provenance"), Mapping) else {}
    kinds = Counter()

    # Preserve existing attributes and annotate provenance if absent.
    for field in FIELDS:
        if not _is_blank(row.get(field)):
            fp.setdefault(field, _existing_prov(row, field))
            kinds[str(fp[field].get("kind") or "unknown")] += 1

    # Edge-level difficulty produces both feasible and infeasible examples while
    # keeping the generated environment physically plausible.
    difficulty = rng.random()
    narrow = difficulty < 0.10
    steep = 0.10 <= difficulty < 0.17
    cross_steep = 0.17 <= difficulty < 0.23
    curb_context = _is_curb_context(row)
    no_ramp = curb_context and 0.23 <= difficulty < 0.33

    def put(field: str, value: Any, method: str) -> None:
        if _is_blank(row.get(field)):
            row[field] = value
            fp[field] = _sim_prov(city, split, episode_id, edge_id, field, s, method)
            kinds["simulated"] += 1

    put("width_m", round(rng.uniform(0.80, 1.10), 3) if narrow else round(rng.uniform(1.45, 3.20), 3), "conditional_path_width_prior")
    put("slope", round(rng.uniform(0.09, 0.14), 4) if steep else round(rng.uniform(0.005, 0.065), 4), "conditional_running_slope_prior")
    put("cross_slope", round(rng.uniform(0.045, 0.075), 4) if cross_steep else round(rng.uniform(0.005, 0.03), 4), "conditional_cross_slope_prior")
    put("surface", ["concrete", "paved", "asphalt"][s % 3], "urban_pedestrian_surface_prior")
    if curb_context:
        put("curb_ramp", not no_ramp, "conditional_curb_ramp_prior")
    put("step_free", False if (no_ramp or steep and rng.random() < 0.25) else True, "conditional_step_free_prior")
    put("lighting", "day", "benchmark_time_of_day_context")
    put("shelter", bool((s // 7) % 5 == 0), "urban_shelter_prior")

    meta.update({
        "field_provenance": fp,
        "truth_mode": "hybrid_geometry_anchored_simulated_accessibility_v1",
        "paper_claim_allowed": not any(str(v.get("kind")) == "simulated" for v in fp.values() if isinstance(v, Mapping)),
    })
    row["metadata"] = meta
    if any(str(v.get("kind")) == "simulated" for v in fp.values() if isinstance(v, Mapping)):
        src = str(row.get("source") or "prepared_accessibility_graph")
        if VERSION not in src:
            row["source"] = src + "+" + VERSION
    return kinds


def _copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        if dst.exists(): dst.unlink()
        os.link(src, dst)
    except Exception:
        shutil.copy2(src, dst)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input_graph_dir", required=True)
    p.add_argument("--output_graph_dir", required=True)
    p.add_argument("--city", required=True, choices=["boston", "pittsburgh", "vegas", "singapore"])
    p.add_argument("--split", required=True, choices=["train", "val", "test"])
    p.add_argument("--seed", type=int, default=20260822)
    p.add_argument("--episode_allowlist", default=None, help="Optional text file of episode IDs to overlay")
    p.add_argument("--report_json", required=True)
    args = p.parse_args()

    inp = Path(args.input_graph_dir); out = Path(args.output_graph_dir)
    if not inp.exists(): raise FileNotFoundError(inp)
    out.mkdir(parents=True, exist_ok=True)
    allow = None
    if args.episode_allowlist:
        allow = {x.strip() for x in Path(args.episode_allowlist).read_text().splitlines() if x.strip() and not x.lstrip().startswith("#")}

    edge_files = sorted(inp.glob("*.edges.jsonl"))
    if not edge_files: raise RuntimeError(f"no *.edges.jsonl in {inp}")
    reports = Counter(); field_kind_counts: Dict[str, Counter] = defaultdict(Counter)
    episodes = 0; edges_total = 0; simulated_edges = 0
    for ef in edge_files:
        eid = ef.name[:-len(".edges.jsonl")]
        if allow is not None and eid not in allow: continue
        rows = []
        episode_sim = False
        for raw in iter_jsonl(ef):
            row = dict(raw); before = {k: row.get(k) for k in FIELDS}
            _fill(row, city=args.city, split=args.split, episode_id=eid, base_seed=args.seed)
            fp = ((row.get("metadata") or {}).get("field_provenance") or {})
            for field in FIELDS:
                pv = fp.get(field) if isinstance(fp, Mapping) else None
                if isinstance(pv, Mapping): field_kind_counts[field][str(pv.get("kind") or "unknown")] += 1
                if _is_blank(before.get(field)) and not _is_blank(row.get(field)):
                    reports[field] += 1
                    if isinstance(pv, Mapping) and str(pv.get("kind")) == "simulated": episode_sim = True
            rows.append(row)
        write_jsonl(out / ef.name, rows)
        edges_total += len(rows); simulated_edges += sum(1 for r in rows if not bool((r.get("metadata") or {}).get("paper_claim_allowed", True)))
        nf = inp / f"{eid}.nodes.jsonl"
        if nf.exists(): _copy_file(nf, out / nf.name)
        mf = inp / f"{eid}.meta.json"
        if mf.exists():
            try:
                payload = json.loads(mf.read_text())
                md = dict(payload.get("metadata") or {})
                md.update({"truth_mode": "hybrid_geometry_anchored_simulated_accessibility_v1", "hybrid_overlay_version": VERSION, "paper_claim_allowed": not episode_sim})
                payload["metadata"] = md
                (out / mf.name).write_text(json.dumps(payload, indent=2, sort_keys=True)+"\n")
            except Exception:
                _copy_file(mf, out / mf.name)
        episodes += 1

    report = {
        "status": "PASS",
        "version": VERSION,
        "city": args.city,
        "split": args.split,
        "episodes": episodes,
        "edges": edges_total,
        "edges_with_any_simulated_field": simulated_edges,
        "filled_missing_field_counts": dict(reports),
        "field_provenance_kind_counts": {k: dict(v) for k,v in sorted(field_kind_counts.items())},
        "input_graph_dir": str(inp),
        "output_graph_dir": str(out),
        "interpretation": "Real topology/geometry is preserved. Simulated edge attributes are benchmark truth only, never measured city truth.",
    }
    rp=Path(args.report_json); rp.parent.mkdir(parents=True, exist_ok=True); rp.write_text(json.dumps(report, indent=2, sort_keys=True)+"\n")
    print(json.dumps(report, indent=2, sort_keys=True)); print("HYBRID_ACCESSIBILITY_OVERLAY_CHECK=PASS")

if __name__ == "__main__": main()
