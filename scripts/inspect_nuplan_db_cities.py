#!/usr/bin/env python
"""Inspect nuPlan SQLite split directories and verify city/map coverage.

The script never moves or duplicates DB files. It reads the `log` table's
location/map metadata and checks that every discovered location is represented by
one of the configured city map_names. This is the safest way to understand mixed
val/test splits before ScenarioFilter(map_names=...) is applied.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List

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
        records = []
        for row in rows:
            rec = {wanted[i]: row[i] for i in range(len(wanted))}
            records.append(rec)
        locations = sorted({str(r.get(lower["location"]) or "").strip() for r in records if str(r.get(lower["location"]) or "").strip()})
        return {"db": str(path), "locations": locations, "log_metadata": records}
    finally:
        conn.close()


def _city_for_location(location: str, city_map_names: Dict[str, List[str]]) -> List[str]:
    value = location.strip().lower()
    matches: List[str] = []
    for city, map_names in city_map_names.items():
        for name in map_names:
            n = str(name).strip().lower()
            # Exact map_name is preferred; substring tolerance handles historical
            # DB strings that include a map version/path wrapper.
            if value == n or n in value or value in n:
                matches.append(city)
                break
    return sorted(set(matches))


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", default="configs/abilitybench_nuplan_real.yaml")
    p.add_argument("--split", choices=["train", "val", "test"], required=True)
    p.add_argument("--fail_on_unknown", action="store_true", help="Exit non-zero when a DB location cannot be mapped to configured city map_names.")
    p.add_argument("--report_json", default=None)
    args = p.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8")) or {}
    db_root = expand_path(cfg["nuplan"]["db_root"])
    split_cfg = cfg.get("splits", {}).get(args.split) or {}
    db_dirs = [str(x) for x in split_cfg.get("db_dirs") or []]
    city_map_names = {str(c): [str(x) for x in (cfg["cities"][c].get("map_names") or [])] for c in split_cfg.get("cities") or []}
    if not db_dirs:
        raise RuntimeError(f"split {args.split!r} has no db_dirs in config")

    dbs: List[Path] = []
    for item in db_dirs:
        candidate = db_root / item
        if candidate.is_dir():
            dbs.extend(sorted(candidate.glob("*.db")))
        elif candidate.is_file() and candidate.suffix == ".db":
            dbs.append(candidate)
        else:
            raise FileNotFoundError(f"configured nuPlan DB path does not exist: {candidate}")
    dbs = sorted(set(dbs))
    if not dbs:
        raise RuntimeError(f"no .db files found for split {args.split} under {db_dirs}")

    location_counts: Counter[str] = Counter()
    city_db_counts: Counter[str] = Counter()
    unknown: Dict[str, List[str]] = defaultdict(list)
    ambiguous: Dict[str, List[str]] = defaultdict(list)
    db_reports = []
    for db in dbs:
        info = inspect_db(db)
        mapped_cities = set()
        for loc in info["locations"]:
            location_counts[loc] += 1
            matches = _city_for_location(loc, city_map_names)
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

    issues = []
    if unknown:
        issues.append("unknown log.location values are not represented by configured city map_names")
    if ambiguous:
        issues.append("some log.location values match more than one configured city")
    missing_configured = sorted(c for c in city_map_names if city_db_counts.get(c, 0) == 0)
    # A split is allowed to omit a city, so this is diagnostic rather than a hard error.
    report = {
        "status": "PASS" if not issues else "FAIL",
        "split": args.split,
        "db_root": str(db_root),
        "db_dirs": db_dirs,
        "db_count": len(dbs),
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
