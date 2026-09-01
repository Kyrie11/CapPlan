from __future__ import annotations

import importlib.util
from dataclasses import replace
from pathlib import Path

import numpy as np


def _load_train_module():
    spec = importlib.util.spec_from_file_location("train_casa_reviewfix9", Path("scripts/train_casa.py"))
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_review_bundle_dispatch_uses_reviewfix9_guard_not_reviewfix7():
    text = Path("scripts/build_abilitybench_data0_20260817.sh").read_text(encoding="utf-8")
    start = text.index("hybrid_review_bundle() {")
    end = text.index("\n}\n", start)
    body = text[start:end]
    assert "reviewfix9_review_guard" in body
    assert "reviewfix7_runtime_guard" not in body
    assert "reviewfix9-preflight" in text


def test_paper_safe_v2_preserves_structural_relations_but_masks_targets():
    from capplan.data.accessibility_layer import synthetic_accessibility_graph
    from capplan.data.pudo_interface_layer import synthetic_pudo_anchors, synthetic_vehicle_interface
    from capplan.models.casa_features import FeatureVocab, encode_transition
    from capplan.planning.transition_generator import TransitionGenerator

    eid = "paper_safe_v2"
    graph = synthetic_accessibility_graph(eid)
    t = TransitionGenerator().generate(eid, graph, synthetic_pudo_anchors(eid, graph=graph), synthetic_vehicle_interface(eid))[0]
    v = FeatureVocab()
    x = encode_transition(t, v, feature_policy="paper_safe_v2")
    assert x[0] == float(v.actions.index(t.action))
    assert x[1] == float(v.phases.index(t.from_phase))
    assert x[2] == float(v.phases.index(t.to_phase))
    assert x[5] == float(t.cost)
    for idx in [3, 4, 6, 7, 8, 10]:
        assert x[idx] == 0.0


def test_normalization_preserves_relation_category_ids():
    mod = _load_train_module()
    x = np.array([[1, 2, 3, 10.0], [4, 5, 6, 20.0]], dtype=np.float32)
    mean, std = mod._normalization_stats(x)
    xn = (x - mean) / std
    assert np.allclose(xn[:, :3], x[:, :3])
    assert abs(float(xn[:, 3].mean())) < 1e-6


def test_planner_search_starts_from_real_request_entrance_anchor():
    from capplan.data.accessibility_layer import synthetic_accessibility_graph
    from capplan.data.capability_contracts import default_contract
    from capplan.data.pudo_interface_layer import synthetic_pudo_anchors, synthetic_vehicle_interface
    from capplan.planning.planner import CapPlanPlanner
    from capplan.planning.transition_generator import TransitionGenerator

    eid = "real_origin_runtime"
    graph = synthetic_accessibility_graph(eid)
    pudo = synthetic_pudo_anchors(eid, graph=graph)
    vehicle = synthetic_vehicle_interface(eid)
    transitions = TransitionGenerator().generate(eid, graph, pudo, vehicle)
    real_anchor = "entrance_real_42"
    transitions = [replace(t, from_anchor=real_anchor) if t.from_phase == "origin" and t.action == "access" else t for t in transitions]
    result = CapPlanPlanner().plan(
        eid,
        default_contract("passenger"),
        graph,
        pudo,
        vehicle,
        transitions=transitions,
        trip_context={"origin_entrance_id": real_anchor},
    )
    assert result.success
    assert result.diagnostics.get("initial_state_source") == "caller_request_anchor"
    assert (real_anchor, "origin") in result.diagnostics.get("initial_states", [])


def test_counterfactual_response_is_scored_against_oracle_change():
    from capplan.evaluation.closed_loop import ClosedLoopRunner

    pair = {"episode_id": "e", "weak_passenger_id": "e:base", "strict_passenger_id": "e:strict", "counterfactual_axis": "min_width"}
    results = {
        ("e", "e:base"): {"passenger_complete": True, "selected_transitions": ["a", "b"]},
        ("e", "e:strict"): {"passenger_complete": True, "selected_transitions": ["a", "b"]},
    }
    oracle_skeletons = {
        ("e", "e:base"): {"transitions": ["a", "b"]},
        ("e", "e:strict"): {"transitions": ["a", "c"]},
    }
    rows = ClosedLoopRunner._evaluate_counterfactual_pairs([pair], results, oracle_skeletons, {})
    assert rows[0]["oracle_changed"] is True
    assert rows[0]["model_changed"] is False
    assert rows[0]["response_correct"] is False


def test_binary_completion_value_loss_uses_bce():
    from capplan.models.losses import casa_loss

    out = casa_loss(
        np.array([0.5]), np.array([1.0]),
        np.array([0.5]), np.array([1.0]),
    )
    assert abs(out["L_value"] - np.log(2.0)) < 1e-6


def test_typed_demand_loss_uses_resource_normalizers_equally_for_equal_normalized_errors():
    import numpy as np
    from capplan.models.losses import masked_normalized_huber

    # 100 m distance error and 0.1 slope error are both one normalized unit.
    pred = np.array([[200.0, 0.20]], dtype=float)
    target = np.array([[100.0, 0.10]], dtype=float)
    mask = np.ones_like(pred)
    scales = np.array([100.0, 0.10], dtype=float)
    total = masked_normalized_huber(pred, target, mask, scale=scales)
    assert abs(total - 0.5) < 1e-9


def test_categorical_resources_are_not_regressed_as_numeric_typed_demand():
    from types import SimpleNamespace
    from capplan.models.casa_dataset import CASADataset
    from capplan.models.casa_features import FeatureVocab

    fake_self = SimpleNamespace(vocab=FeatureVocab())
    transition = SimpleNamespace(resource_evidence=[
        SimpleNamespace(resource_name="step_free", missing=False, value=True),
        SimpleNamespace(resource_name="slope", missing=False, value=0.07),
    ])
    values, masks = CASADataset._demand_target(fake_self, transition)
    rid = {name: i for i, name in enumerate(fake_self.vocab.resources)}
    assert masks[rid["step_free"]] == 0.0
    assert masks[rid["slope"]] == 1.0
    assert abs(values[rid["slope"]] - 0.07) < 1e-9


def test_nuplan_metric_import_preserves_integrated_semantics_and_standard_scores():
    from scripts.import_nuplan_vehicle_metrics import normalize_vehicle_metric_row

    row = {
        "route_completion": 0.91,
        "no_ego_at_fault_collisions": True,
        "drivable_area_compliance": 1.0,
        "time_to_collision_within_bound": 0.8,
        "speed_limit_compliance": True,
        "driving_direction_compliance": 0.95,
        "ego_is_comfortable": True,
        "score": 0.77,
        "capplan_method_specific_closed_loop": True,
    }
    scene = {"episode_id": "e", "route_length_m": 1000.0}
    out = normalize_vehicle_metric_row(row, scene)
    assert out["at_fault_collision_rate"] == 0.0
    assert out["speed_limit_compliance"] == 1.0
    assert out["comfort"] == 1.0
    assert out["nuplan_score"] == 0.77
    assert out["capplan_method_specific_closed_loop"] is True


def test_metrics_report_planner_latency_and_detailed_nuplan_scores():
    from capplan.evaluation.metrics import compute_all_metrics

    rows = [
        {"planning_latency_ms": 10.0, "search_expansions": 4, "at_fault_collision_rate": 0.0, "nuplan_score": 0.8},
        {"planning_latency_ms": 30.0, "search_expansions": 8, "at_fault_collision_rate": 1.0, "nuplan_score": 0.6},
    ]
    m = compute_all_metrics(rows, [])
    assert m["PlannerLatency_ms_mean"] == 20.0
    assert 20.0 < m["PlannerLatency_ms_p95"] <= 30.0
    assert m["nuplan::at_fault_collision_rate"] == 0.5
    assert m["nuplan::nuplan_score"] == 0.7
