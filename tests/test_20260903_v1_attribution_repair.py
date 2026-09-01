from __future__ import annotations

from dataclasses import replace


def _transition():
    from capplan.data.accessibility_layer import synthetic_accessibility_graph
    from capplan.data.pudo_interface_layer import synthetic_pudo_anchors, synthetic_vehicle_interface
    from capplan.planning.transition_generator import TransitionGenerator
    eid = "v1_attr"
    graph = synthetic_accessibility_graph(eid)
    transitions = TransitionGenerator().generate(
        eid, graph, synthetic_pudo_anchors(eid, graph=graph), synthetic_vehicle_interface(eid)
    )
    return next(t for t in transitions if t.resource_evidence)


def test_no_casa_net_ablation_uses_deterministic_heuristic_predictor():
    from capplan.models.casa_net import CASANet
    from capplan.models.predictors import HeuristicTransitionPredictor

    net = CASANet(mode="learned", disabled=True, checkpoint=None, device="cpu")
    assert isinstance(net.predictor, HeuristicTransitionPredictor)


def test_edge_validity_is_not_folded_into_dynamic_availability(monkeypatch):
    from capplan.models.predictors import LearnedLinearTransitionPredictor

    t = _transition()
    t = replace(t, availability=0.5)
    pred = LearnedLinearTransitionPredictor({}, device="cpu")
    monkeypatch.setattr(
        pred, "_predict_heads_batch",
        lambda transitions, context=None: [(0.001, 0.5, 0.8, None, None) for _ in transitions],
    )
    out = pred.predict([t])[t.transition_id]
    assert abs(out.dynamic_availability - 0.4) < 1e-9
    assert abs(out.edge_validity - 0.001) < 1e-9


def test_categorical_evidence_is_never_overwritten_by_numeric_demand_head(monkeypatch):
    from capplan.data.schemas import ResourceEvidence
    from capplan.models.predictors import LearnedLinearTransitionPredictor

    t = _transition()
    t = replace(t, resource_evidence=[
        ResourceEvidence("ramp", "categorical", True, source="vehicle_spec"),
        ResourceEvidence("slope", "upper", 0.07, source="map"),
    ])
    pred = LearnedLinearTransitionPredictor({}, device="cpu")
    monkeypatch.setattr(
        pred, "_predict_heads_batch",
        lambda transitions, context=None: [(0.9, 0.5, 1.0, {"ramp": 0.13, "slope": 0.09}, {"ramp": 4.0, "slope": 0.01})],
    )
    out = pred.predict([t])[t.transition_id]
    ev = {x.resource_name: x for x in out.typed_evidence}
    assert ev["ramp"].value is True
    assert ev["ramp"].sigma == 0.0
    assert abs(float(ev["slope"].value) - 0.09) < 1e-9
    assert abs(float(ev["slope"].sigma) - 0.01) < 1e-9


def test_counterfactual_metrics_expose_all_fail_collapse_despite_high_crsp():
    from capplan.evaluation.metrics import compute_all_metrics

    pairs = []
    # 9 stable both-fail oracle pairs and 1 oracle success flip. A collapsed model
    # predicts both-fail for all pairs, so aggregate outcome accuracy looks high
    # while success-flip recall correctly reveals complete failure.
    for i in range(9):
        pairs.append({
            "counterfactual_axis": "x", "oracle_changed": False,
            "oracle_weak_success": False, "oracle_strict_success": False,
            "model_weak_success": False, "model_strict_success": False,
            "outcomes_match_oracle": True, "response_correct": True,
        })
    pairs.append({
        "counterfactual_axis": "x", "oracle_changed": True,
        "oracle_weak_success": True, "oracle_strict_success": False,
        "model_weak_success": False, "model_strict_success": False,
        "outcomes_match_oracle": False, "response_correct": False,
    })
    m = compute_all_metrics([], pairs)
    assert m["CRsp"] == 0.9
    assert m["CF_outcome_pair_accuracy"] == 0.9
    assert m["CF_success_flip_recall"] == 0.0
    assert m["CF_response_accuracy_oracle_changed"] == 0.0
