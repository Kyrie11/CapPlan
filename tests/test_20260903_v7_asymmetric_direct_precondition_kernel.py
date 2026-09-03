from __future__ import annotations

from capplan.data.schemas import CandidateTransition, CapabilityClause, CapabilityContract, ResourceEvidence, TransitionTests
from capplan.models.predictors import HeuristicTransitionPredictor
from capplan.planning.capability_precondition_antichain import (
    evaluate_precondition_antichain, evaluate_rejection_precondition_antichain,
)
from capplan.planning.capability_viability_kernel import build_capability_viability_kernel
from capplan.planning.direct_capability_precondition_kernel import build_direct_dual_precondition_kernel
from capplan.planning.typed_safe_budget_search import SearchConfig, TypedSafeBudgetSearch
from capplan.semantics.capability_compiler import CapabilityCompiler
from capplan.semantics.resource_registry import DEFAULT_REGISTRY
from capplan.semantics.service_automaton import ServiceAutomaton
from capplan.semantics.typed_resource_algebra import init_ledger, update_value


def _edge(tid, a, b, p, q, action, ride=None, *, cost=1.0, tests=None):
    return CandidateTransition(
        transition_id=tid, episode_id="ep", from_anchor=a, to_anchor=b,
        from_phase=p, to_phase=q, action=action,
        resource_evidence=([] if ride is None else [ResourceEvidence(
            "ride_time_s", "cumulative", ride, sigma=0.0, source="test_evidence"
        )]),
        availability=1.0, map_confidence=1.0, interface={}, dynamic={}, cost=cost,
        tests=tests or TransitionTests(),
    )


def _contract(limit=10.0):
    return CapabilityContract("p", [CapabilityClause(
        "ride_time_s", ["ride"], "<=", limit, "cumulative",
        beta_tau=0.0, clause_id="ride", source="passenger_contract",
    )])


def _v7_search(*, rejection=True, max_paths=256, max_depth=16):
    return TypedSafeBudgetSearch(ServiceAutomaton(), config=SearchConfig(
        no_completion_value_guidance=True,
        lambda_edge_validity=0.0,
        lambda_learned_feasibility=0.0,
        lambda_frontier_ranker=0.0,
        use_viability_kernel=True,
        viability_pruning=True,
        viability_typed_pruning=True,
        use_precondition_antichain=True,
        use_direct_dual_precondition_kernel=True,
        use_rejection_antichain=rejection,
        viability_use_proof_envelope=rejection,
        viability_max_paths_per_state=max_paths,
        viability_max_depth=max_depth,
    ))


def test_v7_direct_compiler_matches_v5_search_without_raw_suffix_enumeration():
    transitions = [
        _edge("bad0", "B", "RB", "board", "ride", "ride", 6.0, cost=0.1),
        _edge("good0", "B", "RG", "board", "ride", "ride", 3.0, cost=1.0),
        _edge("bad1", "RB", "AB", "ride", "alight", "alight", 6.0, cost=0.1),
        _edge("good1", "RG", "AG", "ride", "alight", "alight", 3.0, cost=1.0),
        _edge("bad2", "AB", "EB", "alight", "egress", "egress"),
        _edge("good2", "AG", "EG", "alight", "egress", "egress"),
        _edge("bad3", "EB", "D", "egress", "destination", "egress"),
        _edge("good3", "EG", "D", "egress", "destination", "egress"),
    ]
    compiled = CapabilityCompiler().compile(_contract())
    pred = HeuristicTransitionPredictor().predict(transitions)
    v5 = TypedSafeBudgetSearch(ServiceAutomaton(), config=SearchConfig(
        no_completion_value_guidance=True, lambda_edge_validity=0.0,
        lambda_learned_feasibility=0.0, lambda_frontier_ranker=0.0,
        use_viability_kernel=True, viability_pruning=True, viability_typed_pruning=True,
        use_precondition_antichain=False, viability_use_proof_envelope=False,
    ))
    v7 = _v7_search()
    sk5, _, d5 = v5.search("ep", compiled, transitions, pred, initial_anchor="B", initial_phase="board")
    sk7, _, d7 = v7.search("ep", compiled, transitions, pred, initial_anchor="B", initial_phase="board")
    assert sk5 is not None and sk7 is not None
    assert sk5.transitions == sk7.transitions
    assert d5["expansions"] == d7["expansions"]
    assert d7["precondition_raw_suffixes"] == 0
    assert d7["direct_precondition_build_candidates"] > 0
    assert d7["viability_path_checks"] == 0


def test_v7_acceptance_and_rejection_use_opposite_frontiers():
    transitions = [
        _edge("a", "S", "A1", "ride", "alight", "alight", 2.0),
        _edge("b", "S", "A2", "ride", "alight", "alight", 5.0),
        _edge("a2", "A1", "E1", "alight", "egress", "egress"),
        _edge("b2", "A2", "E2", "alight", "egress", "egress"),
        _edge("a3", "E1", "D", "egress", "destination", "egress"),
        _edge("b3", "E2", "D", "egress", "destination", "egress"),
    ]
    compiled = CapabilityCompiler().compile(_contract())
    pred = HeuristicTransitionPredictor().predict(transitions)
    kernel = build_capability_viability_kernel(
        transitions, pred, ServiceAutomaton(), enumerate_suffixes=False
    )
    dual = build_direct_dual_precondition_kernel(kernel, compiled, pred)
    state = ("S", "ride")
    # Existential feasibility only needs the 2s suffix. Diagnostic semantics must
    # retain the 5s branch as a distinct concrete boundary.
    assert len(dual.state_summaries(state)) == 1
    assert len(dual.state_rejections(state)) >= 2

    ledger = init_ledger({"ride_time_s"})
    ledger["ride_time_s"] = update_value(
        ledger["ride_time_s"], 9.0, DEFAULT_REGISTRY.get("ride_time_s")
    )
    adec = evaluate_precondition_antichain(state, ledger, compiled, dual)
    rdec = evaluate_rejection_precondition_antichain(state, ledger, compiled, dual)
    assert not adec.viable and adec.witness is not None
    assert rdec.witness is not None
    assert rdec.witness.resource_type == "ride_time_s"
    assert rdec.witness.signed_margin <= adec.witness.signed_margin


def test_v7_direct_proof_recovers_downstream_interface_without_crossing_typed_prefix():
    invalid = TransitionTests(interface_valid=False, reasons=["door_interface_invalid"])
    transitions = [
        _edge("p", "B", "S", "board", "ride", "ride", 4.0),
        _edge("invalid", "S", "X", "ride", "alight", "alight", None, tests=invalid),
        _edge("typed", "S", "A", "ride", "alight", "alight", 7.0),
        _edge("e", "A", "E", "alight", "egress", "egress"),
        _edge("d", "E", "D", "egress", "destination", "egress"),
    ]
    compiled = CapabilityCompiler().compile(_contract())
    pred = HeuristicTransitionPredictor().predict(transitions)
    _, cert, diag = _v7_search().search("ep", compiled, transitions, pred, initial_anchor="B", initial_phase="board")
    assert cert is not None and cert.resource_type == "interface"
    assert diag["precondition_proof_envelope_hits"] >= 1

    hidden = TransitionTests(interface_valid=False, reasons=["hidden_interface"])
    transitions2 = [
        _edge("p", "B", "S", "board", "ride", "ride", 4.0),
        _edge("to_hidden", "S", "H", "ride", "alight", "alight", 7.0),
        _edge("hidden_invalid", "H", "X", "alight", "egress", "egress", None, tests=hidden),
        _edge("to_a", "S", "A", "ride", "alight", "alight", 8.0),
        _edge("e", "A", "E", "alight", "egress", "egress"),
        _edge("d", "E", "D", "egress", "destination", "egress"),
    ]
    pred2 = HeuristicTransitionPredictor().predict(transitions2)
    _, cert2, _ = _v7_search().search("ep", compiled, transitions2, pred2, initial_anchor="B", initial_phase="board")
    assert cert2 is not None and cert2.resource_type == "ride_time_s"


def test_v7_frontier_overflow_fails_open():
    transitions = [
        _edge("p0", "B", "S", "board", "ride", "ride", 1.0),
        _edge("bad", "S", "AB", "ride", "alight", "alight", 20.0, cost=0.1),
        _edge("good", "S", "AG", "ride", "alight", "alight", 1.0, cost=1.0),
        _edge("b2", "AB", "EB", "alight", "egress", "egress"),
        _edge("g2", "AG", "EG", "alight", "egress", "egress"),
        _edge("b3", "EB", "D", "egress", "destination", "egress"),
        _edge("g3", "EG", "D", "egress", "destination", "egress"),
    ]
    compiled = CapabilityCompiler().compile(_contract())
    pred = HeuristicTransitionPredictor().predict(transitions)
    sk, _, diag = _v7_search(max_depth=2).search(
        "ep", compiled, transitions, pred, initial_anchor="B", initial_phase="board"
    )
    assert sk is not None and sk.accepted and "good" in sk.transitions
    assert diag["direct_precondition_incomplete_states"] >= 1


def test_v7_planner_wiring_keeps_v6_and_v5_controls():
    from capplan.planning.planner import CapPlanPlanner, PlannerConfig
    p = CapPlanPlanner(PlannerConfig(algorithm_version="V7", evidence_grounded_runtime=True))
    assert p.searcher.config.use_viability_kernel
    assert p.searcher.config.use_precondition_antichain
    assert p.searcher.config.use_direct_dual_precondition_kernel
    assert p.searcher.config.use_rejection_antichain

    v6 = CapPlanPlanner(PlannerConfig(
        algorithm_version="V7", evidence_grounded_runtime=True, v6_reference_runtime=True
    ))
    assert v6.searcher.config.use_precondition_antichain
    assert not v6.searcher.config.use_direct_dual_precondition_kernel

    v5 = CapPlanPlanner(PlannerConfig(
        algorithm_version="V7", evidence_grounded_runtime=True, v5_reference_runtime=True
    ))
    assert v5.searcher.config.use_viability_kernel
    assert not v5.searcher.config.use_precondition_antichain
