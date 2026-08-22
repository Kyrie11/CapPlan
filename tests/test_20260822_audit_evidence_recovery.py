from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_lta_passenger_pickup_bay_preserves_explicit_loading_legality():
    mod = _load("normalize_20260822", "scripts/normalize_accessibility_evidence.py")
    args = argparse.Namespace(width_unit="unknown", reveal_unit="unknown", slope_unit="unknown")
    row = {"type": "Feature", "geometry": {"type": "Point", "coordinates": [103.85, 1.29]}, "properties": {"OBJECTID": 7}}
    out = mod.normalize("lta_passenger_pickup_bay", row, 0, "Singapore LTA DataMall Passenger Pickup Bay", args)
    assert out["legal_stop"] is True
    assert out["candidate_only"] is False
    assert out["service_class"] == "general_passenger_loading"
    assert out["legal_linkage_method"] == "authoritative_source_relation"
    assert str(out["evidence_tier"]).startswith("A_")


def test_exact_candidate_regulation_relation_beats_nearest_join():
    mod = _load("prepare_20260822", "scripts/prepare_pudo_audit_worklist.py")
    reg = {
        "regulation_id": "42", "lon": 103.85, "lat": 1.29, "legal_stop": True,
        "legal_basis": "designated passenger loading bay", "service_class": "general_passenger_loading",
        "authoritative": True, "evidence_tier": "A_test", "source": "official",
    }
    site = {"candidate_anchor_ids_train": "ep-1:candidate:42"}
    hit = mod._exact_regulation(site, {"42": reg}, 103.85001, 1.29001)
    assert hit is not None
    assert hit[0] is True
    assert hit[1] == "designated passenger loading bay"


def test_recovery_creates_singapore_authoritative_regulation_without_physical_facts(tmp_path):
    ext = tmp_path / "external"
    cand = ext / "normalized" / "candidates" / "singapore" / "passenger_pickup_bay.jsonl"
    cand.parent.mkdir(parents=True)
    cand.write_text(json.dumps({
        "regulation_id": "8", "lon": 103.8, "lat": 1.3,
        "source": "Singapore LTA DataMall Passenger Pickup Bay",
        "legal_stop": False, "candidate_only": True,
    }) + "\n", encoding="utf-8")
    report = ext / "reports" / "recover.json"
    subprocess.check_call([
        sys.executable, str(ROOT / "scripts" / "recover_pudo_audit_sources.py"),
        "--external_root", str(ext), "--cities", "singapore", "--report_json", str(report),
    ], cwd=ROOT)
    row = json.loads((ext / "normalized" / "curb_regulations" / "singapore.jsonl").read_text().strip())
    assert row["legal_stop"] is True
    assert row["service_class"] == "general_passenger_loading"
    assert "curb_height_m" not in row
    assert "deployment_clearance_m" not in row


def test_triage_still_requires_missing_physical_evidence_after_legality_recovery():
    mod = _load("triage_20260822", "scripts/triage_pudo_audits.py")
    row = {
        "audit_id": "SG-SITE-1", "lon": "103.8", "lat": "1.3",
        "legal_stop": "true", "legal_basis": "designated bay", "legal_stop_source": "LTA",
        "legal_stop_evidence_tier": "A_official", "legal_linkage_method": "direct_feature_relation",
    }
    decision, reasons = mod.classify_row(row)
    assert decision == "NEW_EVIDENCE_REQUIRED"
    assert any("curb_height_m" in r for r in reasons)
    assert any("deployment_clearance_m" in r for r in reasons)


def test_status_report_distinguishes_pass_from_evidence_readiness(tmp_path):
    ext = tmp_path / "external"; reports = ext / "reports"; reports.mkdir(parents=True)
    for city in ("boston", "pittsburgh", "vegas", "singapore"):
        d = ext / "audits" / city; d.mkdir(parents=True)
        with (d / "new_evidence_required.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=["audit_id"]); w.writeheader(); w.writerow({"audit_id": city})
    out = reports / "status.json"
    subprocess.check_call([
        sys.executable, str(ROOT / "scripts" / "summarize_pudo_audit_status.py"),
        "--external_root", str(ext), "--reports_root", str(reports), "--output", str(out),
    ], cwd=ROOT)
    data = json.loads(out.read_text())
    assert data["status"] == "PASS"
    assert data["paper_evidence_ready"] is False
    assert data["ready_for_human_source_review"] is False
    assert data["totals"]["new_evidence"] == 4
