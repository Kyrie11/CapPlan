from __future__ import annotations

import json
from pathlib import Path

from capplan.data.external_validation import inspect_source, validate_external_config


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_rejects_html_and_zero_byte_downloads(tmp_path: Path) -> None:
    html = tmp_path / "fake.geojson"
    html.write_text("<!doctype html><title>403 Forbidden</title>", encoding="utf-8")
    empty = tmp_path / "empty.json"
    empty.touch()
    assert not inspect_source(html).valid
    assert "downloaded_error_or_html_page_instead_of_data" in inspect_source(html).errors
    assert not inspect_source(empty).valid
    assert "empty_file" in inspect_source(empty).errors


def test_curb_flag_without_measurement_is_not_physical_inventory(tmp_path: Path) -> None:
    p = tmp_path / "curbs.jsonl"
    _write_jsonl(p, [{"id": "r1", "lon": -71.0, "lat": 42.0, "curb_ramp": True}])
    report = inspect_source(p, role="curb_inventory")
    assert not report.valid
    assert "curb_inventory_has_no_dimensional_or_slope_measurements" in report.errors


def test_bootstrap_and_paper_preflight_are_separate(tmp_path: Path) -> None:
    ext = tmp_path / "external"
    osm = ext / "normalized" / "osm" / "boston_sidewalks.geojson"
    _write_json(osm, {
        "type": "FeatureCollection",
        "properties": {"schema_variant": "osm_geojson", "evidence_tier": "B_community_mapped"},
        "features": [{"type": "Feature", "geometry": {"type": "LineString", "coordinates": [[-71.1, 42.3], [-71.0, 42.4]]}, "properties": {"highway": "footway"}}],
    })
    georef = ext / "georeference" / "boston.json"
    _write_json(georef, {"wgs84_crs": "EPSG:4326", "local_crs": "EPSG:26919", "validated": True, "spatial_alignment_validated": True})
    config = {"external_root": str(ext), "cities": {"boston": {}}}

    bootstrap = validate_external_config(config, ["boston"], policy="bootstrap", project_root=tmp_path)
    assert bootstrap["ready_for_requested_policy"]
    assert not bootstrap["publication_ready"]

    _write_jsonl(ext / "normalized" / "curb_inventory" / "boston.jsonl", [{
        "id": "a1", "lon": -71.0, "lat": 42.0, "curb_ramp": True,
        "curb_height_m": 0.0, "sidewalk_width_m": 1.5, "deployment_clearance_m": 1.4,
        "source": "manual", "authoritative": True,
    }])
    _write_jsonl(ext / "normalized" / "curb_regulations" / "boston.jsonl", [{
        "regulation_id": "a1", "lon": -71.0, "lat": 42.0, "legal_stop": True,
        "legal_basis": "posted loading sign verified", "source": "manual",
    }])
    _write_json(ext / "normalized" / "entrances" / "boston.geojson", {
        "type": "FeatureCollection", "features": [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [-71.0, 42.0]}, "properties": {"entrance_id": "e1"}}]
    })
    _write_jsonl(ext / "normalized" / "dem" / "boston.jsonl", [{"lon": -71.0, "lat": 42.0, "elevation_m": 3.2}])
    _write_jsonl(ext / "audits" / "boston" / "manual_audit_manifest.jsonl", [{
        "audit_id": "a1", "observed_at": "2026-08-01T00:00:00Z", "auditor_id": "hash1"
    }])
    _write_json(ext / "manifests" / "boston.json", {"sources": [{
        "role": "osm", "source_url": "https://example.org/osm", "license": "ODbL-1.0",
        "path": str(osm), "files": [{"path": str(osm), "sha256": "abc"}],
    }]})

    paper = validate_external_config(config, ["boston"], policy="paper", project_root=tmp_path)
    assert paper["ready_for_requested_policy"], paper["blockers"]
    assert paper["publication_ready"]
