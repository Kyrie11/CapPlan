from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path

from capplan.data.gis_fusion import SceneContext


def _load(name: str, rel: str):
    root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(name, root / rel)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_legacy_partial_scan_records_exact_ordered_episode_prefix_hash(tmp_path):
    mod = _load("extract_20260821", "scripts/extract_nuplan_scenes.py")
    scenes = tmp_path / "scenes.jsonl.part"
    episodes = tmp_path / "episodes.jsonl.part"
    ids = ["nuplan_a", "nuplan_b", "nuplan_c"]
    with scenes.open("w", encoding="utf-8") as sf, episodes.open("w", encoding="utf-8") as ef:
        for eid in ids:
            sf.write(json.dumps({"episode_id": eid, "source": "nuplan", "split": "train", "map_name": "us-ma-boston", "scenario_type": "unknown"}) + "\n")
            ef.write(json.dumps({"episode_id": eid}) + "\n")
    got = mod._scan_partial_pair(scenes, episodes, split="train", allowed_map_names={"us-ma-boston"})
    h = hashlib.sha256()
    for eid in ids:
        h.update(eid.encode()); h.update(b"\0")
    assert got["num_scenes"] == 3
    assert got["last_episode_id"] == ids[-1]
    assert got["episode_id_prefix_sha256"] == h.hexdigest()


def test_partial_checkpoint_truncates_uncommitted_tail(tmp_path):
    mod = _load("extract_checkpoint_20260821", "scripts/extract_nuplan_scenes.py")
    scenes = tmp_path / "scenes.jsonl.part"; episodes = tmp_path / "episodes.jsonl.part"
    committed_s = b'{"episode_id":"a"}\n'; committed_e = b'{"episode_id":"a"}\n'
    scenes.write_bytes(committed_s + b'{"episode_id":"tail"}\n')
    episodes.write_bytes(committed_e + b'{"episode_id":"tail"}\n')
    state = tmp_path / "scene_context_partial_state.json"
    state.write_text(json.dumps({
        "status": "PARTIAL", "extract_fingerprint": "fp", "num_scenes": 1,
        "last_episode_id": "a", "scenes_bytes": len(committed_s), "episodes_bytes": len(committed_e),
    }), encoding="utf-8")
    got = mod._load_checkpoint(state, "fp", scenes, episodes)
    assert got and got["num_scenes"] == 1
    assert scenes.read_bytes() == committed_s
    assert episodes.read_bytes() == committed_e


def test_graph_resume_fingerprint_is_per_episode_route_context(tmp_path):
    mod = _load("graphs_20260821", "scripts/build_accessibility_graphs.py")
    georef = tmp_path / "geo.json"; georef.write_text("{}", encoding="utf-8")
    args = argparse.Namespace(
        source_name="x", episode_radius_m=120.0, corridor_chunk_length_m=250.0,
        snap_tolerance_m=4.0, pudo_connector_radius_m=12.0,
        min_nodes_per_episode=100, min_edges_per_episode=150,
        no_bidirectional_edges=False, compact_storage=True, fail_on_synthetic=True,
        georeference_json=str(georef), nodes_jsonl=None, edges_jsonl=None,
        osm_nodes_jsonl=None, osm_edges_jsonl=None, osm_source=None,
        opensidewalks_source=None, city_gis_dir=None, curb_inventory_source=None,
        entrance_source=None, elevation_source=None,
    )
    static = mod._graph_static_fingerprint(args)
    a = SceneContext("a", "us-ma-boston", [[0.0, 0.0], [10.0, 0.0]], (0, -1, 10, 1), 120.0)
    b = SceneContext("b", "us-ma-boston", [[0.0, 0.0], [10.0, 0.0]], (0, -1, 10, 1), 120.0)
    a_changed = SceneContext("a", "us-ma-boston", [[0.0, 0.0], [20.0, 0.0]], (0, -1, 20, 1), 120.0)
    assert mod._graph_episode_fingerprint(static, a) != mod._graph_episode_fingerprint(static, b)
    assert mod._graph_episode_fingerprint(static, a) != mod._graph_episode_fingerprint(static, a_changed)


def _source_complete_row():
    row = {
        "audit_id": "X", "city": "boston", "lon": "-71.0", "lat": "42.35",
        "curb_height_m": "0.10", "sidewalk_width_m": "1.8", "deployment_clearance_m": "1.4",
        "curb_ramp": "true", "legal_stop": "true", "legal_basis": "official passenger loading zone",
        "entrance_id": "ent-1", "entrance_lon": "-71.0001", "entrance_lat": "42.3501",
        "curb_height_m_source": "city", "sidewalk_width_m_source": "city", "deployment_clearance_m_source": "city", "curb_ramp_source": "city",
        "curb_height_m_evidence_tier": "A_city", "sidewalk_width_m_evidence_tier": "A_city", "deployment_clearance_m_evidence_tier": "A_city", "curb_ramp_evidence_tier": "A_city",
        "legal_stop_source": "city_reg", "legal_stop_evidence_tier": "A_city",
        "entrance_source": "city_buildings", "entrance_evidence_tier": "A_city",
    }
    return row


def test_audit_triage_never_auto_accepts_nearest_semantic_join():
    mod = _load("triage_20260821", "scripts/triage_pudo_audits.py")
    row = _source_complete_row()
    row["entrance_candidate_match_distance_m"] = "2.0"
    decision, reasons = mod.classify_row(row)
    assert decision == "VISUAL_REVIEW_REQUIRED"
    assert any("entrance" in r for r in reasons)


def test_audit_triage_can_pass_only_explicit_authoritative_relations():
    mod = _load("triage_explicit_20260821", "scripts/triage_pudo_audits.py")
    row = _source_complete_row()
    row["entrance_linkage_method"] = "explicit_source_relation"
    row["legal_linkage_method"] = "explicit_curb_segment_relation"
    decision, reasons = mod.classify_row(row)
    assert decision == "MACHINE_PASS_EXPLICIT_AUTHORITATIVE_SOURCE"
    assert reasons == []


def test_audit_triage_rejects_implausible_physical_value():
    mod = _load("triage_invalid_20260821", "scripts/triage_pudo_audits.py")
    row = _source_complete_row(); row["curb_height_m"] = "3.0"
    decision, reasons = mod.classify_row(row)
    assert decision == "MACHINE_REJECT_INVALID_OR_AMBIGUOUS"
    assert any("curb_height_m" in r for r in reasons)
