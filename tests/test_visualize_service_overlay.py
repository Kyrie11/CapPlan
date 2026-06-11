import json
import subprocess
import sys
from pathlib import Path

from capplan.utils.serialization import write_jsonl


def test_visualize_resolves_nuplan_suffix_and_fails_nonempty(tmp_path):
    dataset = tmp_path / "dataset"
    graph_dir = dataset / "accessibility_graphs"
    graph_dir.mkdir(parents=True)
    eid = "nuplan_abcdef123456"
    write_jsonl(dataset / "episodes.jsonl", [{"episode_id": eid}])
    write_jsonl(graph_dir / f"{eid}.nodes.jsonl", [
        {"node_id": "origin", "x": 0.0, "y": 0.0, "kind": "entrance"},
        {"node_id": "destination", "x": 10.0, "y": 0.0, "kind": "entrance"},
        {"node_id": "nuplan_pickup_0", "x": 3.0, "y": 1.0, "kind": "pudo"},
    ])
    write_jsonl(graph_dir / f"{eid}.edges.jsonl", [
        {"edge_id": "e0", "from_node": "origin", "to_node": "nuplan_pickup_0", "length_m": 3.2, "geometry": [[0.0, 0.0], [3.0, 1.0]]},
        {"edge_id": "e1", "from_node": "nuplan_pickup_0", "to_node": "destination", "length_m": 7.1, "geometry": [[3.0, 1.0], [10.0, 0.0]]},
    ])
    write_jsonl(dataset / "pudo_anchors.jsonl", [{
        "episode_id": eid,
        "anchor_id": "nuplan_pickup_0",
        "curb_pose": {"x": 3.0, "y": 1.0},
        "stop_pose": {"x": 3.0, "y": -1.0},
        "source": "nuplan_route_map",
    }])
    write_jsonl(dataset / "entrances.jsonl", [
        {"episode_id": eid, "anchor_id": "origin", "pose": {"x": 0.0, "y": 0.0}, "source": "nuplan_scene_proxy"},
        {"episode_id": eid, "anchor_id": "destination", "pose": {"x": 10.0, "y": 0.0}, "source": "nuplan_scene_proxy"},
    ])
    out = tmp_path / "vis.png"
    proc = subprocess.run(
        [sys.executable, "scripts/visualize_service_overlay.py", "--dataset_dir", str(dataset), "--episode_id", "abcdef123456", "--output", str(out)],
        cwd=Path(__file__).resolve().parents[1],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    payload = json.loads(proc.stdout)
    assert payload["episode_id"] == eid
    assert out.exists()
