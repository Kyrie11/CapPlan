from __future__ import annotations

import csv
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HEADER = [
    "audit_id","city","lon","lat","curb_height_m","sidewalk_width_m","deployment_clearance_m","curb_ramp",
    "running_slope","cross_slope","surface","legal_stop","legal_basis","service_class","time_window",
    "entrance_id","entrance_lon","entrance_lat","observed_at","auditor_id","photo_ref","notes","protocol_version",
]


def write_csv(path: Path, *, entrance_lon: str = "", entrance_lat: str = "") -> None:
    row = {
        "audit_id":"A1","city":"boston","lon":"-71.05","lat":"42.35","curb_height_m":"0.0",
        "sidewalk_width_m":"1.8","deployment_clearance_m":"1.5","curb_ramp":"true",
        "running_slope":"0.04","cross_slope":"0.01","surface":"concrete","legal_stop":"true",
        "legal_basis":"posted sign photo","service_class":"autonomous_mobility","time_window":"snapshot",
        "entrance_id":"E1","entrance_lon":entrance_lon,"entrance_lat":entrance_lat,
        "observed_at":"2026-08-01T00:00:00Z","auditor_id":"hash","photo_ref":"p.jpg","notes":"","protocol_version":"v1",
    }
    with path.open("w", newline="", encoding="utf-8") as f:
        w=csv.DictWriter(f, fieldnames=HEADER); w.writeheader(); w.writerow(row)


def test_entrance_must_have_independent_coordinates(tmp_path: Path) -> None:
    src=tmp_path/"audit.csv"; write_csv(src)
    proc=subprocess.run([sys.executable, str(ROOT/"scripts/build_manual_audit_layers.py"), "--input_csv", str(src), "--city", "boston", "--external_root", str(tmp_path/"ext")], text=True, capture_output=True)
    assert proc.returncode != 0
    assert "requires independent entrance_lon/entrance_lat" in proc.stderr


def test_entrance_uses_entrance_coordinates_not_curb(tmp_path: Path) -> None:
    src=tmp_path/"audit.csv"; write_csv(src, entrance_lon="-71.051", entrance_lat="42.351")
    subprocess.check_call([sys.executable, str(ROOT/"scripts/build_manual_audit_layers.py"), "--input_csv", str(src), "--city", "boston", "--external_root", str(tmp_path/"ext")])
    fc=json.loads((tmp_path/"ext/normalized/entrances/boston.geojson").read_text())
    assert fc["features"][0]["geometry"]["coordinates"] == [-71.051, 42.351]
    assert fc["features"][0]["geometry"]["coordinates"] != [-71.05, 42.35]
