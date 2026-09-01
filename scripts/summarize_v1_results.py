#!/usr/bin/env python
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path


def _json(path: Path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _csv(path: Path):
    if not path.exists():
        return None
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main() -> None:
    p = argparse.ArgumentParser(description="Summarize V1 attribution evidence into one machine-readable file.")
    p.add_argument("--v1_root", required=True)
    p.add_argument("--seed", type=int, default=13)
    args = p.parse_args()
    root = Path(args.v1_root)
    seed = int(args.seed)
    full = _json(root / "test" / f"seed{seed}" / "metrics.json") or {}
    semantics = _json(root / "test" / f"seed{seed}" / "evaluation_semantics.json") or {}
    casa = _json(root / "diagnostics" / f"casa_heads_seed{seed}.json") or {}
    main_ablation = _csv(root / "ablations" / f"seed{seed}" / "ablation_results.csv") or []
    head_isolation = _csv(root / "diagnostics" / f"head_isolation_seed{seed}" / "ablation_results.csv") or []

    out = {
        "algorithm_version": "V1",
        "seed": seed,
        "full_metrics": full,
        "evaluation_semantics": semantics,
        "casa_head_metrics": casa,
        "main_ablations": main_ablation,
        "head_isolation": head_isolation,
        "attribution_ready": bool(semantics.get("algorithm_attribution_ready", False)),
        "attribution_warnings": list(semantics.get("algorithm_attribution_warnings") or []),
    }
    path = root / f"v1_seed{seed}_summary.json"
    path.write_text(json.dumps(out, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps({
        "summary": str(path),
        "attribution_ready": out["attribution_ready"],
        "PCR": full.get("PCR"),
        "PlanReturnRate": full.get("PlanReturnRate"),
        "TSBS_expansions_p95": full.get("TSBS_expansions_p95"),
        "CF_success_flip_recall": full.get("CF_success_flip_recall"),
        "edge_auprc": casa.get("edge_auprc"),
        "value_auprc": casa.get("value_auprc"),
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
