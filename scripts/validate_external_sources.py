#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

try:
    import yaml  # type: ignore
except Exception as exc:  # pragma: no cover
    raise RuntimeError("pyyaml is required; run pip install -r requirements.txt") from exc

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from capplan.data.external_validation import validate_external_config
from capplan.utils.serialization import dump_json


def load_config(path: str | Path) -> Dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def main() -> None:
    p = argparse.ArgumentParser(description="Validate external AbilityBench sources before GIS fusion. Rejects zero-byte, HTML/error-page and malformed files.")
    p.add_argument("--config", default="configs/abilitybench_nuplan_real.yaml")
    p.add_argument("--cities", default=None, help="Comma/plus-separated city subset; defaults to all configured cities.")
    p.add_argument("--source_policy", choices=["bootstrap", "paper"], default=None)
    p.add_argument("--output", default="{project_root}/data/outputs/reports/external_source_preflight.json")
    p.add_argument("--no_fail", action="store_true", help="Write report and return success even when blockers exist.")
    args = p.parse_args()

    cfg = load_config(args.config)
    policy = args.source_policy or str(cfg.get("quality", {}).get("source_policy", "bootstrap"))
    if args.cities:
        cities = [x for x in args.cities.replace(",", "+").split("+") if x]
    else:
        cities = list(cfg.get("cities", {}))
    unknown = [x for x in cities if x not in cfg.get("cities", {})]
    if unknown:
        raise SystemExit(f"unknown cities: {unknown}")

    report = validate_external_config(cfg, cities, policy=policy, project_root=PROJECT_ROOT)
    output = Path(str(args.output).format(project_root=str(PROJECT_ROOT))).expanduser()
    if not output.is_absolute():
        output = PROJECT_ROOT / output
    dump_json(output, report)
    print(json.dumps(report, indent=2, sort_keys=True))
    print(f"wrote {output}")
    if report["blockers"] and not args.no_fail:
        raise SystemExit("external source preflight failed: " + "; ".join(report["blockers"]))


if __name__ == "__main__":
    main()
