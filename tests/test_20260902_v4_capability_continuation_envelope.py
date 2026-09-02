from __future__ import annotations

from capplan.data.schemas import CandidateTransition, CapabilityClause, CapabilityContract, ResourceEvidence
from capplan.models.predictors import HeuristicTransitionPredictor
from capplan.planning.capability_continuation_envelope import build_continuation_envelope, evaluate_continuation
from capplan.planning.typed_safe_budget_search import SearchConfig, SearchLabel, TypedSafeBudgetSearch
from capplan.semantics.capability_compiler import CapabilityCompiler
from capplan.semantics.service_automaton import ServiceAutomaton
from capplan.semantics.typed_resource_algebra import init_ledger


def _edge(tid, a, b, p, q, action, ride, cost=1.0):
    return CandidateTransition(
        transition_id=tid,
        episode_id="ep",
        from_anchor=a,
        to_anchor=b,
        from_phase=p,
        to_phase=q,
        action=action,
        resource_evidence=(
            [ResourceEvidence("ride_time_s", "cumulative", ride, sigma=0.0, source="test")]
            if ride is not None else []
        ),
        availability=1.0,
        map_confidence=1.0,
        interface={},
        dynamic={},
        cost=cost,
    )


def _contract(limit=10.0):
    return CapabilityContract(
        "p",
        [CapabilityClause("ride_time_s", ["ride"], "<=", limit, "cumulative", beta_tau=0.0, clause_id="ride")],
    )


def _graph():
    # The bad branch is one-step feasible (6 <= 10) but its forced suffix adds
    # another 6. The good branch consumes 3 + 3 and reaches destination.
    return [
        _edge("bad0", "B", "RB", "board", "ride", "ride", 6.0, cost=0.1),
        _edge("good0", "B", "RG", "board", "ride", "ride", 3.0, cost=1.0),
        _edge("bad1", "RB", "AB", "ride", "alight", "alight", 6.0, cost=0.1),
        _edge("good1", "RG", "AG", "ride", "alight", "alight", 3.0, cost=1.0),
        _edge("bad2", "AB", "EB", "alight", "egress", "egress", None, cost=0.1),
        _edge("good2", "AG", "EG", "alight", "egress", "egress", None, cost=1.0),
        _edge("bad3", "EB", "D", "egress", "destination", "egress", None, cost=0.1),
        _edge("good3", "EG", "D", "egress", "destination", "egress", None, cost=1.0),
    ]


def test_cce_detects_unavoidable_future_resource_violation():
    transitions = _graph()
    compiled = CapabilityCompiler().compile(_contract())
    pred = HeuristicTransitionPredictor().predict(transitions)
    env = build_continuation_envelope(compiled, transitions, pred, ServiceAutomaton())

    ledger = init_ledger({"ride_time_s"})
    # After taking bad0 the forward ledger contains 6 seconds.
    from capplan.semantics.typed_resource_algebra import update_value
    from capplan.semantics.resource_registry import DEFAULT_REGISTRY
    ledger["ride_time_s"] = update_value(ledger["ride_time_s"], 6.0, DEFAULT_REGISTRY.get("ride_time_s"))
    dec = evaluate_continuation(("RB", "ride"), ledger, compiled, env)
    assert dec.structural_reachable
    assert dec.impossible
    assert "ride_time_s" in dec.failed_resources


def test_cce_preserves_success_and_prunes_dead_branch():
    transitions = _graph()
    compiled = CapabilityCompiler().compile(_contract())
    pred = HeuristicTransitionPredictor().predict(transitions)

    base = TypedSafeBudgetSearch(
        ServiceAutomaton(),
        config=SearchConfig(
            no_completion_value_guidance=True,
            lambda_edge_validity=0.0,
            lambda_learned_feasibility=0.0,
            lambda_frontier_ranker=0.0,
            use_continuation_envelope=False,
        ),
    )
    v4 = TypedSafeBudgetSearch(
        ServiceAutomaton(),
        config=SearchConfig(
            no_completion_value_guidance=True,
            lambda_edge_validity=0.0,
            lambda_learned_feasibility=0.0,
            lambda_frontier_ranker=0.0,
            use_continuation_envelope=True,
            continuation_pruning=True,
            lambda_continuation_cost=0.0,
            lambda_continuation_margin=0.0,
        ),
    )

    sk0, _, d0 = base.search("ep", compiled, transitions, pred, initial_anchor="B", initial_phase="board")
    sk1, _, d1 = v4.search("ep", compiled, transitions, pred, initial_anchor="B", initial_phase="board")
    assert sk0 is not None and sk0.accepted
    assert sk1 is not None and sk1.accepted
    assert sk0.transitions[-1] == "good3"
    assert sk1.transitions[-1] == "good3"
    assert d1["continuation_pruned"] >= 1
    assert d1["expansions"] < d0["expansions"]


def test_cce_can_be_priority_only_without_changing_hard_semantics():
    transitions = _graph()
    compiled = CapabilityCompiler().compile(_contract())
    pred = HeuristicTransitionPredictor().predict(transitions)
    search = TypedSafeBudgetSearch(
        ServiceAutomaton(),
        config=SearchConfig(
            no_completion_value_guidance=True,
            lambda_edge_validity=0.0,
            lambda_learned_feasibility=0.0,
            lambda_frontier_ranker=0.0,
            use_continuation_envelope=True,
            continuation_pruning=False,
            lambda_continuation_cost=0.2,
            lambda_continuation_margin=0.35,
        ),
    )
    sk, _, diag = search.search("ep", compiled, transitions, pred, initial_anchor="B", initial_phase="board")
    assert sk is not None and sk.accepted
    assert diag["continuation_pruned"] == 0
    assert diag["continuation_scored"] > 0
