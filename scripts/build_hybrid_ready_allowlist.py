#!/usr/bin/env python
"""Select benchmark-ready episodes from a hybrid PUDO truth JSONL.

This selector never invents or edits geometry.  It keeps only episodes that
already contain the configured minimum number of complete, legal, unblocked
hybrid PUDO anchors.  Rejected episode IDs and machine-readable reasons are
written for auditability so the final dataset build can skip structurally
insufficient candidates instead of aborting the whole split.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from capplan.utils.serialization import iter_jsonl

VERSION = "abilitybench_hybrid_ready_allowlist_v1_20260823"


def _write_lines(path: Path, values: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(path.suffix + ".part")
    part.write_text("".join(f"{x}\n" for x in values), encoding="utf-8")
    part.replace(path)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input_pudo_jsonl", required=True)
    p.add_argument("--output_allowlist", required=True)
    p.add_argument("--output_rejected", default=None)
    p.add_argument("--min_hybrid_eligible_pudos", type=int, default=2)
    p.add_argument("--city", default=None)
    p.add_argument("--split", default=None)
    p.add_argument("--report_json", required=True)
    args = p.parse_args()

    src = Path(args.input_pudo_jsonl)
    if not src.exists():
        raise FileNotFoundError(src)
    minimum = int(args.min_hybrid_eligible_pudos)
    if minimum < 1:
        raise ValueError("--min_hybrid_eligible_pudos must be >= 1")

    counts: Dict[str, Counter] = defaultdict(Counter)
    rows = 0
    for row in iter_jsonl(src):
        eid = str(row.get("episode_id") or "").strip()
        if not eid:
            raise RuntimeError(f"hybrid PUDO row without episode_id in {src}")
        c = counts[eid]
        c["total"] += 1
        c["complete"] += int(bool(row.get("hybrid_evidence_complete")))
        c["eligible"] += int(bool(row.get("hybrid_eligible")))
        c["legal"] += int(row.get("legal_stop") is True)
        try:
            c["unblocked"] += int(float(row.get("blockage_risk") or 0.0) < 0.85)
        except Exception:
            pass
        rows += 1
    if not counts:
        raise RuntimeError(f"hybrid PUDO JSONL is empty: {src}")

    allowed: list[str] = []
    rejected: list[str] = []
    reason_counts: Counter = Counter()
    rejected_examples: list[Dict[str, Any]] = []
    for eid in sorted(counts):
        c = counts[eid]
        if c["eligible"] >= minimum:
            allowed.append(eid)
            continue
        rejected.append(eid)
        if c["total"] < minimum:
            reason = "insufficient_geometry_anchored_candidates"
        elif c["complete"] < minimum:
            reason = "insufficient_complete_hybrid_evidence"
        else:
            reason = "insufficient_legal_unblocked_hybrid_candidates"
        reason_counts[reason] += 1
        if len(rejected_examples) < 100:
            rejected_examples.append({
                "episode_id": eid,
                "reason": reason,
                "total_pudos": c["total"],
                "complete_pudos": c["complete"],
                "eligible_pudos": c["eligible"],
                "legal_pudos": c["legal"],
                "unblocked_pudos": c["unblocked"],
            })

    if not allowed:
        raise RuntimeError(
            f"no hybrid-ready episodes in {src}: minimum={minimum}, episodes={len(counts)}"
        )

    out = Path(args.output_allowlist)
    rejected_out = Path(args.output_rejected) if args.output_rejected else out.with_name(out.stem + ".rejected.txt")
    _write_lines(out, allowed)
    _write_lines(rejected_out, rejected)

    report = {
        "status": "PASS" if not rejected else "PARTIAL",
        "version": VERSION,
        "city": args.city,
        "split": args.split,
        "input_pudo_jsonl": str(src),
        "input_sha256": _sha256(src),
        "input_rows": rows,
        "episodes": len(counts),
        "min_hybrid_eligible_pudos": minimum,
        "allowed_episode_count": len(allowed),
        "rejected_episode_count": len(rejected),
        "retention_rate": len(allowed) / len(counts),
        "rejection_reason_counts": dict(reason_counts),
        "rejected_examples": rejected_examples,
        "output_allowlist": str(out),
        "output_rejected": str(rejected_out),
        "selection_semantics": (
            "No PUDO geometry or evidence is synthesized by this selector. It only keeps episodes "
            "whose existing hybrid evidence satisfies the configured PUDO availability gate."
        ),
    }
    rp = Path(args.report_json)
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    print("HYBRID_READY_ALLOWLIST_CHECK=" + report["status"])


if __name__ == "__main__":
    main()
