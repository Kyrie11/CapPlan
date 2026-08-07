#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

try:
    import yaml  # type: ignore
except Exception as exc:  # pragma: no cover
    raise RuntimeError("pyyaml is required") from exc

try:
    from pyproj import CRS  # type: ignore
except Exception as exc:  # pragma: no cover
    raise RuntimeError("pyproj>=3.6 is required for CRS validation") from exc

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def expand_path(value: str) -> Path:
    p = Path(str(value).format(project_root=str(PROJECT_ROOT))).expanduser()
    return p if p.is_absolute() else PROJECT_ROOT / p


def find_gpkg(map_root: Path, map_version: str, map_name: str) -> Path:
    candidates = [
        map_root / map_version / map_name / f"{map_name}.gpkg",
        map_root / map_version / map_name / "map.gpkg",
        map_root / map_name / f"{map_name}.gpkg",
        map_root / map_name / "map.gpkg",
    ]
    for p in candidates:
        if p.exists():
            return p
    matches = sorted(map_root.rglob(f"{map_name}.gpkg")) + sorted(map_root.rglob("map.gpkg"))
    for p in matches:
        if map_name in str(p):
            return p
    raise FileNotFoundError(f"could not locate GPKG for {map_name} below {map_root}")


def table_columns(conn: sqlite3.Connection, table: str) -> List[str]:
    return [str(row[1]) for row in conn.execute(f'PRAGMA table_info("{table}")')]


def projected_crs_from_gpkg(path: Path) -> Tuple[str, Dict[str, Any]]:
    conn = sqlite3.connect(str(path))
    try:
        tables = [str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        if "meta" in tables:
            cols = table_columns(conn, "meta")
            lower = {c.lower(): c for c in cols}
            # nuPlan map GPKGs normally expose metadata as key/value. Prefer an
            # exact lookup so an unrelated EPSG-looking value cannot win.
            if "key" in lower and "value" in lower:
                row = conn.execute(
                    f'SELECT "{lower["value"]}" FROM "meta" WHERE lower("{lower["key"]}")=lower(?)',
                    ("projectedCoordSystem",),
                ).fetchone()
                if row:
                    epsg = normalize_epsg(str(row[0]))
                    if epsg:
                        return epsg, {"table": "meta", "key": "projectedCoordSystem", "value": row[0]}

            # Compatibility fallback for map packages whose metadata schema is
            # not a strict key/value pair. Only accept rows that explicitly
            # mention projectedCoordSystem/projected_crs.
            rows = conn.execute('SELECT * FROM "meta"').fetchall()
            for row in rows:
                record = dict(zip(cols, row))
                joined = " ".join(str(v) for v in record.values() if v is not None)
                if "projectedcoordsystem" not in joined.lower() and not any(
                    str(k).lower() in {"projectedcoordsystem", "projected_crs"} for k in record
                ):
                    continue
                for key, value in record.items():
                    if value is None:
                        continue
                    if str(key).lower() in {"value", "projectedcoordsystem", "projected_crs"} or "epsg" in str(value).lower():
                        epsg = normalize_epsg(str(value))
                        if epsg:
                            return epsg, {"table": "meta", "record": record}

        # Standards-compliant fallback: inspect the GeoPackage SRS registry.
        if "gpkg_spatial_ref_sys" in tables:
            rows = conn.execute(
                "SELECT srs_name, srs_id, organization, organization_coordsys_id, definition FROM gpkg_spatial_ref_sys"
            ).fetchall()
            projected: List[Tuple[Any, ...]] = []
            for r in rows:
                if str(r[2]).upper() != "EPSG":
                    continue
                try:
                    candidate = CRS.from_epsg(int(r[3]))
                except Exception:
                    continue
                if candidate.is_projected:
                    projected.append(r)
            if len(projected) == 1:
                epsg = f"EPSG:{int(projected[0][3])}"
                return epsg, {"table": "gpkg_spatial_ref_sys", "row": projected[0]}
        raise RuntimeError("projectedCoordSystem not found in meta and no unique projected EPSG fallback exists")
    finally:
        conn.close()


def normalize_epsg(value: str) -> Optional[str]:
    m = re.search(r"EPSG\s*[:=]?\s*(\d{4,6})", value, flags=re.I)
    if m:
        return f"EPSG:{m.group(1)}"
    if value.strip().isdigit():
        return f"EPSG:{value.strip()}"
    return None


def main() -> None:
    p = argparse.ArgumentParser(description="Generate validated georeference JSON files from nuPlan map GPKG metadata instead of guessing UTM zones.")
    p.add_argument("--config", default="configs/abilitybench_nuplan_real.yaml")
    p.add_argument("--cities", default=None)
    p.add_argument("--output_dir", default="{project_root}/data/external/georeference")
    args = p.parse_args()
    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8")) or {}
    map_root = expand_path(cfg["nuplan"]["map_root"])
    map_version = str(cfg["nuplan"]["map_version"])
    cities = [x for x in args.cities.replace(",", "+").split("+") if x] if args.cities else list(cfg["cities"])
    out_dir = expand_path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    report = []
    for city in cities:
        map_names = cfg["cities"][city].get("map_names") or []
        if len(map_names) != 1:
            raise RuntimeError(f"{city} must have exactly one map_name for CRS generation: {map_names}")
        gpkg = find_gpkg(map_root, map_version, map_names[0])
        epsg, evidence = projected_crs_from_gpkg(gpkg)
        parsed = CRS.from_user_input(epsg)
        if not parsed.is_projected:
            raise RuntimeError(f"{city}: nuPlan local CRS is not projected: {epsg}")
        payload = {
            "wgs84_crs": "EPSG:4326",
            "local_crs": epsg,
            "projected_map_frame": True,
            # Compatibility flag: this means the CRS metadata itself is valid.
            # Spatial overlap with external GIS is checked separately downstream.
            "validated": True,
            "crs_metadata_validated": True,
            "spatial_alignment_validated": False,
            "validation_scope": "nuplan_map_crs_metadata_only",
            "validation_method": "nuplan_map_gpkg_metadata",
            "map_name": map_names[0],
            "map_gpkg": str(gpkg),
            "evidence": evidence,
        }
        dest = out_dir / f"{city}.json"
        dest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        report.append({"city": city, "output": str(dest), "local_crs": epsg, "map_gpkg": str(gpkg)})
    print(json.dumps({"status": "PASS", "cities": report}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
