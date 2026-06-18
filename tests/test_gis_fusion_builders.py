import json
import subprocess
import sys
from pathlib import Path

from capplan.utils.serialization import read_jsonl


def test_gis_fusion_builder_creates_entrances_and_curb_connectors(tmp_path):
    georef = tmp_path / "geo.json"
    georef.write_text(json.dumps({"origin_lat": 42.0, "origin_lon": -71.0}), encoding="utf-8")
    scene_dir = tmp_path / "scene"
    scene_dir.mkdir()
    (scene_dir / "scenes.jsonl").write_text(json.dumps({"episode_id": "ep0", "map_name": "test_map", "route_corridor": {"polyline": [[0, 0], [120, 0]]}}) + "\n")
    osm = tmp_path / "osm.json"
    osm.write_text(json.dumps({
        "elements": [
            {"type": "node", "id": 1, "lat": 42.0, "lon": -71.0},
            {"type": "node", "id": 2, "lat": 42.0, "lon": -70.999, "tags": {"kerb": "lowered", "source": "osm_survey"}},
            {"type": "way", "id": 10, "nodes": [1, 2], "tags": {"highway": "footway", "footway": "sidewalk", "width": "1.5", "source": "osm_survey"}},
            {"type": "node", "id": 3, "lat": 42.00002, "lon": -71.0, "tags": {"entrance": "main", "source": "city_building_entrances"}},
            {"type": "node", "id": 4, "lat": 42.00002, "lon": -70.999, "tags": {"entrance": "main", "source": "city_building_entrances"}}
        ]
    }), encoding="utf-8")
    out = tmp_path / "graphs"
    subprocess.check_call([
        sys.executable, "scripts/build_accessibility_graphs.py",
        "--scene_dataset_dir", str(scene_dir),
        "--georeference_json", str(georef),
        "--osm_source", str(osm),
        "--output_graph_dir", str(out),
        "--min_nodes_per_episode", "2", "--min_edges_per_episode", "1",
    ])
    nodes = read_jsonl(out / "ep0.nodes.jsonl")
    edges = read_jsonl(out / "ep0.edges.jsonl")
    assert any(n["kind"] == "entrance" for n in nodes)
    assert any(n["kind"] == "curb_ramp" for n in nodes)
    assert any("connector" in str(e.get("crossing_type")) for e in edges)


def test_pudo_and_service_generators_use_graph_outputs(tmp_path):
    graph_dir = tmp_path / "graphs"
    graph_dir.mkdir()
    nodes = [
        {"node_id": "entrance_a", "x": 0.0, "y": 0.0, "kind": "entrance", "confidence": 0.95, "source": "city_entrance", "pose": {"x": 0.0, "y": 0.0, "heading": 0.0, "frame": "map"}},
        {"node_id": "entrance_b", "x": 50.0, "y": 0.0, "kind": "entrance", "confidence": 0.95, "source": "city_entrance", "pose": {"x": 50.0, "y": 0.0, "heading": 0.0, "frame": "map"}},
        {"node_id": "curb0", "x": 10.0, "y": 1.0, "kind": "curb_ramp", "confidence": 0.9, "source": "city_curb", "pose": {"x": 10.0, "y": 1.0, "heading": 0.0, "frame": "map"}},
    ]
    edges = [
        {"edge_id": "e0", "from_node": "entrance_a", "to_node": "curb0", "length_m": 10.0, "width_m": 1.5, "slope": 0.01, "cross_slope": 0.01, "surface": "concrete", "curb_ramp": True, "step_free": True, "obstacle": False, "confidence": 0.9, "geometry": [[0,0],[10,1]], "source": "city_sidewalk"},
        {"edge_id": "e1", "from_node": "curb0", "to_node": "entrance_b", "length_m": 40.0, "width_m": 1.5, "slope": 0.01, "cross_slope": 0.01, "surface": "concrete", "curb_ramp": True, "step_free": True, "obstacle": False, "confidence": 0.9, "geometry": [[10,1],[50,0]], "source": "city_sidewalk"},
    ]
    (graph_dir / "ep0.nodes.jsonl").write_text("\n".join(json.dumps(x) for x in nodes) + "\n")
    (graph_dir / "ep0.edges.jsonl").write_text("\n".join(json.dumps(x) for x in edges) + "\n")
    (graph_dir / "ep0.jsonl").write_text(json.dumps({"episode_id":"ep0", "nodes": nodes, "edges": edges, "metadata": {"node_attributes": {"curb0": {"pudo_connector_candidate": True, "curb_height_m": 0.03, "deployment_clearance_m": 1.3}}}}) + "\n")
    scene_dir = tmp_path / "scene"
    scene_dir.mkdir()
    (scene_dir / "scenes.jsonl").write_text(json.dumps({"episode_id": "ep0", "route_corridor": {"polyline": [[0, 0], [60, 0]]}, "agent_history": []}) + "\n")
    regs = tmp_path / "regs.jsonl"
    regs.write_text(json.dumps({"id": "reg0", "x": 10.0, "y": 1.0, "legal_stop": True, "source": "city_curb_regulation", "confidence": 0.9}) + "\n")
    pudo_out = tmp_path / "pudo.jsonl"
    subprocess.check_call([
        sys.executable, "scripts/build_pudo_evidence.py", "--scene_dataset_dir", str(scene_dir), "--accessibility_graph_dir", str(graph_dir),
        "--curb_regulation_jsonl", str(regs), "--output_pudo_evidence_jsonl", str(pudo_out), "--source_name", "city_curb_regulation", "--fail_on_missing_core_evidence"
    ])
    pudo = read_jsonl(pudo_out)
    assert pudo and pudo[0]["legal_stop"] is True and pudo[0]["deployment_clearance_m"] == 1.3

    req_out = tmp_path / "requests.jsonl"
    prof_out = tmp_path / "profiles.jsonl"
    subprocess.check_call([
        sys.executable, "scripts/build_service_layer.py", "--accessibility_graph_dir", str(graph_dir),
        "--output_service_requests_jsonl", str(req_out), "--output_capability_profiles_jsonl", str(prof_out),
        "--num_requests_per_episode", "3"
    ])
    profiles = read_jsonl(prof_out)
    requests = read_jsonl(req_out)
    assert {p["profile_id"] for p in profiles} == {"basic_service_complete", "mobility_interface_constrained", "compound_uncertainty_sensitive"}
    assert len(requests) == 3
