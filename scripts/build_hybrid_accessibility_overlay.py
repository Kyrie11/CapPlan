#!/usr/bin/env python
"""Complete missing accessibility-edge attributes with auditable, spatially correlated simulation.

The hybrid branch preserves real graph topology, node coordinates and every
pre-existing observed/derived edge attribute.  Missing static pedestrian facts
are completed with deterministic priors correlated at the underlying mapped
feature/connector level, so adjacent segments do not receive IID checkerboard
properties.  Simulated values are benchmark scenario truth only and are never
represented as measured city ground truth.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import re
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Mapping, MutableMapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from capplan.utils.serialization import iter_jsonl, write_jsonl

VERSION = "abilitybench_hybrid_accessibility_v2_20260823"
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
        str(row.get("crossing_type") or "").lower() in {"curb", "curb_connector"}
        or "curb" in str(row.get("edge_id") or "").lower()
        or "pudo" in str(row.get("from_node") or "").lower()
        or "pudo" in str(row.get("to_node") or "").lower()
        or "curb" in str(row.get("from_node") or "").lower()
        or "curb" in str(row.get("to_node") or "").lower()
    )


def _is_entrance_connector(row: Mapping[str, Any]) -> bool:
    ct = str(row.get("crossing_type") or "").lower()
    eid = str(row.get("edge_id") or "").lower()
    return ct == "entrance_connector" or eid.startswith("entrance_connector:")


def _edge_group_key(row: Mapping[str, Any]) -> str:
    """Return a stable physical-feature key shared across adjacent/reverse edges.

    Linear GIS features have IDs like ``<feature>:12:13[:rev]``.  We strip the
    segment suffix so all segments from the same source feature share static
    simulated properties.  Snap connectors use a direction-independent endpoint
    key; these node IDs are map-quantized and stable across nuPlan snapshots.
    """
    eid = str(row.get("edge_id") or "unknown")
    base = re.sub(r":rev$", "", eid)
    m = re.match(r"^(.*):\d+:\d+$", base)
    if m:
        return f"feature:{m.group(1)}"
    if ":" in base and ("connector" in base.lower() or "+snap" in str(row.get("source") or "").lower()):
        a = str(row.get("from_node") or "")
        b = str(row.get("to_node") or "")
        return "connector:" + "|".join(sorted([a, b]))
    # Fallback keeps reverse edges tied and is still stable across repeated scenes
    # when the real graph builder emits stable edge IDs.
    return f"edge:{base}"


def _city_priors(city: str) -> Dict[str, tuple[float, float] | float]:
    # These are conservative plausibility priors, not municipal measurements.
    # Singapore's normal accessible-route width is slightly shifted upward to
    # reflect current BCA accessibility design context; the US range covers
    # common urban sidewalks while retaining intentionally narrow negatives.
    if city == "singapore":
        return {
            "normal_width": (1.50, 3.20),
            "narrow_width": (0.80, 1.18),
            "normal_slope": (0.002, 0.050),
            "steep_slope": (0.085, 0.140),
            "normal_cross": (0.003, 0.020),
            "steep_cross": (0.028, 0.060),
            "lit_prob": 0.94,
            "shelter_prob": 0.14,
            "curb_ramp_prob": 0.90,
        }
    return {
        "normal_width": (1.22, 3.20),
        "narrow_width": (0.76, 1.10),
        "normal_slope": (0.002, 0.055),
        "steep_slope": (0.085, 0.140),
        "normal_cross": (0.003, 0.022),
        "steep_cross": (0.028, 0.060),
        "lit_prob": 0.90,
        "shelter_prob": 0.08,
        "curb_ramp_prob": 0.84,
    }


def _sim_prov(
    city: str,
    split: str,
    episode_id: str,
    edge_id: str,
    group_key: str,
    field: str,
    seed: int,
    method: str,
) -> Dict[str, Any]:
    return {
        "kind": "simulated",
        "source": VERSION,
        "method": method,
        "seed": seed,
        "city_context": city,
        "split": split,
        "episode_id": episode_id,
        "edge_id": edge_id,
        "simulation_group_id": group_key,
        "correlation_scope": "mapped_feature_or_connector_across_episodes_and_splits",
        "claim_scope": "benchmark_scenario_truth_not_real_site_ground_truth",
    }


def _existing_prov(row: Mapping[str, Any], field: str) -> Dict[str, Any]:
    meta = row.get("metadata") if isinstance(row.get("metadata"), Mapping) else {}
    fp = meta.get("field_provenance") if isinstance(meta, Mapping) and isinstance(meta.get("field_provenance"), Mapping) else {}
    if isinstance(fp.get(field), Mapping):
        return dict(fp[field])
    return {
        "kind": _source_kind(row.get("source")),
        "source": str(row.get("source") or "prepared_accessibility_graph"),
        "method": "preexisting_graph_attribute",
    }


def _group_class(rng: random.Random, row: Mapping[str, Any]) -> str:
    """Draw one stable physical class for the whole underlying map feature."""
    if "steps" in str(row.get("source") or "").lower() or "step" in str(row.get("edge_id") or "").lower():
        return "steps_or_non_step_free"
    u = rng.random()
    if u < 0.78:
        return "typical_accessible"
    if u < 0.86:
        return "narrow"
    if u < 0.92:
        return "steep"
    if u < 0.96:
        return "high_cross_slope"
    return "rough_or_non_step_free"


def _fill(row: MutableMapping[str, Any], *, city: str, split: str, episode_id: str, base_seed: int) -> tuple[Counter, str]:
    edge_id = str(row.get("edge_id") or "unknown")
    group_key = _edge_group_key(row)
    # Intentionally exclude split/episode from static seed.  The same physical
    # source feature must retain width/slope/surface in repeated nuPlan snapshots.
    group_seed = _seed(base_seed, city, group_key, "static")
    group_rng = random.Random(group_seed)
    # Small deterministic per-edge jitter preserves smooth variation without
    # changing the feature's accessibility class.
    edge_seed = _seed(base_seed, city, group_key, edge_id, "segment")
    edge_rng = random.Random(edge_seed)
    pri = _city_priors(city)
    group_class = _group_class(group_rng, row)

    meta = dict(row.get("metadata") or {}) if isinstance(row.get("metadata"), Mapping) else {}
    fp = dict(meta.get("field_provenance") or {}) if isinstance(meta.get("field_provenance"), Mapping) else {}
    kinds = Counter()

    for field in FIELDS:
        if not _is_blank(row.get(field)):
            fp.setdefault(field, _existing_prov(row, field))
            kinds[str(fp[field].get("kind") or "unknown")] += 1

    curb_context = _is_curb_context(row)
    entrance_connector = _is_entrance_connector(row)

    def put(field: str, value: Any, method: str) -> None:
        if _is_blank(row.get(field)):
            row[field] = value
            fp[field] = _sim_prov(city, split, episode_id, edge_id, group_key, field, group_seed, method)
            kinds["simulated"] += 1

    normal_width = pri["normal_width"]
    narrow_width = pri["narrow_width"]
    normal_slope = pri["normal_slope"]
    steep_slope = pri["steep_slope"]
    normal_cross = pri["normal_cross"]
    steep_cross = pri["steep_cross"]
    assert isinstance(normal_width, tuple) and isinstance(narrow_width, tuple)
    assert isinstance(normal_slope, tuple) and isinstance(steep_slope, tuple)
    assert isinstance(normal_cross, tuple) and isinstance(steep_cross, tuple)

    if group_class == "narrow":
        width = edge_rng.uniform(*narrow_width)
    else:
        width = edge_rng.uniform(*normal_width)
    if entrance_connector:
        # A building/public-realm connector is short and typically not wider
        # than the adjoining walkway, while still remaining plausible.
        width = min(width, edge_rng.uniform(1.20 if city != "singapore" else 1.50, 2.20))
    put("width_m", round(width, 3), "city_conditioned_feature_correlated_path_width_prior")

    if group_class == "steep":
        slope = edge_rng.uniform(*steep_slope)
    else:
        slope = edge_rng.uniform(*normal_slope)
    put("slope", round(slope, 4), "feature_correlated_running_slope_prior")

    if group_class == "high_cross_slope":
        cross = edge_rng.uniform(*steep_cross)
    else:
        cross = edge_rng.uniform(*normal_cross)
    put("cross_slope", round(cross, 4), "feature_correlated_cross_slope_prior")

    # Urban pedestrian networks are overwhelmingly hard-surfaced; use a stable
    # weighted prior instead of the previous equal-probability per-edge draw.
    su = group_rng.random()
    if su < 0.62:
        surface = "concrete"
    elif su < 0.88:
        surface = "paved"
    elif su < 0.98:
        surface = "asphalt"
    else:
        surface = "compacted_gravel"
    if group_class == "rough_or_non_step_free" and group_rng.random() < 0.35:
        surface = "compacted_gravel"
    put("surface", surface, "weighted_urban_surface_feature_prior")

    curb_ramp = row.get("curb_ramp") if isinstance(row.get("curb_ramp"), bool) else None
    existing_step_free = row.get("step_free") if isinstance(row.get("step_free"), bool) else None
    if curb_context and curb_ramp is None:
        ramp_prob = float(pri["curb_ramp_prob"])
        # Negative curb-interface cases are deliberate but remain a minority.
        # Existing step-free=False evidence is a strong signal not to invent a
        # curb ramp that would contradict the known connector state.
        curb_ramp = False if existing_step_free is False else (group_rng.random() < ramp_prob)
        if group_class == "steps_or_non_step_free":
            curb_ramp = False
        put("curb_ramp", bool(curb_ramp), "city_conditioned_curb_ramp_feature_prior")

    step_free = existing_step_free
    if step_free is None:
        step_free = group_class not in {"steps_or_non_step_free", "rough_or_non_step_free"}
        if curb_context and curb_ramp is False:
            step_free = False
        put("step_free", bool(step_free), "topology_and_feature_correlated_step_free_prior")

    # Store infrastructure illumination rather than hard-coding time of day.
    # Transition generation resolves this against the actual nuPlan request hour.
    lit = group_rng.random() < float(pri["lit_prob"])
    put("lighting", "lit" if lit else "unlit", "city_conditioned_infrastructure_lighting_prior")

    shelter_prob = float(pri["shelter_prob"])
    if entrance_connector:
        shelter_prob = min(0.65, shelter_prob + 0.18)
    put("shelter", bool(group_rng.random() < shelter_prob), "feature_correlated_shelter_prior")

    meta.update({
        "field_provenance": fp,
        "truth_mode": "hybrid_geometry_anchored_feature_correlated_simulated_accessibility_v2",
        "hybrid_simulation_group_id": group_key,
        "hybrid_simulation_group_class": group_class,
        "paper_claim_allowed": not any(str(v.get("kind")) == "simulated" for v in fp.values() if isinstance(v, Mapping)),
    })
    row["metadata"] = meta
    if any(str(v.get("kind")) == "simulated" for v in fp.values() if isinstance(v, Mapping)):
        src = str(row.get("source") or "prepared_accessibility_graph")
        if VERSION not in src:
            row["source"] = src + "+" + VERSION
    return kinds, group_key


def _copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        if dst.exists():
            dst.unlink()
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

    inp = Path(args.input_graph_dir)
    out = Path(args.output_graph_dir)
    if not inp.exists():
        raise FileNotFoundError(inp)
    out.mkdir(parents=True, exist_ok=True)
    allow = None
    if args.episode_allowlist:
        allow = {x.strip() for x in Path(args.episode_allowlist).read_text().splitlines() if x.strip() and not x.lstrip().startswith("#")}

    edge_files = sorted(inp.glob("*.edges.jsonl"))
    if not edge_files:
        raise RuntimeError(f"no *.edges.jsonl in {inp}")
    reports = Counter()
    field_kind_counts: Dict[str, Counter] = defaultdict(Counter)
    group_classes = Counter()
    group_class_edge_counts = Counter()
    group_class_by_key: Dict[str, str] = {}
    simulation_groups: set[str] = set()
    numeric_minmax: Dict[str, list[float | None]] = {k: [None, None] for k in ("width_m", "slope", "cross_slope")}
    categorical_counts: Dict[str, Counter] = {k: Counter() for k in ("surface", "curb_ramp", "step_free", "lighting", "shelter")}
    episodes = 0
    edges_total = 0
    simulated_edges = 0
    for ef in edge_files:
        eid = ef.name[:-len(".edges.jsonl")]
        if allow is not None and eid not in allow:
            continue
        rows = []
        episode_sim = False
        for raw in iter_jsonl(ef):
            row = dict(raw)
            before = {k: row.get(k) for k in FIELDS}
            _, group_key = _fill(row, city=args.city, split=args.split, episode_id=eid, base_seed=args.seed)
            simulation_groups.add(group_key)
            meta = row.get("metadata") or {}
            gclass = str(meta.get("hybrid_simulation_group_class") or "unknown")
            prev_class = group_class_by_key.setdefault(group_key, gclass)
            if prev_class != gclass:
                raise RuntimeError(f"simulation group {group_key} changed class within one overlay: {prev_class} != {gclass}")
            group_class_edge_counts[gclass] += 1
            for field, bounds in numeric_minmax.items():
                try:
                    val = float(row.get(field))
                    if math.isfinite(val):
                        bounds[0] = val if bounds[0] is None else min(float(bounds[0]), val)
                        bounds[1] = val if bounds[1] is None else max(float(bounds[1]), val)
                except Exception:
                    pass
            for field, counter in categorical_counts.items():
                if row.get(field) is not None:
                    counter[str(row.get(field))] += 1
            fp = meta.get("field_provenance") or {}
            for field in FIELDS:
                pv = fp.get(field) if isinstance(fp, Mapping) else None
                if isinstance(pv, Mapping):
                    field_kind_counts[field][str(pv.get("kind") or "unknown")] += 1
                if _is_blank(before.get(field)) and not _is_blank(row.get(field)):
                    reports[field] += 1
                    if isinstance(pv, Mapping) and str(pv.get("kind")) == "simulated":
                        episode_sim = True
            rows.append(row)
        write_jsonl(out / ef.name, rows)
        edges_total += len(rows)
        simulated_edges += sum(1 for r in rows if not bool((r.get("metadata") or {}).get("paper_claim_allowed", True)))
        nf = inp / f"{eid}.nodes.jsonl"
        if nf.exists():
            _copy_file(nf, out / nf.name)
        mf = inp / f"{eid}.meta.json"
        if mf.exists():
            try:
                payload = json.loads(mf.read_text())
                md = dict(payload.get("metadata") or {})
                md.update({
                    "truth_mode": "hybrid_geometry_anchored_feature_correlated_simulated_accessibility_v2",
                    "hybrid_overlay_version": VERSION,
                    "paper_claim_allowed": not episode_sim,
                })
                payload["metadata"] = md
                (out / mf.name).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
            except Exception:
                _copy_file(mf, out / mf.name)
        episodes += 1

    group_classes.update(group_class_by_key.values())
    report = {
        "status": "PASS",
        "version": VERSION,
        "city": args.city,
        "split": args.split,
        "episodes": episodes,
        "edges": edges_total,
        "simulation_group_count": len(simulation_groups),
        "simulation_group_class_counts": dict(group_classes),
        "simulation_group_class_edge_counts": dict(group_class_edge_counts),
        "numeric_field_ranges": {k: {"min": v[0], "max": v[1]} for k, v in numeric_minmax.items()},
        "categorical_field_counts": {k: dict(v) for k, v in categorical_counts.items()},
        "edges_with_any_simulated_field": simulated_edges,
        "filled_missing_field_counts": dict(reports),
        "field_provenance_kind_counts": {k: dict(v) for k, v in sorted(field_kind_counts.items())},
        "input_graph_dir": str(inp),
        "output_graph_dir": str(out),
        "interpretation": (
            "Real topology/geometry and every pre-existing observed/derived field are preserved. "
            "Missing static attributes are deterministic and correlated by mapped feature/connector across snapshots; "
            "they are benchmark scenario truth only, never measured city truth."
        ),
    }
    rp = Path(args.report_json)
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    print("HYBRID_ACCESSIBILITY_OVERLAY_CHECK=PASS")


if __name__ == "__main__":
    main()
