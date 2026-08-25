import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_pudo_fallback_side_is_relational_not_static():
    mod = _load("pudo_v5", "scripts/build_hybrid_pudo_evidence.py")
    row = {
        "episode_id": "ep", "anchor_id": "a", "side": "unknown",
        "curb_height_m": None, "sidewalk_width_m": None,
        "deployment_clearance_m": None, "curb_ramp": None,
        "legal_stop": None, "legal_basis": None, "blockage_risk": None,
        "lighting": None, "shelter": None,
    }
    prov = {}
    profile = mod._profile("boston")
    mod._fill_missing(
        row, prov, city="boston", split="train", site_class="accessible_loading",
        site_seed=123, dynamic_seed=456, site_key="boston|site", profile=profile,
    )
    assert row["side"] == "right"
    assert prov["side"]["kind"] == "simulated"
    assert prov["side"]["semantic_scope"] == "episode_route_relative_service_relation"
    assert prov["side"]["correlation_scope"] == "episode_route_approach"
    assert "side" not in mod.STATIC_TRANSFER_FIELDS
    assert mod.VERSION == "abilitybench_hybrid_pudo_v5_20260825"


def test_hybrid_graph_rejects_extreme_dem_grade_and_nonpositive_width():
    mod = _load("graph_v3", "scripts/build_hybrid_accessibility_overlay.py")
    row = {
        "edge_id": "sidewalk:0:1", "from_node": "p0", "to_node": "p1",
        "source": "osm",
        "width_m": 0.0,
        "slope": 2.5,
        "cross_slope": 0.01,
        "surface": "concrete",
        "curb_ramp": True,
        "step_free": True,
        "lighting": "lit",
        "shelter": False,
        "metadata": {"field_provenance": {
            "width_m": {"kind": "observed", "source": "osm"},
            "slope": {"kind": "derived", "source": "high_resolution_dem_endpoint_elevation", "method": "absolute_endpoint_grade"},
            "cross_slope": {"kind": "observed", "source": "survey"},
            "surface": {"kind": "observed", "source": "osm"},
            "curb_ramp": {"kind": "observed", "source": "gis"},
            "step_free": {"kind": "observed", "source": "gis"},
            "lighting": {"kind": "observed", "source": "gis"},
            "shelter": {"kind": "observed", "source": "gis"},
        }},
    }
    _kinds, _group, rejected = mod._fill(row, city="boston", split="train", episode_id="ep", base_seed=20260822)
    assert rejected["slope"]["value"] == 2.5
    assert rejected["width_m"]["value"] == 0.0
    assert 0.0 < float(row["width_m"]) <= 3.2
    assert 0.0 <= float(row["slope"]) <= 0.35
    assert row["metadata"]["field_provenance"]["slope"]["kind"] == "simulated"
    assert "rejected_field_evidence" in row["metadata"]
    assert mod.VERSION == "abilitybench_hybrid_accessibility_v3_20260825"


def test_dataset_audit_v4_is_provenance_strict():
    mod = _load("audit_v4", "scripts/audit_hybrid_benchmark.py")
    assert mod.VERSION == "abilitybench_hybrid_dataset_audit_v4_20260825"
