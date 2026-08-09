from __future__ import annotations

import importlib.util
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_script(name: str):
    p = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(name.replace('.py',''), p)
    mod = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def test_nuplan_las_vegas_alias_maps_to_vegas():
    mod = load_script("inspect_nuplan_db_cities.py")
    names = {
        "vegas": ["us-nv-las-vegas-strip", "las_vegas"],
        "boston": ["us-ma-boston"],
    }
    assert mod._city_for_location("las_vegas", names) == ["vegas"]
    assert mod._city_for_location("us-nv-las-vegas-strip", names) == ["vegas"]


def test_nuplan_db_collection_is_recursive(tmp_path: Path):
    mod = load_script("inspect_nuplan_db_cities.py")
    nested = tmp_path / "nested"
    nested.mkdir()
    db = nested / "a.db"
    sqlite3.connect(db).close()
    assert mod._collect_db_path(tmp_path, recursive=True) == [db]
    assert mod._collect_db_path(tmp_path, recursive=False) == []


def test_pittsburgh_payment_points_official_headers_normalize(tmp_path: Path):
    raw = tmp_path / "payment.csv"
    raw.write_text(
        "id,location,location_type,latitude,longitude,status,zone\n"
        "abc,Forbes Ave,On street,40.4401,-79.9959,Active,1\n",
        encoding="utf-8",
    )
    out = tmp_path / "out.jsonl"
    subprocess.check_call([
        sys.executable, str(ROOT / "scripts" / "normalize_accessibility_evidence.py"),
        "--input", str(raw), "--output", str(out),
        "--profile", "pittsburgh_parking_meter", "--source", "WPRDC",
    ])
    row = json.loads(out.read_text().strip())
    assert row["lat"] == 40.4401
    assert row["lon"] == -79.9959
    assert row["candidate_only"] is True
    assert row["legal_stop"] is False


def test_pittsburgh_payment_points_header_variants_normalize(tmp_path: Path):
    raw = tmp_path / "payment.csv"
    raw.write_text(
        " ID , Latitude , Long ,Location\n"
        "abc,40.4401,-79.9959,Forbes Ave\n",
        encoding="utf-8",
    )
    out = tmp_path / "out.jsonl"
    subprocess.check_call([
        sys.executable, str(ROOT / "scripts" / "normalize_accessibility_evidence.py"),
        "--input", str(raw), "--output", str(out),
        "--profile", "pittsburgh_parking_meter", "--source", "WPRDC",
    ])
    row = json.loads(out.read_text().strip())
    assert row["lat"] == 40.4401
    assert row["lon"] == -79.9959


def test_georeference_intersection_fraction():
    mod = load_script("validate_georeference_alignment.py")
    assert mod.fraction_covered((0, 0, 10, 10), (0, 0, 5, 10)) == 0.5
