#!/usr/bin/env python
"""Package the small reports needed to audit a four-city hybrid benchmark run.

The bundle intentionally excludes the materialized dataset, accessibility graph
JSONLs, PUDO JSONLs, nuPlan DBs, and large CSV audit catalogs.  It is meant to be
uploaded after a build so a remote reviewer can determine pipeline identity,
rebuild coverage, per-city provenance, hybrid-ready retention, dataset semantic
quality, and merged train/val/test status without receiving the large corpus.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import zipfile
from pathlib import Path
from typing import Iterable

VERSION = "capplan_hybrid_review_bundle_v1_20260824"

# These reports collectively prove source/DB identity, base rebuild semantics,
# hybrid provenance, readiness selection, and final dataset labels.
REPORT_GLOBS = (
    "nuplan_db_cities.*.json",
    "nuplan_map_crs*.json",
    "georeference*.json",
    "dem*.json",
    "paper_site_catalog.*.json",
    "pudo_audit_status.json",
    "pudo_evidence_gap_manifest.json",
    "pudo_audit_evidence_recovery.json",
    "site_disjoint/*.json",
    "hybrid_graph.*.json",
    "hybrid_pudo.*.json",
    "hybrid_ready.*.json",
    "build/*/external_source_preflight.json",
    "build/*/service_layer*.json",
    "build/*/dataset_quality*.json",
    "build/*/dataset_diagnostics*.json",
    "build/*/hybrid_dataset_audit*.json",
    "build/*/merged_dataset_manifest.hybrid.json",
    "build/*/merged_validation_report.hybrid.json",
)

COMMAND_GLOBS = (
    "commands/pipeline_identity*.txt",
    "commands/realism_v4_*.log",
    "commands/hybrid_graph.*.log",
    "commands/hybrid_pudo.*.log",
    "commands/hybrid_ready.*.log",
    "commands/hybrid_build.*.log",
)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _collect(root: Path, patterns: Iterable[str]) -> list[Path]:
    selected: dict[str, Path] = {}
    for pattern in patterns:
        for path in root.glob(pattern):
            if path.is_file():
                selected[str(path.relative_to(root))] = path
    return [selected[k] for k in sorted(selected)]


def _copy_log_for_zip(path: Path, max_bytes: int) -> bytes:
    data = path.read_bytes()
    if max_bytes <= 0 or len(data) <= max_bytes:
        return data
    # Preserve both command/startup identity and final traceback/END marker.
    half = max(1, max_bytes // 2)
    marker = b"\n\n===== CAPPLAN REVIEW BUNDLE: MIDDLE OF LOG OMITTED =====\n\n"
    return data[:half] + marker + data[-half:]


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--reports_root", required=True)
    p.add_argument("--output_zip", required=True)
    p.add_argument("--max_command_log_bytes", type=int, default=2_000_000)
    args = p.parse_args()

    root = Path(args.reports_root).resolve()
    if not root.exists():
        raise FileNotFoundError(root)
    out = Path(args.output_zip).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    reports = _collect(root, REPORT_GLOBS)
    logs = _collect(root, COMMAND_GLOBS)
    selected = reports + [x for x in logs if x not in reports]
    manifest = {
        "status": "PASS",
        "version": VERSION,
        "reports_root": str(root),
        "selected_file_count": len(selected),
        "max_command_log_bytes": int(args.max_command_log_bytes),
        "files": [],
        "interpretation": (
            "Upload this ZIP together with the outer nohup/tee log if a run failed. "
            "The bundle excludes the full dataset and graph/PUDO materializations."
        ),
    }

    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        readme = (
            "CapPlan hybrid benchmark review bundle\n"
            f"version={VERSION}\n\n"
            "Contains only small reports and selected command logs. It excludes nuPlan DBs, "
            "materialized datasets, accessibility graph JSONLs and PUDO JSONLs.\n"
            "If the build failed, also upload the outer nohup/tee console log so the first fatal traceback is preserved.\n"
        )
        z.writestr("README.txt", readme)
        for path in selected:
            rel = str(path.relative_to(root))
            size = path.stat().st_size
            is_log = rel.startswith("commands/") and path.suffix == ".log"
            payload = _copy_log_for_zip(path, int(args.max_command_log_bytes)) if is_log else path.read_bytes()
            z.writestr(rel, payload)
            manifest["files"].append({
                "path": rel,
                "size_bytes": size,
                "packaged_bytes": len(payload),
                "sha256": _sha256(path),
                "mtime_ns": path.stat().st_mtime_ns,
                "truncated_in_bundle": bool(len(payload) != size),
            })
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    manifest_path = root / "hybrid_review_bundle_manifest.json"
    manifest["output_zip"] = str(out)
    manifest["output_zip_bytes"] = out.stat().st_size
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "PASS",
        "version": VERSION,
        "selected_file_count": len(selected),
        "output_zip": str(out),
        "output_zip_bytes": out.stat().st_size,
        "manifest": str(manifest_path),
    }, ensure_ascii=False, indent=2, sort_keys=True))
    print("HYBRID_REVIEW_BUNDLE=PASS")


if __name__ == "__main__":
    main()
