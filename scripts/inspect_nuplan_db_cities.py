#!/usr/bin/env python
"""Inspect nuPlan SQLite split directories and verify city/map coverage.

The script never moves or duplicates DB files. It reads the `log` table's
location/map metadata and maps DB-specific location strings through explicit
`location_aliases` plus nuPlan map names from the configuration.

It also validates every configured DB directory independently. This matters for
multi-directory train splits: a missing/empty/nested directory must not be
silently hidden by thousands of DBs found in the other cities.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, List

try:
    import yaml  # type: ignore
except Exception as exc:  # pragma: no cover
    raise RuntimeError("pyyaml is required") from exc

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def expand_path(value: str) -> Path:
    p = Path(str(value).format(project_root=str(PROJECT_ROOT))).expanduser()
    return p if p.is_absolute() else PROJECT_ROOT / p


def _table_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    return [str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')]


def inspect_db(path: Path) -> Dict[str, Any]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = {str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "log" not in tables:
            raise RuntimeError("missing required nuPlan `log` table")
        cols = _table_columns(conn, "log")
        lower = {c.lower(): c for c in cols}
        if "location" not in lower:
            raise RuntimeError(f"log table has no location column; columns={cols}")
        wanted = [lower["location"]]
        for key in ("date", "map_version"):
            if key in lower:
                wanted.append(lower[key])
        query = "SELECT DISTINCT " + ", ".join(f'"{x}"' for x in wanted) + ' FROM "log"'
        rows = conn.execute(query).fetchall()
        records = [{wanted[i]: row[i] for i in range(len(wanted))} for row in rows]
        locations = sorted({
            str(r.get(lower["location"]) or "").strip()
            for r in records
            if str(r.get(lower["location"]) or "").strip()
        })
        return {"db": str(path), "locations": locations, "log_metadata": records}
    finally:
        conn.close()


def _normal(value: str) -> str:
    return str(value).strip().lower().replace("-", "_").replace(" ", "_")


def _city_for_location(location: str, city_location_names: Dict[str, List[str]]) -> List[str]:
    value = _normal(location)
    matches: List[str] = []
    for city, names in city_location_names.items():
        for name in names:
            n = _normal(name)
            # Exact alias is preferred. Substring tolerance remains only for
            # historical wrappers such as '<map_name>/version'.
            if value == n or (len(n) >= 6 and (n in value or value in n)):
                matches.append(city)
                break
    return sorted(set(matches))


def _collect_db_path(candidate: Path, recursive: bool) -> List[Path]:
    if candidate.is_file() and candidate.suffix.lower() == ".db":
        return [candidate]
    if not candidate.is_dir():
        raise FileNotFoundError(f"configured nuPlan DB path does not exist: {candidate}")
    pattern = "**/*.db" if recursive else "*.db"
    return sorted(p for p in candidate.glob(pattern) if p.is_file())


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", default="configs/abilitybench_nuplan_real.yaml")
    p.add_argument("--split", choices=["train", "val", "test"], required=True)
    p.add_argument("--fail_on_unknown", action="store_true", help="Exit non-zero when coverage/mapping checks fail.")
    p.add_argument("--no_recursive", action="store_true", help="Only inspect .db files directly inside each configured db_dir.")
    p.add_argument("--report_json", default=None)
    args = p.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8")) or {}
    db_root = expand_path(cfg["nuplan"]["db_root"])
    split_cfg = cfg.get("splits", {}).get(args.split) or {}
    db_dirs = [str(x) for x in split_cfg.get("db_dirs") or []]
    cities = [str(c) for c in split_cfg.get("cities") or []]
    city_location_names: Dict[str, List[str]] = {}
    for city in cities:
        ccfg = cfg["cities"][city]
        names = [str(x) for x in (ccfg.get("map_names") or [])]
        names.extend(str(x) for x in (ccfg.get("location_aliases") or []))
        # De-duplicate while preserving order.
        city_location_names[city] = list(dict.fromkeys(names))
    if not db_dirs:
        raise RuntimeError(f"split {args.split!r} has no db_dirs in config")

    dbs: List[Path] = []
    db_dir_reports: List[Dict[str, Any]] = []
    empty_db_dirs: List[str] = []
    recursive = not args.no_recursive
    for item in db_dirs:
        candidate = db_root / item
        found = _collect_db_path(candidate, recursive=recursive)
        db_dir_reports.append({
            "configured": item,
            "path": str(candidate),
            "db_count": len(found),
            "recursive": recursive,
        })
        if not found:
            empty_db_dirs.append(str(candidate))
        dbs.extend(found)
    dbs = sorted(set(dbs))
    if not dbs:
        raise RuntimeError(f"no .db files found for split {args.split} under {db_dirs}")

    location_counts: Counter[str] = Counter()
    city_db_counts: Counter[str] = Counter()
    unknown: Dict[str, List[str]] = defaultdict(list)
    ambiguous: Dict[str, List[str]] = defaultdict(list)
    db_reports = []
    db_errors: List[Dict[str, str]] = []
    for db in dbs:
        try:
            info = inspect_db(db)
        except Exception as exc:
            db_errors.append({"db": str(db), "error": f"{type(exc).__name__}: {exc}"})
            continue
        mapped_cities = set()
        for loc in info["locations"]:
            location_counts[loc] += 1
            matches = _city_for_location(loc, city_location_names)
            if len(matches) == 1:
                mapped_cities.add(matches[0])
            elif len(matches) == 0:
                unknown[loc].append(str(db))
            else:
                ambiguous[loc].extend(matches)
        for city in mapped_cities:
            city_db_counts[city] += 1
        info["mapped_cities"] = sorted(mapped_cities)
        db_reports.append(info)

    missing_configured = sorted(c for c in city_location_names if city_db_counts.get(c, 0) == 0)
    issues = []
    if empty_db_dirs:
        issues.append("one or more configured db_dirs contain no .db files (recursive scan)")
    if db_errors:
        issues.append("one or more DB files could not be inspected")
    if unknown:
        issues.append("unknown log.location values are not represented by configured map_names/location_aliases")
    if ambiguous:
        issues.append("some log.location values match more than one configured city")
    if bool(split_cfg.get("require_all_cities", False)) and missing_configured:
        issues.append("split requires all configured cities, but one or more cities were not observed")

    report = {
        "status": "PASS" if not issues else "FAIL",
        "split": args.split,
        "db_root": str(db_root),
        "db_dirs": db_dirs,
        "db_dir_reports": db_dir_reports,
        "empty_db_dirs": empty_db_dirs,
        "db_count": len(dbs),
        "db_errors": db_errors,
        "location_aliases": city_location_names,
        "location_db_counts": dict(sorted(location_counts.items())),
        "city_db_counts": dict(sorted(city_db_counts.items())),
        "configured_cities_not_observed": missing_configured,
        "unknown_locations": dict(unknown),
        "ambiguous_locations": dict(ambiguous),
        "issues": issues,
        "dbs": db_reports,
        "note": "val/test may contain multiple cities; keep DBs in place and filter by map_names rather than physically splitting them.",
    }
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(text)
    print(f"NUPLAN_DB_CITY_CHECK={report['status']}")
    if args.report_json:
        path = Path(args.report_json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
    if issues and args.fail_on_unknown:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
