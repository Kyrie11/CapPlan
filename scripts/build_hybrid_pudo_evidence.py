#!/usr/bin/env python
"""Build an auditable hybrid PUDO truth layer for AbilityBench-AV.

The input remains geometry-anchored to real nuPlan/GIS candidates.  Observed or
source-prefilled values are never overwritten.  Only missing benchmark fields
are filled with deterministic physically-plausible simulated values.  Every
filled field carries explicit provenance and simulated values NEVER set the
paper_evidence_complete/paper_eligible flags.

This mode is intended for the paper's measured-or-simulated typed-resource
benchmark, not for claims about exact curb conditions or local law at a real
city location.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from capplan.utils.serialization import iter_jsonl, write_jsonl

VERSION = "abilitybench_hybrid_pudo_v2_20260823"
PHYSICAL_FIELDS = ("curb_height_m", "sidewalk_width_m", "deployment_clearance_m", "curb_ramp")
REQUIRED_FIELDS = (*PHYSICAL_FIELDS, "legal_stop", "legal_basis", "side", "adjacent_ped_node_id")


def _blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _as_float(v: Any) -> Optional[float]:
    if _blank(v):
        return None
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except Exception:
        return None


def _as_bool(v: Any) -> Optional[bool]:
    if isinstance(v, bool):
        return v
    if _blank(v):
        return None
    s = str(v).strip().lower()
    if s in {"1", "true", "yes", "y", "allowed", "legal"}:
        return True
    if s in {"0", "false", "no", "n", "disallowed", "illegal", "not_allowed"}:
        return False
    return None


def _seed(base: int, *parts: str) -> int:
    payload = "|".join([str(base), *map(str, parts)]).encode("utf-8")
    return int.from_bytes(hashlib.sha256(payload).digest()[:8], "big") & 0x7FFFFFFF


def _source_is_real(value: Any) -> bool:
    s = str(value or "").strip().lower()
    if not s:
        return False
    return not (s.startswith("synthetic") or "proxy" in s or "simulated" in s or s in {"unknown", "default", "toy", "mock"})


def _load_audit_map(path: Optional[str], split: str) -> Dict[str, Dict[str, Any]]:
    if not path:
        return {}
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    out: Dict[str, Dict[str, Any]] = {}
    with p.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            anchors = str(row.get(f"candidate_anchor_ids_{split}") or "").split(";")
            for aid in anchors:
                aid = aid.strip()
                if aid:
                    out[aid] = dict(row)
    return out


def _audit_value(row: Mapping[str, Any], field: str) -> Any:
    if field in {"curb_height_m", "sidewalk_width_m", "deployment_clearance_m"}:
        return _as_float(row.get(field))
    if field in {"curb_ramp", "legal_stop"}:
        return _as_bool(row.get(field))
    value = row.get(field)
    return None if _blank(value) else value


def _audit_provenance(audit: Mapping[str, Any], field: str) -> Optional[Dict[str, Any]]:
    value = _audit_value(audit, field)
    if value is None:
        return None
    if field in {"legal_stop", "legal_basis"}:
        source = audit.get("legal_stop_source") or audit.get("legal_basis_source")
        tier = audit.get("legal_stop_evidence_tier") or audit.get("legal_basis_evidence_tier")
        distance = audit.get("legal_stop_match_distance_m")
        as_of = audit.get("legal_stop_evidence_as_of")
        linkage = audit.get("legal_linkage_method")
    else:
        source = audit.get(f"{field}_source")
        tier = audit.get(f"{field}_evidence_tier")
        distance = audit.get(f"{field}_match_distance_m")
        as_of = audit.get(f"{field}_evidence_as_of")
        linkage = None
    # Existing human-entered rows may not have a source column.  Treat them as
    # observed only when the audit itself has reviewer/time evidence.
    human = bool(str(audit.get("auditor_id") or "").strip() and str(audit.get("observed_at") or "").strip())
    if not _source_is_real(source) and not human:
        return None
    return {
        "kind": "observed",
        "source": str(source or f"manual_audit:{audit.get('audit_id') or 'unknown'}"),
        "evidence_tier": str(tier or ("A_manual_observation" if human else "unknown")),
        "method": "source_prefill_or_manual_audit",
        "audit_id": audit.get("audit_id"),
        "match_distance_m": _as_float(distance),
        "evidence_as_of": as_of or audit.get("observed_at"),
        "linkage_method": linkage,
    }


def _base_provenance(row: Mapping[str, Any], field: str) -> Optional[Dict[str, Any]]:
    value = row.get(field)
    if field in {"curb_height_m", "sidewalk_width_m", "deployment_clearance_m"}:
        value = _as_float(value)
    elif field in {"curb_ramp", "legal_stop"}:
        value = _as_bool(value)
    if value is None or _blank(value):
        return None
    fp = row.get("field_provenance")
    if isinstance(fp, Mapping) and isinstance(fp.get(field), Mapping):
        p = dict(fp[field])
        if str(p.get("kind") or "").lower() in {"observed", "derived", "simulated"}:
            return p
    source = row.get(f"{field}_source") or (row.get("legal_stop_source") if field.startswith("legal") else None) or row.get("source")
    if field in {"legal_stop", "legal_basis"} and _source_is_real(source):
        linkage = str(row.get("legal_linkage_method") or "").lower()
        tier = str(row.get("evidence_tier") or row.get("legal_stop_evidence_tier") or "").lower()
        if linkage in {"authoritative_source_relation", "direct_feature_relation"} or tier.startswith("a_"):
            return {"kind": "observed", "source": str(source), "method": "explicit_authoritative_source_semantics", "linkage_method": linkage or None}
    if bool(row.get("paper_evidence_complete")) and _source_is_real(source):
        return {"kind": "observed", "source": str(source), "method": "preexisting_paper_audited_evidence"}
    # Geometry/lane-side and pedestrian binding are allowed as map-derived facts.
    if field in {"side", "adjacent_ped_node_id"} and value not in {None, "", "unknown"}:
        return {"kind": "derived", "source": str(source or "nuplan_gis_geometry"), "method": "geometry_or_topology_binding"}
    return None


def _sim_prov(city: str, split: str, episode_id: str, anchor_id: str, field: str, seed: int, profile: str, method: str) -> Dict[str, Any]:
    return {
        "kind": "simulated",
        "source": VERSION,
        "method": method,
        "seed": seed,
        "city_context": city,
        "split": split,
        "episode_id": episode_id,
        "anchor_id": anchor_id,
        "standard_profile": profile,
        "claim_scope": "benchmark_scenario_truth_not_real_site_ground_truth",
    }


def _profile(city: str) -> Dict[str, Any]:
    if city == "singapore":
        return {
            "name": "SG_physically_plausible_accessibility_prior_v1",
            "positive_sidewalk": (1.55, 3.20),
            "positive_clearance": (1.55, 2.20),
            "narrow_sidewalk": (0.85, 1.25),
            "narrow_clearance": (0.65, 1.05),
            "standard_reference": "Singapore BCA Code on Accessibility 2025 used as design-context reference; numeric simulation ranges are benchmark priors, not site measurements.",
        }
    return {
        "name": "US_accessible_loading_geometry_prior_v1",
        "positive_sidewalk": (1.70, 3.40),
        "positive_clearance": (1.55, 2.25),
        "narrow_sidewalk": (0.85, 1.30),
        "narrow_clearance": (0.65, 1.10),
        "standard_reference": "ADA/PROWAG passenger-loading access aisle 60 in (1.525 m) used as a plausibility lower-bound for accessible simulated loading scenarios; not a claim about the mapped site.",
    }


def _scenario_for(rng: random.Random) -> str:
    u = rng.random()
    if u < 0.45: return "accessible_loading"
    if u < 0.60: return "narrow_clearance"
    if u < 0.72: return "no_curb_ramp"
    if u < 0.80: return "high_curb"
    if u < 0.90: return "simulated_loading_prohibited"
    return "temporary_blockage"


def _fill_missing(row: MutableMapping[str, Any], prov: MutableMapping[str, Any], *, city: str, split: str, scenario: str, seed_value: int, profile: Mapping[str, Any]) -> None:
    rng = random.Random(seed_value)
    profile_name = str(profile["name"])

    def put(field: str, value: Any, method: str) -> None:
        if row.get(field) is None or _blank(row.get(field)) or (field == "side" and str(row.get(field)).lower() == "unknown"):
            row[field] = value
            prov[field] = _sim_prov(city, split, str(row.get("episode_id")), str(row.get("anchor_id") or row.get("pudo_id")), field, seed_value, profile_name, method)

    accessible_geometry = scenario not in {"narrow_clearance", "no_curb_ramp", "high_curb"}
    if scenario == "narrow_clearance":
        sw_lo, sw_hi = profile["narrow_sidewalk"]
        cl_lo, cl_hi = profile["narrow_clearance"]
    else:
        sw_lo, sw_hi = profile["positive_sidewalk"]
        cl_lo, cl_hi = profile["positive_clearance"]
    sidewalk = round(rng.uniform(float(sw_lo), float(sw_hi)), 3)
    clearance = round(min(sidewalk - 0.05, rng.uniform(float(cl_lo), float(cl_hi))), 3)
    clearance = max(0.40, clearance)

    if scenario in {"no_curb_ramp", "high_curb"}:
        ramp = False
        curb_h = round(rng.uniform(0.10 if scenario == "no_curb_ramp" else 0.14, 0.18 if scenario == "no_curb_ramp" else 0.22), 3)
    else:
        ramp = True
        curb_h = round(rng.uniform(0.0, 0.025), 3)

    put("sidewalk_width_m", sidewalk, "conditional_physical_prior")
    put("deployment_clearance_m", clearance, "conditional_clear_space_prior_bounded_by_sidewalk_width")
    put("curb_ramp", ramp, "conditional_curb_interface_prior")
    put("curb_height_m", curb_h, "conditional_curb_height_prior")
    if str(row.get("side") or "unknown").lower() == "unknown":
        put("side", "right", "benchmark_vehicle_side_context")

    if _as_bool(row.get("legal_stop")) is None:
        legal = scenario != "simulated_loading_prohibited"
        row["legal_stop"] = legal
        prov["legal_stop"] = _sim_prov(city, split, str(row.get("episode_id")), str(row.get("anchor_id") or row.get("pudo_id")), "legal_stop", seed_value, profile_name, "scenario_level_simulated_service_permission")
    if _blank(row.get("legal_basis")):
        legal = bool(_as_bool(row.get("legal_stop")))
        row["legal_basis"] = "SIMULATED_BENCHMARK_LOADING_PERMISSION" if legal else "SIMULATED_BENCHMARK_LOADING_PROHIBITION"
        prov["legal_basis"] = _sim_prov(city, split, str(row.get("episode_id")), str(row.get("anchor_id") or row.get("pudo_id")), "legal_basis", seed_value, profile_name, "scenario_level_simulated_legal_context_not_municipal_law")
        if "legal_stop" not in prov:
            prov["legal_stop"] = dict(prov["legal_basis"])
    if not _source_is_real(row.get("legal_stop_source")) and str(prov.get("legal_stop", {}).get("kind")) == "simulated":
        row["legal_stop_source"] = VERSION + ":simulated_service_permission"

    # Dynamic blockage is an explicitly allowed simulator/counterfactual field.
    if scenario == "temporary_blockage" and float(row.get("blockage_risk") or 0.0) < 0.85:
        row["blockage_risk"] = round(rng.uniform(0.88, 0.98), 3)
        prov["blockage_risk"] = _sim_prov(city, split, str(row.get("episode_id")), str(row.get("anchor_id") or row.get("pudo_id")), "blockage_risk", seed_value, profile_name, "controlled_dynamic_counterfactual")
    elif row.get("blockage_risk") is None:
        row["blockage_risk"] = round(rng.uniform(0.01, 0.08), 3)
        prov["blockage_risk"] = _sim_prov(city, split, str(row.get("episode_id")), str(row.get("anchor_id") or row.get("pudo_id")), "blockage_risk", seed_value, profile_name, "controlled_dynamic_baseline")

    # Keep map confidence about the geometry/map source separate from simulated
    # truth certainty. Do not inflate a low real-map confidence.
    if row.get("map_confidence") is None:
        row["map_confidence"] = 0.85
        prov["map_confidence"] = _sim_prov(city, split, str(row.get("episode_id")), str(row.get("anchor_id") or row.get("pudo_id")), "map_confidence", seed_value, profile_name, "benchmark_default_map_confidence")
    if row.get("dynamic_confidence") is None:
        row["dynamic_confidence"] = 0.95
        prov["dynamic_confidence"] = _sim_prov(city, split, str(row.get("episode_id")), str(row.get("anchor_id") or row.get("pudo_id")), "dynamic_confidence", seed_value, profile_name, "simulator_state_confidence")


def _copy_observed_from_audit(row: MutableMapping[str, Any], audit: Mapping[str, Any], prov: MutableMapping[str, Any]) -> None:
    for field in (*PHYSICAL_FIELDS, "legal_stop", "legal_basis"):
        p = _audit_provenance(audit, field)
        if p is None:
            continue
        value = _audit_value(audit, field)
        if value is None:
            continue
        row[field] = value
        prov[field] = p
        if field == "legal_stop" and audit.get("legal_stop_source"):
            row["legal_stop_source"] = audit.get("legal_stop_source")


def _normalize_base(row: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(row)
    for field in ("curb_height_m", "sidewalk_width_m", "deployment_clearance_m"):
        out[field] = _as_float(out.get(field))
    for field in ("legal_stop", "curb_ramp"):
        val = _as_bool(out.get(field))
        if val is not None:
            out[field] = val
    out["blockage_risk"] = float(out.get("blockage_risk") or 0.0)
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input_pudo_jsonl", required=True)
    p.add_argument("--output_pudo_jsonl", required=True)
    p.add_argument("--city", required=True, choices=["boston", "pittsburgh", "vegas", "singapore"])
    p.add_argument("--split", required=True, choices=["train", "val", "test"])
    p.add_argument("--audit_worklist_csv", default=None, help="Optional source-prefilled site worklist from prepare_pudo_audit_worklist.py")
    p.add_argument("--seed", type=int, default=20260822)
    p.add_argument("--min_positive_per_episode", type=int, default=2)
    p.add_argument("--report_json", required=True)
    args = p.parse_args()

    base_rows = [_normalize_base(dict(r)) for r in iter_jsonl(args.input_pudo_jsonl)]
    if not base_rows:
        raise RuntimeError("input PUDO JSONL is empty")
    audit_map = _load_audit_map(args.audit_worklist_csv, args.split)
    profile = _profile(args.city)
    by_episode: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in base_rows:
        eid = str(row.get("episode_id") or "")
        aid = str(row.get("anchor_id") or row.get("pudo_id") or "")
        if eid and aid:
            by_episode[eid].append(row)

    output: List[Dict[str, Any]] = []
    field_kinds: Dict[str, Counter] = defaultdict(Counter)
    scenarios = Counter()
    complete_eps = 0
    eligible_eps = 0
    insufficient_eps: List[Dict[str, Any]] = []
    simulated_rows = 0

    for eid, rows in sorted(by_episode.items()):
        # First apply observed/audited evidence so forced-positive scenario slots
        # never overwrite a real negative legality observation.
        prepared: List[tuple[Dict[str, Any], Dict[str, Any]]] = []
        for row in sorted(rows, key=lambda r: str(r.get("anchor_id") or r.get("pudo_id"))):
            aid = str(row.get("anchor_id") or row.get("pudo_id"))
            prov: Dict[str, Any] = dict(row.get("field_provenance") or {}) if isinstance(row.get("field_provenance"), Mapping) else {}
            for field in REQUIRED_FIELDS:
                if field in prov:
                    continue
                bp = _base_provenance(row, field)
                if bp is not None:
                    prov[field] = bp
            # Bootstrap PUDO generation uses legal_stop=False as fail-closed when
            # legality is unknown.  In hybrid mode that boolean is not allowed to
            # masquerade as an observed prohibition: if there is no legality
            # provenance, restore UNKNOWN so the explicit benchmark-scenario
            # legality overlay can fill it.
            if "legal_stop" not in prov:
                row["legal_stop"] = None
                if str(row.get("legal_basis") or "").lower().startswith(("unknown", "no_", "bootstrap")):
                    row["legal_basis"] = None
            audit = audit_map.get(aid)
            if audit:
                _copy_observed_from_audit(row, audit, prov)
                row["hybrid_site_audit_id"] = audit.get("audit_id")
                if audit.get("entrance_candidate_id"):
                    row["entrance_candidate_id"] = audit.get("entrance_candidate_id")
                    row["entrance_candidate_source"] = audit.get("entrance_candidate_source")
                    row["entrance_candidate_match_distance_m"] = _as_float(audit.get("entrance_candidate_match_distance_m"))
            prepared.append((row, prov))

        forceable = []
        for idx, (row, _prov) in enumerate(prepared):
            legal = _as_bool(row.get("legal_stop"))
            legal_prov = _prov.get("legal_stop") if isinstance(_prov.get("legal_stop"), Mapping) else {}
            observed_illegal = legal is False and str(legal_prov.get("kind")) == "observed"
            if not observed_illegal and row.get("adjacent_ped_node_id"):
                # Prefer candidates that are already dynamically available.
                # A previous implementation selected the first N anchors and
                # could force an ``accessible_loading`` scenario onto a row
                # whose observed/preexisting blockage_risk was >= 0.85.  The
                # fill logic correctly refuses to overwrite that real dynamic
                # fact, so the supposedly forced-positive row stayed
                # ineligible.  Stable sorting by availability fixes that
                # avoidable loss without modifying any observed value.
                try:
                    blockage = float(row.get("blockage_risk") or 0.0)
                except Exception:
                    blockage = 1.0
                forceable.append((blockage >= 0.85, blockage, idx))
        forceable.sort()
        forced = {idx for _blocked, _risk, idx in forceable[: max(0, args.min_positive_per_episode)]}

        eligible_count = 0
        complete_count = 0
        for idx, (row, prov) in enumerate(prepared):
            aid = str(row.get("anchor_id") or row.get("pudo_id"))
            s = _seed(args.seed, args.city, args.split, eid, aid)
            rng = random.Random(s)
            scenario = "accessible_loading" if idx in forced else _scenario_for(rng)
            _fill_missing(row, prov, city=args.city, split=args.split, scenario=scenario, seed_value=s, profile=profile)

            # Consistency repair is allowed only for simulated fields. Observed
            # facts are never modified to make an episode pass.
            sw = _as_float(row.get("sidewalk_width_m"))
            cl = _as_float(row.get("deployment_clearance_m"))
            if sw is not None and cl is not None and cl > sw:
                if str((prov.get("deployment_clearance_m") or {}).get("kind")) == "simulated":
                    row["deployment_clearance_m"] = max(0.40, round(sw - 0.05, 3))
                    prov["deployment_clearance_m"]["consistency_clamp"] = "clearance<=sidewalk_width"

            simulated = any(isinstance(v, Mapping) and str(v.get("kind")) == "simulated" for v in prov.values())
            if simulated:
                simulated_rows += 1
                row["paper_evidence_complete"] = False
                row["paper_eligible"] = False
                row["paper_claim_allowed"] = False
            else:
                row["paper_claim_allowed"] = bool(row.get("paper_claim_allowed", True))

            missing = []
            for field in REQUIRED_FIELDS:
                val = row.get(field)
                if field == "legal_stop":
                    if _as_bool(val) is None: missing.append(field)
                elif field in {"curb_height_m", "sidewalk_width_m", "deployment_clearance_m"}:
                    if _as_float(val) is None: missing.append(field)
                elif field == "curb_ramp":
                    if _as_bool(val) is None: missing.append(field)
                elif _blank(val) or (field == "side" and str(val).lower() == "unknown"):
                    missing.append(field)
                if field not in prov:
                    missing.append(field + ":provenance")
            complete = not missing
            legal = bool(_as_bool(row.get("legal_stop")))
            eligible = complete and legal and float(row.get("blockage_risk") or 0.0) < 0.85 and float(row.get("deployment_clearance_m") or 0.0) > 0
            row.update({
                "truth_mode": "hybrid_geometry_anchored_simulated_interface_v1",
                "evidence_kind": "mixed" if simulated and any(isinstance(v, Mapping) and str(v.get("kind")) in {"observed", "derived"} for v in prov.values()) else ("simulated" if simulated else "observed_or_derived"),
                "field_provenance": prov,
                "hybrid_evidence_complete": complete,
                "hybrid_eligible": eligible,
                "deployment_clearance_semantics": "available_environment_clear_space",
                "hybrid_scenario_class": scenario,
                "hybrid_seed": s,
                "hybrid_standard_profile": profile["name"],
                "hybrid_missing_fields": sorted(set(missing)),
                "evidence_status": "hybrid_ready" if complete else "hybrid_incomplete",
                "source": str(row.get("source") or "geometry_anchored_pudo") + ("+" + VERSION if simulated else ""),
            })
            for field, pv in prov.items():
                if isinstance(pv, Mapping):
                    field_kinds[field][str(pv.get("kind") or "unknown")] += 1
            scenarios[scenario] += 1
            complete_count += int(complete)
            eligible_count += int(eligible)
            output.append(row)
        complete_eps += int(complete_count >= args.min_positive_per_episode)
        eligible_eps += int(eligible_count >= args.min_positive_per_episode)
        if eligible_count < args.min_positive_per_episode:
            insufficient_eps.append({"episode_id": eid, "hybrid_eligible_pudos": eligible_count, "pudos": len(prepared)})

    out = Path(args.output_pudo_jsonl); out.parent.mkdir(parents=True, exist_ok=True)
    write_jsonl(out, output)
    report = {
        "status": "PASS" if not insufficient_eps else "PARTIAL",
        "version": VERSION,
        "city": args.city,
        "split": args.split,
        "input_rows": len(base_rows),
        "output_rows": len(output),
        "episodes": len(by_episode),
        "episodes_with_min_complete_pudos": complete_eps,
        "episodes_with_min_hybrid_eligible_pudos": eligible_eps,
        "min_positive_per_episode": args.min_positive_per_episode,
        "simulated_overlay_rows": simulated_rows,
        "scenario_class_counts": dict(scenarios),
        "field_provenance_kind_counts": {k: dict(v) for k, v in sorted(field_kinds.items())},
        "insufficient_episode_count": len(insufficient_eps),
        "insufficient_episode_examples": insufficient_eps[:50],
        "standard_profile": profile,
        "paper_claim_allowed_for_simulated_rows": False,
        "interpretation": "Benchmark-ready hybrid truth may contain simulated typed-resource values. Simulated fields are scenario truth only and must not be reported as measured city ground truth.",
        "output_pudo_jsonl": str(out),
    }
    rp = Path(args.report_json); rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    print("HYBRID_PUDO_EVIDENCE_CHECK=" + ("PASS" if report["status"] == "PASS" else "PARTIAL"))


if __name__ == "__main__":
    main()
