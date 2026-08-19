#!/usr/bin/env python
"""Create a compact QA bundle for remote review of a CapPlan paper build.

The bundle intentionally excludes nuPlan DBs, raster/GIS payloads, graph JSONLs,
and full per-episode paper-selection reports.  The compact readiness JSON already
summarizes those artifacts.  Only provenance manifests, allowlists and small QA /
model / evaluation reports are included by default.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path
from typing import Iterable

import yaml


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _iter_unique(paths: Iterable[Path]) -> list[Path]:
    seen = set(); out = []
    for p in paths:
        if not p.is_file():
            continue
        rp = p.resolve()
        if rp in seen:
            continue
        seen.add(rp); out.append(p)
    return sorted(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", required=True)
    ap.add_argument("--reports_root", default=None)
    ap.add_argument("--output_zip", required=True)
    ap.add_argument("--include_review_csvs", action="store_true", help="Also include source-review and unresolved audit CSVs; off by default because they can be large.")
    args = ap.parse_args()

    cfg_path = Path(args.config).expanduser().resolve()
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
    ext = Path(str(cfg["external_root"])).expanduser()
    reports = Path(args.reports_root).expanduser() if args.reports_root else ext / "reports"
    out = Path(args.output_zip).expanduser()

    files: list[Path] = [cfg_path]
    root_report_patterns = [
        "four_city_paper_readiness*.json",
        "recommended_public_sources.json", "external.bootstrap.json", "external.paper.json",
        "georeference_spatial_alignment.json", "dem_tiles_*.json", "local_dem_sampling_*.json",
        "nuplan_db_cities.*.json", "paper_site_catalog.*.json", "pudo_audit_prefill.*.json",
        "pudo_audit_classify.*.json", "pudo_audit_source_review.*.json",
        "manual_audit_layers.*.json", "dataset_quality.paper.*.json",
    ]
    for pat in root_report_patterns:
        files.extend(reports.glob(pat))
    for sub in ("model", "eval"):
        files.extend((reports / sub).rglob("*.json"))
        files.extend((reports / sub).rglob("*.jsonl"))

    files.extend((ext / "manifests").glob("*.json"))
    files.append(ext / "provenance_registry.yaml")
    for city in ("boston", "pittsburgh", "vegas", "singapore"):
        ad = ext / "audits" / city
        files.extend((ad / "paper_allowlists").glob("*.txt"))
        files.append(ad / "manual_audit_manifest.jsonl")
        if args.include_review_csvs:
            for name in ("source_complete_review_candidates.csv", "pudo_audit_unresolved.csv", "pudo_audit_source_accepted.csv"):
                files.append(ad / name)

    files = _iter_unique(files)
    out.parent.mkdir(parents=True, exist_ok=True)
    manifest = {"schema_version": "1.0", "files": []}

    # Use stable logical roots instead of leaking arbitrary host prefixes.
    def arcname(p: Path) -> str:
        rp = p.resolve()
        if rp == cfg_path:
            return "config/" + cfg_path.name
        try:
            return "reports/" + str(rp.relative_to(reports.resolve()))
        except ValueError:
            pass
        try:
            return "external/" + str(rp.relative_to(ext.resolve()))
        except ValueError:
            return "other/" + p.name

    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for p in files:
            name = arcname(p)
            zf.write(p, name)
            manifest["files"].append({"path": name, "size_bytes": p.stat().st_size, "sha256": _sha256(p)})
        zf.writestr("QA_BUNDLE_MANIFEST.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    print(json.dumps({
        "status": "PASS", "output_zip": str(out), "files": len(files),
        "size_bytes": out.stat().st_size,
        "note": "Upload this bundle together with the paper/model result tables when asking for a build audit. Raw DB/GIS/graph data are intentionally excluded.",
    }, indent=2, sort_keys=True))
    print("CAPPLAN_QA_BUNDLE_CHECK=PASS")


if __name__ == "__main__":
    main()
