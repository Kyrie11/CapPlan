"""Dependency-free checks for the handoff data/metric extension."""
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

from capplan.data.handoff_schema import validate_temporal_overlay
from capplan.data.schemas import AccessibilityEdge, AccessibilityGraph, AccessibilityNode
from capplan.evaluation.handoff_metrics import compute_handoff_metrics


def _load_script(name: str):
    path = Path(__file__).resolve().parents[1] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


overlay_builder = _load_script("build_handoff_temporal_overlays")
pair_miner = _load_script("mine_handoff_recourse_pairs")


class HandoffUpgradeTests(unittest.TestCase):
    def test_overlay_preserves_irreversible_distance_witness(self):
        pair = {
            "episode_id": "e0", "origin_anchor": "O", "a_anchor": "A", "b_anchor": "B",
            "distances_m": {"origin_to_a": 130.0, "origin_to_b": 170.0, "a_to_b": 120.0},
            "screening_contract": {"distance_budget_m": 200.0, "already_consumed_m": 0.0},
            "two_candidate_distance_and_vehicle_witness": False,
            "evidence_class": "fixture_geometry",
        }
        row = overlay_builder.build_overlay(pair, 0, 90.0, 5.0, 3.0, 0.5)
        validate_temporal_overlay(row)
        self.assertTrue(row["motivation_witness"]["both_direct_choices_within_budget"])
        self.assertTrue(row["motivation_witness"]["a_then_b_exceeds_budget"])
        self.assertFalse(row["motivation_witness"]["vehicle_a_to_b_verified_for_event_time"])
        self.assertEqual(row["initial_execution_state"]["walked_or_rolled_m"], 0.0)

    def test_metric_denominators_and_recourse(self):
        rows = [
            {"profile_id": "p1", "handoff_confirmed": True, "planner_accepted": True,
             "passenger_commitment_issued": True, "recourse_preserved_after_commit": True,
             "robust_recourse_existed_at_commit": True, "evidence_valid_at_commit": True,
             "executed_passenger_distance_m": 100, "passenger_waiting_time_s": 20,
             "passenger_arrival_s": 30, "vehicle_arrival_s": 35},
            {"profile_id": "p2", "handoff_confirmed": False, "planner_accepted": True,
             "passenger_commitment_issued": True, "recourse_preserved_after_commit": False,
             "robust_recourse_existed_at_commit": False, "evidence_valid_at_commit": False,
             "executed_passenger_distance_m": 250, "passenger_waiting_time_s": 40,
             "passenger_arrival_s": 80, "vehicle_arrival_s": 50},
        ]
        metrics = compute_handoff_metrics(rows)
        self.assertEqual(metrics["outcomes"]["verified_handoff_completion_rate"], 0.5)
        self.assertEqual(metrics["recourse"]["post_commitment_recourse_loss_rate"], 0.5)
        self.assertEqual(metrics["recourse"]["false_commit_rate"], 0.5)
        self.assertEqual(metrics["recourse"]["stale_evidence_commit_rate"], 0.5)
        self.assertEqual(metrics["passenger_burden"]["executed_distance_m"]["p50"], 175.0)

    def test_empty_denominator_is_not_zero(self):
        metrics = compute_handoff_metrics([{"decision": "inconclusive"}])
        self.assertIsNone(metrics["outcomes"]["verified_handoff_completion_rate"])
        self.assertIsNone(metrics["recourse"]["post_commitment_recourse_loss_rate"])

    def test_pair_miner_does_not_invent_vehicle_reachability(self):
        nodes = [AccessibilityNode(node, 0, 0, "fixture") for node in ("O", "A", "B")]
        edges = [
            AccessibilityEdge("oa", "O", "A", 130, width_m=1.2, slope=0.01, cross_slope=0.01, step_free=True),
            AccessibilityEdge("ob", "O", "B", 170, width_m=1.2, slope=0.01, cross_slope=0.01, step_free=True),
            AccessibilityEdge("ab", "A", "B", 120, width_m=1.2, slope=0.01, cross_slope=0.01, step_free=True),
        ]
        graph = AccessibilityGraph("e0", nodes, edges)
        anchors = [
            {"anchor_id": "A", "kind": "pickup", "legal_stop": True},
            {"anchor_id": "B", "kind": "pickup", "legal_stop": True},
        ]
        args = SimpleNamespace(
            distance_budget_m=200.0, already_consumed_m=0.0, min_width_m=0.9,
            max_slope=0.08, max_cross_slope=0.04, min_confidence=0.0,
            require_step_free=True, event_time_s=90.0,
        )
        rows, _ = pair_miner.mine_episode({"episode_id": "e0", "origin_anchor": "O"}, anchors, graph, args, {})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["reroute_total_m"], 250.0)
        self.assertFalse(rows[0]["two_candidate_distance_and_vehicle_witness"])
        self.assertFalse(rows[0]["global_no_recovery_claim"])


if __name__ == "__main__":
    unittest.main()

