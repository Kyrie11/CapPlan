from pathlib import Path

from capplan.data.schemas import CandidateTransition, ResourceEvidence, TransitionTests
from capplan.models.casa_features import FeatureVocab
from capplan.models.predictors import LearnedLinearTransitionPredictor
from capplan.evaluation.closed_loop import ClosedLoopRunner


def _transition(value=0.05, sigma=0.005, missing=False):
    return CandidateTransition(
        transition_id="e0", episode_id="ep0", from_anchor="o", to_anchor="p",
        from_phase="origin", to_phase="access", action="access",
        resource_evidence=[ResourceEvidence("slope", "upper", None if missing else value, sigma=sigma, confidence=0.9, source="accessibility_map", missing=missing)],
        availability=1.0, map_confidence=0.9, interface={}, dynamic={}, cost=1.0,
        completion_value=0.5, tests=TransitionTests(),
    )


def _tokens():
    vocab = FeatureVocab()
    return [{
        "resource_id": vocab.resources.index("slope"),
        "kind_id": 1,
        "operator_id": 0,
        "threshold_value": 0.08,
        "threshold_mask": 1,
        "phase_mask": [0, 1, 0, 0, 0, 0, 0, 0],
        "beta_tau": 1.0,
        "hard": 1,
    }]


def _predictor(evidence_grounded):
    p = LearnedLinearTransitionPredictor(checkpoint={}, evidence_grounded_runtime=evidence_grounded)
    p._predict_heads_batch = lambda transitions, context=None: [
        (0.9, 0.8, 1.0, {"slope": 0.50}, {"slope": 0.90}) for _ in transitions
    ]
    return p


def test_v2_hard_channel_preserves_explicit_physical_evidence():
    e = _transition()
    pred = _predictor(True).predict([e], context={"tokens": _tokens()})["e0"]
    ev = pred.typed_evidence[0]
    assert ev.value == 0.05
    assert ev.sigma == 0.005
    assert pred.learned_typed_demand["slope"] == 0.50
    assert pred.learned_uncertainty["slope"] == 0.90
    assert pred.evidence_policy == "evidence_grounded_v2"
    assert pred.learned_feasibility_prior < 0.01


def test_v1_overwrite_behavior_remains_available_for_control_ablation():
    e = _transition()
    pred = _predictor(False).predict([e], context={"tokens": _tokens()})["e0"]
    ev = pred.typed_evidence[0]
    assert ev.value == 0.50
    assert ev.sigma == 0.90
    assert pred.evidence_policy == "learned_overwrite_v1"


def test_v2_missing_hard_evidence_stays_missing():
    e = _transition(missing=True)
    pred = _predictor(True).predict([e], context={"tokens": _tokens()})["e0"]
    ev = pred.typed_evidence[0]
    assert ev.missing is True
    assert ev.value is None


def test_eval_fast_path_does_not_materialize_accessibility_graph(monkeypatch, tmp_path):
    import capplan.evaluation.closed_loop as cl
    monkeypatch.setattr(cl, "load_accessibility_graph", lambda *a, **k: (_ for _ in ()).throw(AssertionError("graph load should be skipped")))
    g = ClosedLoopRunner._graph_for_episode(tmp_path, "ep0", [_transition()])
    assert g.episode_id == "ep0"
    assert g.nodes == [] and g.edges == []
    assert g.metadata["evaluation_fast_path"] == "saved_transitions"
