from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from capplan.data.accessibility_layer import (
    materialize_prepared_accessibility_graph,
    shortest_accessible_path_stats,
    shortest_accessible_path_stats_from_tree,
    shortest_path_tree,
)
from capplan.data.pudo_interface_layer import synthetic_vehicle_interface
from capplan.data.schemas import AccessibilityEdge, AccessibilityGraph, AccessibilityNode, PUDOAnchor, Pose2D, to_dict
from capplan.planning.transition_generator import TransitionGenerator
from capplan.utils.serialization import write_jsonl

ROOT = Path(__file__).resolve().parents[1]


def _line_graph_and_pudos() -> tuple[AccessibilityGraph, list[PUDOAnchor]]:
    eid = "route_aware"
    nodes = [AccessibilityNode("origin", 0.0, 0.0, "entrance")]
    for i in range(1, 7):
        nodes.append(AccessibilityNode(f"n{i}", float(i * 10), 0.0, "sidewalk"))
    nodes.append(AccessibilityNode("destination", 70.0, 0.0, "entrance"))
    edges = []
    chain = ["origin", *[f"n{i}" for i in range(1, 7)], "destination"]
    for a, b in zip(chain, chain[1:]):
        edges.append(AccessibilityEdge(f"{a}->{b}", a, b, 10.0, 2.0, 0.02, 0.01, "paved", True, True))
    graph = AccessibilityGraph(eid, nodes, edges)
    # Deliberately reverse the input order.  Old rows[:4] selection would choose
    # the four farthest pickup anchors from origin.
    pudos = []
    for i in range(6, 0, -1):
        pose = Pose2D(float(i * 10), 0.0)
        pudos.append(PUDOAnchor(
            anchor_id=f"p{i}", episode_id=eid, kind="pickup_dropoff",
            curb_pose=pose, stop_pose=pose, side="right", legal_stop=True,
            adjacent_ped_node_id=f"n{i}", blockage_risk=0.05,
            map_confidence=0.95, dynamic_confidence=0.95,
            hybrid_evidence_complete=True, hybrid_eligible=True,
        ))
    return graph, pudos


def test_route_aware_pudo_selection_uses_service_distance_not_input_order():
    graph, pudos = _line_graph_and_pudos()
    ts = TransitionGenerator().generate(
        graph.episode_id, graph, pudos, synthetic_vehicle_interface(graph.episode_id),
        "origin", "destination",
    )
    pickups = [t.to_anchor for t in ts if t.action == "access"]
    assert pickups == ["p1", "p2", "p3", "p4"]
    dropoffs = sorted({t.from_anchor for t in ts if t.action == "egress" and t.from_phase == "alight"})
    assert dropoffs == ["p3", "p4", "p5", "p6"]


def test_shortest_path_tree_stats_are_exactly_equivalent():
    graph, _ = _line_graph_and_pudos()
    direct = shortest_accessible_path_stats(graph, "origin", "n5")
    dist, prev = shortest_path_tree(graph, "origin")
    batched = shortest_accessible_path_stats_from_tree(graph, "origin", "n5", dist, prev)
    assert batched == direct


def test_prepared_graph_materialization_is_byte_identical_and_audited(tmp_path: Path):
    graph, _ = _line_graph_and_pudos()
    src = tmp_path / "prepared"
    src.mkdir()
    nodes = src / f"{graph.episode_id}.nodes.jsonl"
    edges = src / f"{graph.episode_id}.edges.jsonl"
    write_jsonl(nodes, (to_dict(n) for n in graph.nodes))
    write_jsonl(edges, (to_dict(e) for e in graph.edges))
    out = tmp_path / "dataset"
    mode = materialize_prepared_accessibility_graph(out, graph, nodes, edges)
    dst_nodes = out / "accessibility_graphs" / nodes.name
    dst_edges = out / "accessibility_graphs" / edges.name
    assert dst_nodes.read_bytes() == nodes.read_bytes()
    assert dst_edges.read_bytes() == edges.read_bytes()
    if mode == "hardlink_to_prepared":
        assert os.stat(dst_edges).st_ino == os.stat(edges).st_ino
    audit = json.loads((out / "accessibility_graphs" / f"{graph.episode_id}.audit.json").read_text())
    assert audit["edge_count"] == len(graph.edges)
    assert audit["storage_mode"] in {"hardlink_to_prepared", "copy_from_prepared"}


def test_diagnostics_cli_registers_fast_graph_scan():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "scripts/diagnose_capplan_outputs.py"), "--help"],
        check=True, capture_output=True, text=True,
    )
    assert "--fast_graph_scan" in proc.stdout


def test_reviewfix5_bundle_uses_fresh_dataset_context_and_reuses_only_graph_v3():
    text = (ROOT / "scripts/build_hybrid_review_bundle.py").read_text()
    assert 'EXPECTED_PIPELINE_VERSION = "abilitybench_data0_realism_v4_reviewfix5_hotfix1_20260828"' in text
    assert 'commands/hybrid_run_context.reviewfix5_dataset.json' in text
    assert 'return rel.startswith("hybrid_graph.")' in text


def test_hybrid_pudo_v6_records_dynamic_blockage_provenance():
    text = (ROOT / "scripts/build_hybrid_pudo_evidence.py").read_text()
    assert 'VERSION = "abilitybench_hybrid_pudo_v6_20260828"' in text
    assert '"kind": "derived"' in text
    assert '"source": "nuplan_agent_history"' in text
    assert '"nearest_agent_distance_dynamic_blockage_risk"' in text


def test_reviewfix5_preflight_executes_bash_helpers(tmp_path: Path):
    env = dict(os.environ)
    env.update({
        "CAP_HOME": str(ROOT),
        "DATA_ROOT": str(tmp_path / "data"),
        "CAP_DATA": str(tmp_path / "data"),
        "CONFIG": str(ROOT / "configs/abilitybench_nuplan_real_data0.yaml"),
    })
    proc = subprocess.run(
        ["bash", str(ROOT / "scripts/build_abilitybench_data0_20260817.sh"), "reviewfix5-preflight"],
        check=True, capture_output=True, text=True, env=env,
    )
    assert "CAPPLAN_REVIEWFIX5_RUNTIME_GUARD=PASS" in proc.stdout
    assert "CAPPLAN_REVIEWFIX5_HELPER_DEFINITIONS=present" in proc.stdout
    assert "CAPPLAN_REVIEWFIX5_HELPER_SMOKE=PASS" in proc.stdout
    assert "CAPPLAN_PIPELINE_VERSION=abilitybench_data0_realism_v4_reviewfix5_hotfix1_20260828" in proc.stdout
    assert "CAPPLAN_REVIEWFIX5_DIAGNOSE_FAST_GRAPH_SCAN=present" in proc.stdout


def test_reviewfix5_helper_is_not_inside_python_heredoc():
    text = (ROOT / "scripts/build_abilitybench_data0_20260817.sh").read_text(encoding="utf-8")
    ctx_start = text.index("<<'PYCTX5'")
    ctx_end = text.index("\nPYCTX5", ctx_start)
    heredoc_body = text[ctx_start:ctx_end]
    assert "write_reviewfix5_dataset_hashes()" not in heredoc_body
    assert text.index("write_reviewfix5_dataset_hashes()") > ctx_end


def test_reviewfix5_bundle_requires_fresh_hash_and_upstream_graph_lineage():
    text = (ROOT / "scripts/build_hybrid_review_bundle.py").read_text(encoding="utf-8")
    assert 'commands/reviewfix5_dataset_fix.sha256' in text
    assert 'reused_upstream_run_id' in text
    assert 'upstream_start_ns' in text
    assert 'path.stat().st_mtime_ns <= upstream_start_ns' in text


def test_reviewfix5_reused_graph_preflight_accepts_bound_reviewfix3_lineage(tmp_path: Path):
    import time
    data_root = tmp_path / "data"
    reports = data_root / "external" / "reports"
    commands = reports / "commands"
    commands.mkdir(parents=True)
    start_ns = time.time_ns() - 2_000_000_000
    context = {
        "run_id": "reviewfix3_test_lineage",
        "start_time_ns": start_ns,
    }
    (commands / "hybrid_run_context.reviewfix3.json").write_text(
        json.dumps(context), encoding="utf-8"
    )
    graph_report = {
        "status": "PASS",
        "version": "abilitybench_hybrid_accessibility_v3_20260825",
        "numeric_field_ranges": {"slope": {"max": 0.45}},
    }
    for split in ("train", "val", "test"):
        for city in ("boston", "pittsburgh", "vegas", "singapore"):
            (reports / f"hybrid_graph.{split}.{city}.json").write_text(
                json.dumps(graph_report), encoding="utf-8"
            )
    env = dict(os.environ)
    env.update({"CAP_HOME": str(ROOT), "DATA_ROOT": str(data_root), "CAP_DATA": str(data_root)})
    proc = subprocess.run(
        ["bash", str(ROOT / "scripts/build_abilitybench_data0_20260817.sh"), "reviewfix5-reused-graph-preflight"],
        check=True, capture_output=True, text=True, env=env,
    )
    assert "CAPPLAN_REVIEWFIX5_REUSED_GRAPH_REPORTS=12/12" in proc.stdout
    assert "CAPPLAN_REVIEWFIX5_REUSED_GRAPH_PREFLIGHT=PASS" in proc.stdout
