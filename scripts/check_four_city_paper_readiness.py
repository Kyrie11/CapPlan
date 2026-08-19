#!/usr/bin/env python
"""Produce one compact, uploadable QA report for the four-city paper build.

The checker never fabricates missing artifacts.  It verifies path provenance,
reviewed physical/legal/entrance evidence, fleet-interface completeness,
full-split PUDO eligibility, site-disjoint allowlists, and (when present)
canonical-dataset split leakage / label health.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capplan.data.external_validation import inspect_source

CITIES = ("boston", "pittsburgh", "vegas", "singapore")
SPLITS = ("train", "val", "test")
CORE = ("curb_height_m", "sidewalk_width_m", "deployment_clearance_m")
FLEET_REQUIRED = {
    "door_side", "ramp", "lift", "low_floor", "door_width_m",
    "deployment_clearance_m", "notification_modes", "dwell_time_s", "kneeling",
}
PLACEHOLDER = re.compile(r"(?:TODO|TBD|REVIEW|REPLACE|VERIFY|CHANGEME|PLACEHOLDER)", re.I)


def read_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    if not path.exists():
        return []
    def gen():
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    obj = json.loads(line)
                    if isinstance(obj, dict):
                        yield obj
    return gen()


def _trusted(row: Mapping[str, Any]) -> bool:
    tier = str(row.get("evidence_tier") or "").lower()
    src = str(row.get("source") or "").lower()
    return bool(row.get("authoritative") or row.get("audited") or tier.startswith("a_") or "reviewed_audit:" in src or "manual_audit:" in src)


def _pudo_stats(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"exists": False, "path": str(path)}
    episodes: Dict[str, Dict[str, int]] = defaultdict(lambda: {"rows": 0, "paper_eligible": 0, "evidence_complete_negative": 0})
    statuses = Counter()
    rows = eligible = complete = 0
    for r in read_jsonl(path):
        rows += 1
        eid = str(r.get("episode_id") or "")
        episodes[eid]["rows"] += 1
        pe = bool(r.get("paper_eligible")); ec = bool(r.get("paper_evidence_complete"))
        eligible += int(pe); complete += int(ec)
        episodes[eid]["paper_eligible"] += int(pe)
        episodes[eid]["evidence_complete_negative"] += int(ec and not pe)
        statuses[str(r.get("evidence_status") or "unknown")] += 1
    ep2 = sum(1 for x in episodes.values() if x["paper_eligible"] >= 2)
    return {
        "exists": True, "path": str(path), "rows": rows, "episodes": len(episodes),
        "paper_eligible_rows": eligible, "paper_evidence_complete_rows": complete,
        "episodes_with_at_least_2_paper_eligible": ep2,
        "episode_paper_eligibility_rate": ep2 / max(len(episodes), 1),
        "top_evidence_status": statuses.most_common(10),
    }


def _fleet_stats(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"exists": False, "path": str(path)}
    rows = list(read_jsonl(path))
    bad = []
    for r in rows:
        meta = r.get("metadata") if isinstance(r.get("metadata"), dict) else {}
        source = str(meta.get("source") or r.get("source") or "").lower()
        provided = {str(x) for x in (meta.get("provided_interface_fields") or [])}
        if not provided:
            provided = {k for k in FLEET_REQUIRED if k in r and r.get(k) is not None}
        missing = sorted(FLEET_REQUIRED - provided)
        source_bad = (not source or any(x in source for x in ("example", "synthetic", "proxy", "unknown", "toy", "mock")))
        if missing or source_bad:
            bad.append({"vehicle_id": r.get("vehicle_id"), "source": source, "missing": missing})
    return {"exists": True, "path": str(path), "vehicles": len(rows), "paper_ready_vehicles": len(rows)-len(bad), "bad_examples": bad[:10]}


def _provenance_stats(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"exists": False, "path": str(path)}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"exists": True, "path": str(path), "parse_error": f"{type(exc).__name__}:{exc}"}
    sources = payload.get("sources") or []
    blockers = []
    for s in sources:
        role = str(s.get("role") or "unknown")
        for field in ("source_url", "license", "retrieved_at"):
            v = str(s.get(field) or "")
            if not v or PLACEHOLDER.search(v):
                blockers.append(f"{role}:{field}")
        files = s.get("files") or []
        if not files or any(not str(f.get("sha256") or "") for f in files if isinstance(f, dict)):
            blockers.append(f"{role}:files_or_sha256")
    return {"exists": True, "path": str(path), "sources": len(sources), "blockers": sorted(set(blockers)), "ready": not blockers}


def _collect_strings(obj: Any) -> Iterable[str]:
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _collect_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _collect_strings(v)


def _stale_report_paths(reports_root: Path, external_root: Path) -> List[Dict[str, str]]:
    out = []
    if not reports_root.exists():
        return out
    project_data_fragment = "/home/senzeyu2/code/CapPlan/data/"
    for p in sorted(reports_root.rglob("*.json")):
        # Archived pre-migration reports are retained intentionally for audit
        # history.  They must not make the *current* paper snapshot fail just
        # because they still document the old project-local data root.
        try:
            rel = p.relative_to(reports_root)
        except ValueError:
            rel = p
        if "archive" in rel.parts:
            continue
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        for s in _collect_strings(payload):
            if project_data_fragment in s and str(external_root) not in s:
                out.append({"report": str(p), "stale_path": s})
                if len(out) >= 100:
                    return out
    return out


def _allowlist_stats(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {"exists": False, "path": str(path), "episodes": 0, "duplicates": 0}
    ids = [x.strip() for x in path.read_text(encoding="utf-8").splitlines() if x.strip() and not x.lstrip().startswith("#")]
    return {"exists": True, "path": str(path), "episodes": len(set(ids)), "duplicates": len(ids)-len(set(ids))}




def _load_report_summary(path: Path, drop_keys: tuple[str, ...] = ("episodes", "cross_split_clusters", "residual_cross_split_conflicts", "exclude_episode_ids")) -> Dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"status": "FAIL", "parse_error": f"{type(exc).__name__}:{exc}", "path": str(path)}
    if not isinstance(payload, dict):
        return {"status": "FAIL", "parse_error": "report_not_object", "path": str(path)}
    return {k: v for k, v in payload.items() if k not in drop_keys}

def _nuplan_db_split_overlap(reports_root: Path) -> Dict[str, Any]:
    """Verify upstream DB-log identity disjointness across train/val/test reports."""
    split_sets: Dict[str, set[str]] = {}
    missing: List[str] = []
    for split in SPLITS:
        rp = reports_root / f"nuplan_db_cities.{split}.json"
        if not rp.exists():
            missing.append(split)
            continue
        try:
            payload = json.loads(rp.read_text(encoding="utf-8"))
        except Exception:
            missing.append(split)
            continue
        names = set()
        for row in payload.get("dbs") or []:
            if isinstance(row, dict) and row.get("db"):
                names.add(Path(str(row["db"])).name)
        split_sets[split] = names
    overlaps: Dict[str, List[str]] = {}
    for i, a in enumerate(SPLITS):
        for b in SPLITS[i + 1:]:
            if a in split_sets and b in split_sets:
                x = sorted(split_sets[a] & split_sets[b])
                if x:
                    overlaps[f"{a}__{b}"] = x
    return {
        "status": "PASS" if not missing and not overlaps else "FAIL",
        "missing_split_reports": missing,
        "db_counts": {k: len(v) for k, v in split_sets.items()},
        "overlap_counts": {k: len(v) for k, v in overlaps.items()},
        "overlap_examples": {k: v[:20] for k, v in overlaps.items()},
        "unique_db_basenames": len(set().union(*split_sets.values())) if split_sets else 0,
    }

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", required=True)
    p.add_argument("--reports_root", default=None)
    p.add_argument("--output_json", required=True)
    p.add_argument("--require_allowlists", action="store_true", help="Require non-empty train/val/test final paper allowlists and a zero-residual physical-anchor leakage report.")
    p.add_argument("--strict", action="store_true")
    args = p.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8")) or {}
    external_root = Path(str(cfg.get("external_root"))).expanduser()
    outputs_root = Path(str(cfg.get("outputs_root"))).expanduser()
    reports_root = Path(args.reports_root).expanduser() if args.reports_root else external_root / "reports"

    cities: Dict[str, Any] = {}
    blockers: List[str] = []
    for city in CITIES:
        inv = inspect_source(external_root / "normalized" / "curb_inventory" / f"{city}.jsonl", role="curb_inventory")
        reg = inspect_source(external_root / "normalized" / "curb_regulations" / f"{city}.jsonl", role="curb_regulations")
        ent = inspect_source(external_root / "normalized" / "entrances" / f"{city}.geojson", role="entrances")
        audit = inspect_source(external_root / "audits" / city / "manual_audit_manifest.jsonl", role="manual_audit")
        site_report_path = reports_root / f"paper_site_catalog.{city}.json"
        site_report = json.loads(site_report_path.read_text(encoding="utf-8")) if site_report_path.exists() else None
        anchor_leakage_path = external_root / "audits" / city / "paper_anchor_site_disjoint_exclusions.json"
        anchor_leakage = _load_report_summary(anchor_leakage_path)
        pstats = {split: _pudo_stats(outputs_root / "prepared" / split / "pudo" / f"{city}.jsonl") for split in SPLITS}
        allow = {split: _allowlist_stats(external_root / "audits" / city / "paper_allowlists" / f"{split}.txt") for split in SPLITS}
        selection = {split: _load_report_summary(reports_root / f"paper_select.{city}.{split}.json") for split in SPLITS}
        selection_pre_site = {split: _load_report_summary(reports_root / f"paper_select_pre_site.{city}.{split}.json") for split in SPLITS}
        audit_pipeline = {
            "prefill": _load_report_summary(reports_root / f"pudo_audit_prefill.{city}.json"),
            "classify": _load_report_summary(reports_root / f"pudo_audit_classify.{city}.json"),
            "source_review": _load_report_summary(reports_root / f"pudo_audit_source_review.{city}.json"),
            "manual_import": _load_report_summary(reports_root / f"manual_audit_layers.manual.{city}.json"),
            "source_import": _load_report_summary(reports_root / f"manual_audit_layers.source.{city}.json"),
        }
        cities[city] = {
            "curb_inventory": inv.to_dict(), "curb_regulations": reg.to_dict(), "entrances": ent.to_dict(), "manual_audit": audit.to_dict(),
            "site_catalog": site_report, "audit_pipeline": audit_pipeline, "paper_anchor_leakage": anchor_leakage,
            "paper_selection_pre_site": selection_pre_site, "paper_selection": selection, "pudo": pstats, "paper_allowlists": allow,
        }
        if int(inv.role_stats.get("authoritative_or_audited_complete_core_records", 0)) <= 0:
            blockers.append(f"{city}:no_authoritative_complete_core_curb_records")
        if int(reg.role_stats.get("authoritative_or_audited_legality_records", 0)) <= 0:
            blockers.append(f"{city}:no_authoritative_legality_records")
        if int(ent.role_stats.get("authoritative_or_audited_nonproxy_entrance_points", 0)) < 2:
            blockers.append(f"{city}:fewer_than_2_trusted_entrances")
        if not audit.valid:
            blockers.append(f"{city}:manual_audit_manifest_not_ready")
        for split in SPLITS:
            if not pstats[split].get("exists"):
                blockers.append(f"{city}:{split}:missing_full_pudo_evidence")
            if allow[split]["duplicates"]:
                blockers.append(f"{city}:{split}:duplicate_allowlist_ids")
            if args.require_allowlists and (not allow[split]["exists"] or allow[split]["episodes"] <= 0):
                blockers.append(f"{city}:{split}:missing_or_empty_final_paper_allowlist")
        if args.require_allowlists:
            if not anchor_leakage:
                blockers.append(f"{city}:missing_paper_anchor_leakage_report")
            elif anchor_leakage.get("status") != "PASS":
                blockers.append(f"{city}:residual_physical_anchor_split_leakage")

    nuplan_db_split_disjoint = _nuplan_db_split_overlap(reports_root)
    if nuplan_db_split_disjoint.get("status") != "PASS":
        blockers.append("nuplan_db_files_overlap_or_split_reports_missing")

    fleet = _fleet_stats(Path(str(cfg.get("fleet_jsonl"))).expanduser())
    if not fleet.get("exists") or fleet.get("paper_ready_vehicles", 0) <= 0:
        blockers.append("verified_fleet_interface_not_ready")
    provenance = {city: _provenance_stats(external_root / "manifests" / f"{city}.json") for city in CITIES}
    for city, stat in provenance.items():
        if not stat.get("ready"):
            blockers.append(f"{city}:provenance_not_ready")

    stale = _stale_report_paths(reports_root, external_root)
    if stale:
        blockers.append("reports_contain_pre_data0_stale_paths")

    payload = {
        "schema_version": "1.0", "status": "PASS" if not blockers else "FAIL",
        "config": str(Path(args.config).resolve()), "external_root": str(external_root), "outputs_root": str(outputs_root), "reports_root": str(reports_root),
        "cities": cities, "nuplan_db_split_disjoint": nuplan_db_split_disjoint, "fleet": fleet, "provenance": provenance,
        "stale_report_paths": stale,
        "blockers": sorted(set(blockers)),
        "acceptance_notes": [
            "PASS requires evidence provenance, not merely non-null numeric fields.",
            "A non-empty paper allowlist is not required before audit completion; use --require_allowlists for the final publication snapshot.",
            "Final paper main-set physical PUDO/entrance anchors must have zero residual train/val/test cross-split conflicts; official nuPlan DB split membership remains unchanged.",
        ],
    }
    out = Path(args.output_json); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": payload["status"], "blockers": payload["blockers"], "output": str(out)}, indent=2))
    print("FOUR_CITY_PAPER_READINESS_CHECK=" + payload["status"])
    if args.strict and blockers:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
