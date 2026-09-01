from __future__ import annotations

from capplan.data.schemas import CandidateTransition, ResourceEvidence
from capplan.evaluation.metrics import compute_all_metrics
from capplan.models.predictors import LearnedLinearTransitionPredictor


def _transition() -> CandidateTransition:
    return CandidateTransition(
        transition_id="t0",
        episode_id="ep0",
        from_anchor="o",
        to_anchor="p0",
        from_phase="origin",
        to_phase="access",
        action="access",
        resource_evidence=[
            ResourceEvidence("slope", "upper", 0.10, sigma=0.05, source="saved"),
            ResourceEvidence("step_free", "categorical", True, sigma=0.0, source="saved"),
        ],
        availability=1.0,
        map_confidence=1.0,
        interface={},
        dynamic={},
    )


def _patched_predictor(*, no_demand: bool, no_uncertainty: bool) -> LearnedLinearTransitionPredictor:
    predictor = LearnedLinearTransitionPredictor(
        {}, no_learned_demand=no_demand, no_learned_uncertainty=no_uncertainty
    )
    predictor._predict_heads_batch = lambda transitions, context=None: [
        (0.9, 0.5, 1.0, {"slope": 0.90}, {"slope": 0.70})
        for _ in transitions
    ]
    return predictor


def test_learned_demand_and_uncertainty_are_independently_ablatable() -> None:
    t = _transition()

    # Symbolic mean + learned sigma: no_learned_demand must NOT silently disable
    # the uncertainty head.
    pred = _patched_predictor(no_demand=True, no_uncertainty=False).predict([t])["t0"]
    slope = next(ev for ev in pred.typed_evidence if ev.resource_name == "slope")
    assert slope.value == 0.10
    assert slope.sigma == 0.70

    # Learned mean + symbolic sigma.
    pred = _patched_predictor(no_demand=False, no_uncertainty=True).predict([t])["t0"]
    slope = next(ev for ev in pred.typed_evidence if ev.resource_name == "slope")
    assert slope.value == 0.90
    assert slope.sigma == 0.05

    # Fully symbolic factorial control.
    pred = _patched_predictor(no_demand=True, no_uncertainty=True).predict([t])["t0"]
    slope = next(ev for ev in pred.typed_evidence if ev.resource_name == "slope")
    assert slope.value == 0.10
    assert slope.sigma == 0.05

    # Categorical evidence is authoritative in every case.
    step_free = next(ev for ev in pred.typed_evidence if ev.resource_name == "step_free")
    assert step_free.value is True


def test_oracle_referenced_passenger_completion_metrics_penalize_false_accepts() -> None:
    episodes = [
        {"passenger_complete": True, "oracle_label_available": True, "oracle_passenger_complete": True},
        {"passenger_complete": True, "oracle_label_available": True, "oracle_passenger_complete": False},
        {"passenger_complete": False, "oracle_label_available": True, "oracle_passenger_complete": True},
        {"passenger_complete": False, "oracle_label_available": True, "oracle_passenger_complete": False},
    ]
    m = compute_all_metrics(episodes, [])
    assert m["PCR"] == 0.5
    assert m["OraclePCR"] == 0.5
    assert m["PCDecisionTP"] == 1.0
    assert m["PCDecisionFP"] == 1.0
    assert m["PCDecisionFN"] == 1.0
    assert m["PCDecisionTN"] == 1.0
    assert m["PCDecisionPrecision"] == 0.5
    assert m["PCDecisionRecall"] == 0.5
    assert m["PCFalseAcceptRate"] == 0.5
    assert m["PCFalseRejectRate"] == 0.5


def test_counterfactual_success_flip_precision_and_support_are_reported() -> None:
    pairs = [
        {
            "counterfactual_axis": "width",
            "oracle_changed": True,
            "response_correct": True,
            "outcomes_match_oracle": True,
            "oracle_weak_success": True,
            "oracle_strict_success": False,
            "model_weak_success": True,
            "model_strict_success": False,
            "episode_id": "e1", "weak_passenger_id": "b", "strict_passenger_id": "s1",
        },
        {
            "counterfactual_axis": "width",
            "oracle_changed": False,
            "response_correct": False,
            "outcomes_match_oracle": False,
            "oracle_weak_success": False,
            "oracle_strict_success": False,
            "model_weak_success": True,
            "model_strict_success": False,
            "episode_id": "e2", "weak_passenger_id": "b", "strict_passenger_id": "s2",
        },
    ]
    m = compute_all_metrics([], pairs)
    assert m["CF_oracle_success_flip_count"] == 1.0
    assert m["CF_model_success_flip_count"] == 2.0
    assert m["CF_success_flip_recall"] == 1.0
    assert m["CF_success_flip_precision"] == 0.5
    assert m["CF_success_flip_support_axis::width"] == 1.0
