#!/usr/bin/env python
"""Audit static physical-site consistency across train/val/test hybrid PUDO rows.

Static physical/interface facts should not change merely because the same curb is
observed in another nuPlan snapshot or official split.  Dynamic blockage remains
explicitly excluded from this check.  Small tolerances are allowed for multiple
real/derived measurements of the same site.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from capplan.utils.serialization import iter_jsonl

VERSION = "abilitybench_hybrid_site_consistency_v1_20260824"
NUMERIC_TOL = {
    "curb_height_m": 0.03,
    "sidewalk_width_m": 0.25,
    "deployment_clearance_m": 0.25,
}
CATEGORICAL = ("curb_ramp", "side", "lighting", "shelter")


def _blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input", action="append", required=True, metavar="SPLIT=PATH")
    p.add_argument("--output", required=True)
    p.add_argument("--fail_on_error", action="store_true")
    args = p.parse_args()

    by_site: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    rows = 0
    split_counts = Counter()
    missing_site_key = 0
    for spec in args.input:
        if "=" not in spec:
            raise RuntimeError(f"--input expects SPLIT=PATH, got {spec!r}")
        split, path = spec.split("=", 1)
        split = split.strip(); pp = Path(path.strip())
        if split not in {"train", "val", "test"}:
            raise RuntimeError(f"invalid split {split!r}")
        if not pp.exists():
            raise FileNotFoundError(pp)
        for raw in iter_jsonl(pp):
            row = dict(raw); rows += 1; split_counts[split] += 1
            key = str(row.get("hybrid_physical_site_key") or "")
            if not key:
                missing_site_key += 1
                continue
            by_site[key].append((split, row))

    conflicts: list[dict[str, Any]] = []
    repeated_sites = 0
    cross_split_sites = 0
    legal_variation_sites = 0
    for key, items in by_site.items():
        if len(items) > 1:
            repeated_sites += 1
        splits = {s for s, _ in items}
        if len(splits) > 1:
            cross_split_sites += 1
        for field, tol in NUMERIC_TOL.items():
            vals = []
            for split, row in items:
                try:
                    v = float(row.get(field))
                    if math.isfinite(v):
                        vals.append((split, v))
                except Exception:
                    pass
            if len(vals) > 1:
                xs = [v for _, v in vals]
                if max(xs) - min(xs) > tol + 1e-12:
                    conflicts.append({"physical_site_key": key, "field": field, "values": vals[:30], "spread": max(xs)-min(xs), "tolerance": tol})
        for field in CATEGORICAL:
            vals = [(split, str(row.get(field)).lower()) for split, row in items if not _blank(row.get(field)) and str(row.get(field)).lower() != "unknown"]
            uniq = {v for _, v in vals}
            if len(uniq) > 1:
                conflicts.append({"physical_site_key": key, "field": field, "values": vals[:30]})
        legal = {bool(row.get("legal_stop")) for _, row in items if row.get("legal_stop") is not None}
        if len(legal) > 1:
            legal_variation_sites += 1

    errors = []
    if missing_site_key:
        errors.append(f"{missing_site_key} hybrid PUDO rows lack hybrid_physical_site_key")
    if conflicts:
        errors.append(f"{len(conflicts)} static physical-site field conflicts exceed tolerance")
    report = {
        "status": "PASS" if not errors else "FAIL",
        "version": VERSION,
        "rows": rows,
        "split_row_counts": dict(split_counts),
        "physical_site_count": len(by_site),
        "repeated_physical_site_count": repeated_sites,
        "cross_split_physical_site_count": cross_split_sites,
        "static_conflict_count": len(conflicts),
        "static_conflict_examples": conflicts[:100],
        "legal_stop_variation_site_count": legal_variation_sites,
        "missing_physical_site_key_count": missing_site_key,
        "dynamic_fields_excluded": ["blockage_risk", "dynamic_confidence", "hybrid_dynamic_seed"],
        "errors": errors,
        "interpretation": "Static curb/interface facts are checked across official splits; dynamic blockage is intentionally allowed to vary by episode/time. legal_stop variation is reported but not a static-geometry failure because real restrictions can be time-dependent.",
    }
    out = Path(args.output); out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"HYBRID_SITE_CONSISTENCY={report['status']}")
    if args.fail_on_error and report["status"] != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
