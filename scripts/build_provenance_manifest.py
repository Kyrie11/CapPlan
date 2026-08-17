#!/usr/bin/env python
"""Build a checksummed city provenance manifest from a reviewed YAML registry."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[1]


PLACEHOLDER_MARKERS = ("REPLACE", "REVIEW", "TODO", "TBD", "CHANGEME", "PLACEHOLDER", "VERIFY")


def _is_placeholder(value: str) -> bool:
    upper = str(value or "").strip().upper()
    return (not upper) or any(marker in upper for marker in PLACEHOLDER_MARKERS)


def _valid_retrieved_at(value: str) -> bool:
    text = str(value or "").strip()
    if _is_placeholder(text):
        return False
    try:
        # Accept RFC3339/ISO-8601 timestamps; normalize the common Z suffix.
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def resolve(value: str) -> Path:
    text = str(value).format(project_root=str(PROJECT_ROOT))
    p = Path(text).expanduser()
    return p if p.is_absolute() else PROJECT_ROOT / p


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def expand_files(path: Path) -> List[Path]:
    if path.is_file():
        return [path]
    if path.is_dir():
        return sorted(p for p in path.rglob("*") if p.is_file() and not p.name.startswith("."))
    return []


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--registry", required=True)
    p.add_argument("--city", required=True)
    p.add_argument("--output", required=True)
    args = p.parse_args()
    registry = yaml.safe_load(Path(args.registry).read_text(encoding="utf-8")) or {}
    city_cfg = (registry.get("cities") or {}).get(args.city)
    if not isinstance(city_cfg, dict):
        raise RuntimeError(f"registry has no cities.{args.city}")
    sources = city_cfg.get("sources") or []
    if not sources:
        raise RuntimeError("registry source list is empty")
    records: List[Dict[str, Any]] = []
    blockers: List[str] = []
    for i, source in enumerate(sources):
        role = str(source.get("role") or f"source_{i}")
        path = resolve(str(source.get("path") or ""))
        url = str(source.get("source_url") or "").strip()
        license_name = str(source.get("license") or "").strip()
        retrieved_at = str(source.get("retrieved_at") or "").strip()
        if not url:
            blockers.append(f"{role}:missing_source_url")
        elif _is_placeholder(url):
            blockers.append(f"{role}:placeholder_source_url")
        if not license_name:
            blockers.append(f"{role}:missing_license")
        elif _is_placeholder(license_name):
            blockers.append(f"{role}:placeholder_license")
        if not retrieved_at:
            blockers.append(f"{role}:missing_retrieved_at")
        elif not _valid_retrieved_at(retrieved_at):
            blockers.append(f"{role}:invalid_or_placeholder_retrieved_at")
        files = expand_files(path)
        if not files:
            blockers.append(f"{role}:missing_or_empty_path:{path}")
        records.append({
            **source,
            "role": role,
            "path": str(path),
            "files": [{"path": str(f), "bytes": f.stat().st_size, "sha256": sha256_file(f)} for f in files],
        })
    if blockers:
        raise RuntimeError("provenance registry is incomplete: " + "; ".join(blockers))
    payload = {
        "schema_version": "1.0", "city": args.city, "generated_at": datetime.now(timezone.utc).isoformat(),
        "sources": records, "source_count": len(records), "reviewed": True,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(out), "city": args.city, "sources": len(records)}, indent=2))


if __name__ == "__main__":
    main()
