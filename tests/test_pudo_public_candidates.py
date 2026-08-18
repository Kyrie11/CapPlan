import json
import subprocess
import sys
from pathlib import Path

from capplan.utils.serialization import read_jsonl


def _write_jsonl(path: Path, rows):
    path.write_text("".join(json.dumps(x) + "\n" for x in rows), encoding="utf-8")


def test_public_candidate_enters_generator_but_needs_independent_evidence(tmp_path: Path):
    graph_dir = tmp_path / "graphs"
    graph_dir.mkdir()
    nodes = [
        {"node_id": "ped0", "x": 10.0, "y": 0.0, "kind": "sidewalk", "confidence": 0.9, "source": "official_footpath", "pose": {"x": 10.0, "y": 0.0, "heading": 0.0, "frame": "map"}},
        {"node_id": "ped1", "x": 20.0, "y": 0.0, "kind": "sidewalk", "confidence": 0.9, "source": "official_footpath", "pose": {"x": 20.0, "y": 0.0, "heading": 0.0, "frame": "map"}},
    ]
    edges = [{"edge_id": "e0", "from_node": "ped0", "to_node": "ped1", "length_m": 10.0, "width_m": None, "confidence": 0.9, "geometry": [[10, 0], [20, 0]], "source": "official_footpath"}]
    _write_jsonl(graph_dir / "ep0.nodes.jsonl", nodes)
    _write_jsonl(graph_dir / "ep0.edges.jsonl", edges)
    _write_jsonl(graph_dir / "ep0.jsonl", [{"episode_id": "ep0", "metadata": {"node_attributes": {}}}])

    scene_dir = tmp_path / "scene"
    scene_dir.mkdir()
    _write_jsonl(scene_dir / "scenes.jsonl", [{"episode_id": "ep0", "route_corridor": {"polyline": [[0, 0], [30, 0]]}, "agent_history": []}])

    candidates = tmp_path / "candidates.jsonl"
    _write_jsonl(candidates, [{"regulation_id": "pickup1", "x": 10.5, "y": 0.5, "frame": "map", "source": "official_passenger_pickup_bay", "confidence": 0.9, "candidate_only": True}])

    out = tmp_path / "pudo_unverified.jsonl"
    subprocess.check_call([
        sys.executable, "scripts/build_pudo_evidence.py",
        "--scene_dataset_dir", str(scene_dir), "--accessibility_graph_dir", str(graph_dir),
        "--pudo_candidate_source", str(candidates), "--output_pudo_evidence_jsonl", str(out),
    ])
    public = [r for r in read_jsonl(out) if r.get("candidate_source") == "official_passenger_pickup_bay"]
    assert public
    assert public[0]["legal_stop"] is False
    assert public[0]["paper_eligible"] is False

    # Partial manual evidence must remain fail-closed.  Candidate semantics,
    # three scalar measurements, and a bare legal_stop value are not enough.
    inventory = tmp_path / "inventory.jsonl"
    _write_jsonl(inventory, [{
        "id": "inv1", "x": 10.5, "y": 0.5, "frame": "map", "source": "manual_interface_audit",
        "curb_height_m": 0.02, "sidewalk_width_m": 1.8, "deployment_clearance_m": 1.4, "confidence": 1.0,
    }])
    regs = tmp_path / "regs.jsonl"
    _write_jsonl(regs, [{"regulation_id": "reg1", "x": 10.5, "y": 0.5, "frame": "map", "legal_stop": True, "source": "manual_posted_sign_audit", "confidence": 1.0}])
    out2 = tmp_path / "pudo_partial.jsonl"
    subprocess.check_call([
        sys.executable, "scripts/build_pudo_evidence.py",
        "--scene_dataset_dir", str(scene_dir), "--accessibility_graph_dir", str(graph_dir),
        "--pudo_candidate_source", str(candidates), "--curb_inventory_jsonl", str(inventory),
        "--curb_regulation_jsonl", str(regs), "--output_pudo_evidence_jsonl", str(out2),
    ])
    partial = [r for r in read_jsonl(out2) if r.get("candidate_source") == "official_passenger_pickup_bay"]
    assert partial[0]["paper_eligible"] is False

    # Full independent Tier-A interface and legality evidence may promote the
    # physical site represented by a candidate into the paper-eligible layer.
    full_inventory = tmp_path / "inventory_full.jsonl"
    _write_jsonl(full_inventory, [{
        "id": "inv_full", "x": 10.5, "y": 0.5, "frame": "map",
        "source": "manual_interface_audit", "evidence_tier": "A_manual_audit", "audited": True,
        "curb_height_m": 0.02, "sidewalk_width_m": 1.8, "deployment_clearance_m": 1.4,
        "curb_ramp": True, "running_slope": 0.02, "cross_slope": 0.01, "surface": "paved",
        "confidence": 1.0,
    }])
    full_regs = tmp_path / "regs_full.jsonl"
    _write_jsonl(full_regs, [{
        "regulation_id": "reg_full", "x": 10.5, "y": 0.5, "frame": "map",
        "legal_stop": True, "legal_basis": "audited posted curb regulation permits passenger loading",
        "source": "manual_posted_sign_audit", "evidence_tier": "A_manual_audit", "audited": True,
        "confidence": 1.0,
    }])
    out3 = tmp_path / "pudo_verified.jsonl"
    subprocess.check_call([
        sys.executable, "scripts/build_pudo_evidence.py",
        "--scene_dataset_dir", str(scene_dir), "--accessibility_graph_dir", str(graph_dir),
        "--pudo_candidate_source", str(candidates), "--curb_inventory_jsonl", str(full_inventory),
        "--curb_regulation_jsonl", str(full_regs), "--output_pudo_evidence_jsonl", str(out3),
    ])
    verified = [r for r in read_jsonl(out3) if r.get("candidate_source") == "official_passenger_pickup_bay"]
    assert verified[0]["paper_eligible"] is True
