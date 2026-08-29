#!/usr/bin/env python
"""Package and *validate* the reports needed to audit a hybrid benchmark run.

Unlike the original packager, this script does not equate "a ZIP was created"
with "the benchmark build passed".  It anchors freshness to the latest realism
pipeline identity file, checks expected artifact versions, requires all per-city
and merged semantic audits, and records stale/missing/failed artifacts in the
manifest.  The ZIP is still produced for INCOMPLETE/FAIL states so a remote
reviewer receives enough evidence to diagnose the run.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import zipfile
from pathlib import Path
from typing import Any, Iterable

VERSION = "capplan_hybrid_review_bundle_v5_hotfix2_20260828"
EXPECTED_GRAPH_VERSION = "abilitybench_hybrid_accessibility_v3_20260825"
EXPECTED_PUDO_VERSION = "abilitybench_hybrid_pudo_v7_20260828"
EXPECTED_READY_VERSION = "abilitybench_hybrid_ready_allowlist_v1_20260823"
EXPECTED_AUDIT_VERSION = "abilitybench_hybrid_dataset_audit_v4_20260825"
EXPECTED_SITE_AUDIT_VERSION = "abilitybench_hybrid_site_consistency_v2_20260825"
EXPECTED_PIPELINE_VERSION = "abilitybench_data0_realism_v4_reviewfix5_hotfix2_20260828"
SPLITS = ("train", "val", "test")
CITIES = ("boston", "pittsburgh", "vegas", "singapore")

REPORT_GLOBS = (
    "nuplan_db_cities.*.json",
    "nuplan_map_crs*.json",
    "georeference*.json",
    "dem*.json",
    "paper_site_catalog.*.json",
    "pudo_audit_*.json",
    "pudo_evidence_gap_manifest.json",
    "site_disjoint/*.json",
    "hybrid_graph.*.json",
    "hybrid_pudo.*.json",
    "hybrid_ready.*.json",
    "hybrid_site_consistency.json",
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
    "commands/manual_pipeline_identity*.txt",
    "commands/hybrid_run_context*.json",
    "commands/hybrid_run_context*.log",
    "commands/reviewfix5_dataset_fix.sha256",
    "commands/hybrid_realism*.log",
    "commands/realism_v4_*.log",
    "commands/paper_site_catalog.*.log",
    "commands/pudo_audit_evidence_recovery.log",
    "commands/pudo_audit_prefill.*.log",
    "commands/pudo_audit_classify.*.log",
    "commands/pudo_audit_triage.*.log",
    "commands/pudo_audit_status.log",
    "commands/pudo_evidence_gap_manifest.log",
    "commands/site_disjoint_eval.*.log",
    "commands/hybrid_graph.*.log",
    "commands/hybrid_pudo.*.log",
    "commands/hybrid_ready.*.log",
    "commands/hybrid_build.*.log",
    "commands/hybrid_site_consistency.log",
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
    half = max(1, max_bytes // 2)
    marker = b"\n\n===== CAPPLAN REVIEW BUNDLE: MIDDLE OF LOG OMITTED =====\n\n"
    return data[:half] + marker + data[-half:]


def _json(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _latest_identity(root: Path) -> Path | None:
    candidates = [p for p in root.glob("commands/pipeline_identity*.txt") if p.is_file()]
    return max(candidates, key=lambda p: p.stat().st_mtime_ns) if candidates else None


def _identity_pipeline_version(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return None
    m = re.search(r"^CAPPLAN_PIPELINE_VERSION=(.+)$", text, flags=re.MULTILINE)
    return m.group(1).strip() if m else None


def _latest_run_context(root: Path) -> Path | None:
    # Dataset-only resumptions intentionally reuse expensive reviewfix3 graph/PUDO
    # artifacts.  Their old run context is upstream lineage, not the freshness
    # anchor for newly generated labels/audits.  Require the explicit dataset
    # context so an old reviewfix3 audit cannot masquerade as a current result.
    p = root / "commands/hybrid_run_context.reviewfix5_dataset.json"
    return p if p.is_file() else None


def _is_reused_upstream(rel: str) -> bool:
    return rel.startswith("hybrid_graph.")


def _required_paths(root: Path) -> list[tuple[Path, str | None]]:
    req: list[tuple[Path, str | None]] = []
    for split in SPLITS:
        for city in CITIES:
            req.append((root / f"hybrid_graph.{split}.{city}.json", EXPECTED_GRAPH_VERSION))
            req.append((root / f"hybrid_pudo.{split}.{city}.json", EXPECTED_PUDO_VERSION))
            req.append((root / f"hybrid_ready.{split}.{city}.json", EXPECTED_READY_VERSION))
            req.append((root / f"build/{split}/dataset_quality.{city}.json", None))
            req.append((root / f"build/{split}/hybrid_dataset_audit.{city}.json", EXPECTED_AUDIT_VERSION))
        req.append((root / f"build/{split}/hybrid_dataset_audit.merged.json", EXPECTED_AUDIT_VERSION))
        req.append((root / f"build/{split}/merged_dataset_manifest.hybrid.json", None))
        req.append((root / f"build/{split}/merged_validation_report.hybrid.json", None))
        req.append((root / f"commands/hybrid_build.{split}.log", None))
    req.append((root / "hybrid_site_consistency.json", EXPECTED_SITE_AUDIT_VERSION))
    req.append((root / "commands/hybrid_run_context.reviewfix5_dataset.json", None))
    req.append((root / "commands/reviewfix5_dataset_fix.sha256", None))
    return req


def _log_rc(path: Path) -> int | None:
    try:
        tail = path.read_text(encoding="utf-8", errors="replace")[-20000:]
    except Exception:
        return None
    matches = re.findall(r"END rc=(\d+)", tail)
    return int(matches[-1]) if matches else None


def _iso_from_ns(ns: int | None) -> str | None:
    if ns is None:
        return None
    return dt.datetime.fromtimestamp(ns / 1e9, tz=dt.timezone.utc).isoformat()


def _assess(root: Path) -> dict[str, Any]:
    identity = _latest_identity(root)
    identity_version = _identity_pipeline_version(identity)
    run_context = _latest_run_context(root)
    run_context_payload = _json(run_context) if run_context else {}
    try:
        run_start_ns = int(run_context_payload.get("start_time_ns")) if run_context_payload.get("start_time_ns") else (identity.stat().st_mtime_ns if identity else None)
    except Exception:
        run_start_ns = identity.stat().st_mtime_ns if identity else None

    # Dataset-only reviewfix5 intentionally reuses the expensive graph-v3 layer.
    # Reuse is valid only when the current run context names the upstream
    # reviewfix3 context and that context proves the graph reports were produced
    # after its own start.  Do not exempt arbitrary same-version historical graph
    # reports merely because their filename begins with ``hybrid_graph.``.
    upstream_context: Path | None = None
    upstream_payload: dict[str, Any] = {}
    upstream_start_ns: int | None = None
    raw_upstream = run_context_payload.get("reused_upstream_context") if run_context_payload else None
    if raw_upstream:
        candidate = Path(str(raw_upstream))
        if not candidate.is_absolute():
            candidate = root / candidate
        if candidate.is_file():
            upstream_context = candidate.resolve()
            upstream_payload = _json(upstream_context)
            try:
                upstream_start_ns = int(upstream_payload.get("start_time_ns")) if upstream_payload.get("start_time_ns") else None
            except Exception:
                upstream_start_ns = None
    missing: list[str] = []
    stale: list[str] = []
    version_mismatch: list[dict[str, str]] = []
    failed: list[dict[str, Any]] = []
    quality_warnings: list[dict[str, Any]] = []
    fresh: list[str] = []

    if identity is None:
        version_mismatch.append({"path": "commands/pipeline_identity*.txt", "expected": EXPECTED_PIPELINE_VERSION, "actual": "missing"})
    elif identity_version != EXPECTED_PIPELINE_VERSION:
        version_mismatch.append({"path": str(identity.relative_to(root)), "expected": EXPECTED_PIPELINE_VERSION, "actual": str(identity_version or "missing")})
    if run_context is None:
        version_mismatch.append({"path": "commands/hybrid_run_context.reviewfix5_dataset.json", "expected": EXPECTED_PIPELINE_VERSION, "actual": "missing"})
    elif str(run_context_payload.get("pipeline_version") or "") != EXPECTED_PIPELINE_VERSION:
        version_mismatch.append({"path": str(run_context.relative_to(root)), "expected": EXPECTED_PIPELINE_VERSION, "actual": str(run_context_payload.get("pipeline_version") or "missing")})
    elif isinstance(run_context_payload.get("critical_file_sha256"), dict):
        cap_home = Path(str(run_context_payload.get("cap_home") or ""))
        for rel, expected_sha in sorted(run_context_payload["critical_file_sha256"].items()):
            current = cap_home / rel
            actual_sha = _sha256(current) if current.is_file() else "missing"
            if str(expected_sha or "") != actual_sha:
                version_mismatch.append({"path": f"runtime_sha256:{rel}", "expected": str(expected_sha or "missing"), "actual": actual_sha})

    reused_artifacts = set(run_context_payload.get("reused_artifacts") or []) if run_context_payload else set()
    if "hybrid_graph_v3" in reused_artifacts:
        expected_upstream_run_id = str(run_context_payload.get("reused_upstream_run_id") or "")
        if upstream_context is None:
            version_mismatch.append({
                "path": "reused_upstream_context",
                "expected": expected_upstream_run_id or "reviewfix3 context",
                "actual": "missing",
            })
        else:
            actual_upstream_run_id = str(upstream_payload.get("run_id") or "")
            if not expected_upstream_run_id or actual_upstream_run_id != expected_upstream_run_id:
                version_mismatch.append({
                    "path": str(upstream_context),
                    "expected": expected_upstream_run_id or "non-empty reused_upstream_run_id",
                    "actual": actual_upstream_run_id or "missing",
                })
            if upstream_start_ns is None:
                version_mismatch.append({
                    "path": str(upstream_context),
                    "expected": "valid start_time_ns",
                    "actual": "missing_or_invalid",
                })

    for path, expected_version in _required_paths(root):
        rel = str(path.relative_to(root))
        if not path.exists():
            missing.append(rel)
            continue
        # The run-context JSON is the freshness anchor itself.  Comparing its
        # filesystem mtime against the nanosecond timestamp recorded immediately
        # before writing it is vulnerable to filesystem timestamp granularity and
        # can make a completely successful run appear INCOMPLETE.  Its pipeline
        # version and critical hashes are already validated above, so accept the
        # selected context itself as fresh.
        is_run_context_anchor = run_context is not None and path.resolve() == run_context.resolve()
        if is_run_context_anchor:
            pass
        elif _is_reused_upstream(rel):
            if upstream_start_ns is None or path.stat().st_mtime_ns <= upstream_start_ns:
                stale.append(rel)
                continue
        elif run_start_ns is not None and path.stat().st_mtime_ns <= run_start_ns:
            stale.append(rel)
            continue
        fresh.append(rel)
        if path.suffix == ".json":
            payload = _json(path)
            if expected_version and str(payload.get("version") or "") != expected_version:
                version_mismatch.append({"path": rel, "expected": expected_version, "actual": str(payload.get("version") or "missing")})
            status = str(payload.get("status") or "").upper()
            # PARTIAL is allowed for PUDO/readiness reports because a few scenes
            # may be deliberately rejected for insufficient real geometry.  Final
            # semantic audits, site consistency and validation must be PASS-like.
            if rel.startswith("hybrid_pudo."):
                site_conflicts = int(payload.get("same_site_static_evidence_conflict_count") or 0)
                if site_conflicts > 0:
                    failed.append({"path": rel, "status": f"static_site_evidence_conflicts={site_conflicts}"})
                if str(payload.get("side_semantics") or "") != "episode_route_relative_service_approach_relation":
                    failed.append({"path": rel, "status": "missing_or_wrong_route_relative_side_semantics"})
                static_fields = set(payload.get("static_transfer_fields") or [])
                if "side" in static_fields:
                    failed.append({"path": rel, "status": "side_must_not_be_static_transfer_field"})
            if rel.startswith("hybrid_graph."):
                if status != "PASS":
                    failed.append({"path": rel, "status": status or "missing"})
                slope_range = payload.get("numeric_field_ranges", {}).get("slope", {}) if isinstance(payload.get("numeric_field_ranges"), dict) else {}
                try:
                    slope_max = float(slope_range.get("max")) if slope_range.get("max") is not None else None
                except Exception:
                    slope_max = None
                if slope_max is not None and slope_max > 1.0 + 1e-9:
                    failed.append({"path": rel, "status": f"implausible_slope_max={slope_max:.6g}"})
            if "hybrid_dataset_audit" in rel and status != "PASS":
                failed.append({"path": rel, "status": status or "missing"})
            if "/dataset_quality." in rel:
                blocking = set(((payload.get("publication_readiness") or {}).get("blocking_issues") or []))
                sparse = sorted(blocking.intersection({
                    "oracle_passenger_complete_skeletons_too_sparse",
                    "passenger_feasible_edges_too_sparse",
                }))
                if sparse:
                    label_health = payload.get("label_health") if isinstance(payload.get("label_health"), dict) else {}
                    skel_rate = float(label_health.get("oracle_skeleton_rate") or 0.0)
                    edge_rate = float(label_health.get("passenger_y_true_rate") or 0.0)
                    quality_warnings.append({
                        "path": rel,
                        "warning": "quality_distribution_warning:" + ",".join(sparse),
                        "oracle_skeleton_rate": skel_rate,
                        "passenger_y_true_rate": edge_rate,
                    })
                    # A sparse but nonzero benchmark can still support T3/T5 with
                    # class weighting/negative sampling.  A total label collapse
                    # indicates an oracle or transition-generation bug and remains
                    # a structural blocker.
                    if skel_rate <= 0.0 or edge_rate <= 0.0:
                        failed.append({"path": rel, "status": "label_collapse:" + ",".join(sparse)})
            if rel == "hybrid_site_consistency.json" and status != "PASS":
                failed.append({"path": rel, "status": status or "missing"})
            if "merged_validation_report.hybrid.json" in rel:
                valid = payload.get("valid")
                if status == "FAIL" or valid is False:
                    failed.append({"path": rel, "status": status or f"valid={valid}"})
        elif rel.startswith("commands/hybrid_build."):
            rc = _log_rc(path)
            if rc is None:
                failed.append({"path": rel, "status": "missing_END_rc"})
            elif rc != 0:
                failed.append({"path": rel, "status": f"rc={rc}"})

    if failed or version_mismatch:
        status = "FAIL"
    elif missing or stale or identity is None:
        status = "INCOMPLETE"
    else:
        status = "PASS"
    return {
        "status": status,
        "identity_file": str(identity.relative_to(root)) if identity else None,
        "identity_pipeline_version": identity_version,
        "expected_pipeline_version": EXPECTED_PIPELINE_VERSION,
        "run_context_file": str(run_context.relative_to(root)) if run_context else None,
        "run_id": run_context_payload.get("run_id") if run_context_payload else None,
        "run_start_mtime_ns": run_start_ns,
        "run_start_utc": _iso_from_ns(run_start_ns),
        "reused_upstream_context": str(upstream_context) if upstream_context else None,
        "reused_upstream_run_id": upstream_payload.get("run_id") if upstream_payload else None,
        "reused_upstream_start_ns": upstream_start_ns,
        "reused_upstream_required": [rel for rel in fresh if _is_reused_upstream(rel)],
        "fresh_required": fresh,
        "missing_required": missing,
        "stale_required": stale,
        "version_mismatches": version_mismatch,
        "failed_required": failed,
        "quality_warnings": quality_warnings,
    }


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--reports_root", required=True)
    p.add_argument("--output_zip", required=True)
    p.add_argument("--max_command_log_bytes", type=int, default=2_000_000)
    p.add_argument("--require_complete", action="store_true", help="Exit 2 unless assessed status is PASS; the ZIP is still written first.")
    args = p.parse_args()

    root = Path(args.reports_root).resolve()
    if not root.exists():
        raise FileNotFoundError(root)
    out = Path(args.output_zip).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    assessment = _assess(root)
    reports = _collect(root, REPORT_GLOBS)
    logs = _collect(root, COMMAND_GLOBS)
    selected = reports + [x for x in logs if x not in reports]
    manifest: dict[str, Any] = {
        **assessment,
        "version": VERSION,
        "reports_root": str(root),
        "selected_file_count": len(selected),
        "max_command_log_bytes": int(args.max_command_log_bytes),
        "files": [],
        "expected_versions": {
            "hybrid_graph": EXPECTED_GRAPH_VERSION,
            "hybrid_pudo": EXPECTED_PUDO_VERSION,
            "hybrid_ready": EXPECTED_READY_VERSION,
            "hybrid_dataset_audit": EXPECTED_AUDIT_VERSION,
            "hybrid_site_consistency": EXPECTED_SITE_AUDIT_VERSION,
            "pipeline": EXPECTED_PIPELINE_VERSION,
        },
        "interpretation": (
            "PASS means all required final hybrid artifacts are fresh relative to the latest pipeline identity, "
            "have the expected semantic versions, the pipeline identity matches this code revision, and final semantic audits/build logs pass. INCOMPLETE means the "
            "bundle is useful for diagnosis but the benchmark is not yet freeze-ready. Distribution warnings such as sparse positive skeletons are reported in quality_warnings; they do not by themselves mean that structural dataset construction failed."
        ),
    }

    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        readme = (
            "CapPlan hybrid benchmark review bundle\n"
            f"version={VERSION}\nstatus={assessment['status']}\n\n"
            "Contains only small reports and selected command logs. It excludes nuPlan DBs, materialized datasets, "
            "accessibility graph JSONLs and PUDO JSONLs.\n"
            "The bundle status is a real completeness gate, not merely a packaging-success flag.\n"
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
        "status": assessment["status"],
        "version": VERSION,
        "selected_file_count": len(selected),
        "output_zip": str(out),
        "output_zip_bytes": out.stat().st_size,
        "manifest": str(manifest_path),
        "missing_required_count": len(assessment["missing_required"]),
        "stale_required_count": len(assessment["stale_required"]),
        "version_mismatch_count": len(assessment["version_mismatches"]),
        "failed_required_count": len(assessment["failed_required"]),
        "identity_pipeline_version": assessment.get("identity_pipeline_version"),
        "run_id": assessment.get("run_id"),
    }, ensure_ascii=False, indent=2, sort_keys=True))
    print(f"HYBRID_REVIEW_BUNDLE={assessment['status']}")
    if args.require_complete and assessment["status"] != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
