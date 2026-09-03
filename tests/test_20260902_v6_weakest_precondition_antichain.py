from __future__ import annotations

from capplan.data.schemas import (
    CandidateTransition, CapabilityClause, CapabilityContract, ResourceEvidence, TransitionTests,
)
from capplan.models.predictors import HeuristicTransitionPredictor
from capplan.planning.capability_precondition_antichain import (
    build_capability_precondition_antichain, evaluate_precondition_antichain,
)
from capplan.planning.capability_viability_kernel import build_capability_viability_kernel
from capplan.planning.typed_safe_budget_search import SearchConfig, SearchLabel, TypedSafeBudgetSearch
from capplan.semantics.capability_compiler import CapabilityCompiler
from capplan.semantics.service_automaton import ServiceAutomaton
from capplan.semantics.typed_resource_algebra import init_ledger, update_value
from capplan.semantics.resource_registry import DEFAULT_REGISTRY


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


def _search(*, antichain: bool, proof: bool = True):
    return TypedSafeBudgetSearch(ServiceAutomaton(), config=SearchConfig(
        no_completion_value_guidance=True,
        lambda_edge_validity=0.0,
        lambda_learned_feasibility=0.0,
        lambda_frontier_ranker=0.0,
        use_viability_kernel=True,
        viability_pruning=True,
        viability_typed_pruning=True,
        use_precondition_antichain=antichain,
        viability_use_proof_envelope=proof,
        viability_max_paths_per_state=256,
        viability_max_depth=16,
    ))


def test_v6_antichain_preserves_v5_pruning_and_reduces_query_work():
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
    v5 = _search(antichain=False, proof=False)
    v6 = _search(antichain=True, proof=True)
    sk5, _, d5 = v5.search("ep", compiled, transitions, pred, initial_anchor="B", initial_phase="board")
    sk6, _, d6 = v6.search("ep", compiled, transitions, pred, initial_anchor="B", initial_phase="board")
    assert sk5 is not None and sk6 is not None
    assert sk5.transitions == sk6.transitions
    assert d5["expansions"] == d6["expansions"]
    assert d5["viability_typed_pruned"] == d6["viability_typed_pruned"]
    assert d5["viability_path_checks"] > 0
    assert d6["viability_path_checks"] == 0
    assert d6["precondition_summary_checks"] > 0


def test_v6_antichain_compresses_dominated_suffix_effects_exactly():
    transitions = [
        _edge("a", "S", "A1", "ride", "alight", "alight", 2.0, cost=1.0),
        _edge("b", "S", "A2", "ride", "alight", "alight", 5.0, cost=1.0),
        _edge("a2", "A1", "E1", "alight", "egress", "egress"),
        _edge("b2", "A2", "E2", "alight", "egress", "egress"),
        _edge("a3", "E1", "D", "egress", "destination", "egress"),
        _edge("b3", "E2", "D", "egress", "destination", "egress"),
    ]
    compiled = CapabilityCompiler().compile(_contract())
    pred = HeuristicTransitionPredictor().predict(transitions)
    kernel = build_capability_viability_kernel(transitions, pred, ServiceAutomaton())
    ac = build_capability_precondition_antichain(kernel, compiled, pred)
    state = ("S", "ride")
    assert ac.raw_suffix_count[state] == 2
    assert len(ac.state_summaries(state)) == 1

    ledger = init_ledger({"ride_time_s"})
    ledger["ride_time_s"] = update_value(
        ledger["ride_time_s"], 7.0, DEFAULT_REGISTRY.get("ride_time_s")
    )
    assert evaluate_precondition_antichain(state, ledger, compiled, ac).viable
    ledger["ride_time_s"] = 9.0
    dec = evaluate_precondition_antichain(state, ledger, compiled, ac)
    assert not dec.viable
    assert dec.witness is not None and dec.witness.resource_type == "ride_time_s"


def test_v6_proof_envelope_recovers_rejected_downstream_interface_branch():
    # B->S is locally feasible.  At S, one outgoing branch is interface-invalid;
    # the only structurally valid destination suffix exceeds ride budget. V5
    # typed pruning never expands S and therefore reports ride_time. V6 carries
    # the rejected interface branch through the same backward viability object.
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

    _, cert_no_proof, d0 = _search(antichain=True, proof=False).search(
        "ep", compiled, transitions, pred, initial_anchor="B", initial_phase="board"
    )
    _, cert_proof, d1 = _search(antichain=True, proof=True).search(
        "ep", compiled, transitions, pred, initial_anchor="B", initial_phase="board"
    )
    assert d0["expansions"] == d1["expansions"]
    assert cert_no_proof is not None and cert_no_proof.resource_type == "ride_time_s"
    assert cert_proof is not None
    assert cert_proof.resource_type == "interface"
    assert cert_proof.evidence_source == "transition_tests"
    assert d1["precondition_proof_envelope_hits"] >= 1


def test_v6_overflow_remains_fail_open_for_antichain():
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
    search = TypedSafeBudgetSearch(ServiceAutomaton(), config=SearchConfig(
        no_completion_value_guidance=True,
        lambda_edge_validity=0.0, lambda_learned_feasibility=0.0, lambda_frontier_ranker=0.0,
        use_viability_kernel=True, viability_pruning=True, viability_typed_pruning=True,
        use_precondition_antichain=True, viability_use_proof_envelope=True,
        viability_max_paths_per_state=1, viability_max_depth=16,
    ))
    sk, _, diag = search.search("ep", compiled, transitions, pred, initial_anchor="B", initial_phase="board")
    assert sk is not None and sk.accepted and "good" in sk.transitions
    assert diag["viability_typed_pruned"] == 0


def test_v6_planner_wiring_uses_antichain_and_keeps_v5_reference_control():
    from capplan.planning.planner import CapPlanPlanner, PlannerConfig
    p = CapPlanPlanner(PlannerConfig(algorithm_version="V6", evidence_grounded_runtime=True))
    assert p.searcher.config.use_viability_kernel
    assert p.searcher.config.use_precondition_antichain
    assert p.searcher.config.viability_use_proof_envelope
    assert p.searcher.config.lambda_learned_feasibility == 0.20

    v5 = CapPlanPlanner(PlannerConfig(
        algorithm_version="V6", evidence_grounded_runtime=True, v5_reference_runtime=True
    ))
    assert v5.searcher.config.use_viability_kernel
    assert not v5.searcher.config.use_precondition_antichain
    assert not v5.searcher.config.viability_use_proof_envelope


def test_v6_proof_envelope_must_not_cross_typed_infeasible_prefix():
    # The invalid interface is behind a ride-budget violating edge.  Oracle/TSBS
    # cannot reach that downstream state under this passenger ledger, so a
    # backward proof mechanism must not surface the interface witness merely
    # because the hard-valid topology can reach it.
    invalid = TransitionTests(interface_valid=False, reasons=["hidden_interface"])
    transitions = [
        _edge("p", "B", "S", "board", "ride", "ride", 4.0),
        _edge("to_hidden", "S", "H", "ride", "alight", "alight", 7.0),
        _edge("hidden_invalid", "H", "X", "alight", "egress", "egress", None, tests=invalid),
        # A structurally valid destination suffix, also typed-infeasible.
        _edge("to_a", "S", "A", "ride", "alight", "alight", 8.0),
        _edge("e", "A", "E", "alight", "egress", "egress"),
        _edge("d", "E", "D", "egress", "destination", "egress"),
    ]
    compiled = CapabilityCompiler().compile(_contract())
    pred = HeuristicTransitionPredictor().predict(transitions)
    _, cert, _ = _search(antichain=True, proof=True).search(
        "ep", compiled, transitions, pred, initial_anchor="B", initial_phase="board"
    )
    assert cert is not None
    assert cert.resource_type == "ride_time_s"
