from __future__ import annotations

from capplan.data.schemas import (
    CandidateTransition,
    CapabilityClause,
    CapabilityContract,
    ResourceEvidence,
    TransitionTests,
)
from capplan.models.predictors import HeuristicTransitionPredictor
from capplan.planning.capability_viability_kernel import build_capability_viability_kernel
from capplan.planning.typed_safe_budget_search import SearchConfig, TypedSafeBudgetSearch
from capplan.semantics.capability_compiler import CapabilityCompiler
from capplan.semantics.service_automaton import ServiceAutomaton


def _edge(tid, a, b, p, q, action, ride=None, *, cost=1.0, tests=None):
    return CandidateTransition(
        transition_id=tid,
        episode_id="ep",
        from_anchor=a,
        to_anchor=b,
        from_phase=p,
        to_phase=q,
        action=action,
        resource_evidence=(
            [ResourceEvidence("ride_time_s", "cumulative", ride, sigma=0.0, source="test_evidence")]
            if ride is not None else []
        ),
        availability=1.0,
        map_confidence=1.0,
        interface={},
        dynamic={},
        cost=cost,
        tests=tests or TransitionTests(),
    )


def _contract(limit=10.0):
    return CapabilityContract(
        "p",
        [CapabilityClause(
            "ride_time_s", ["ride"], "<=", limit, "cumulative",
            beta_tau=0.0, clause_id="ride", source="passenger_contract",
        )],
    )


def _two_branch_graph(*, bad=(6.0, 6.0), good=(3.0, 3.0)):
    return [
        _edge("bad0", "B", "RB", "board", "ride", "ride", bad[0], cost=0.1),
        _edge("good0", "B", "RG", "board", "ride", "ride", good[0], cost=1.0),
        _edge("bad1", "RB", "AB", "ride", "alight", "alight", bad[1], cost=0.1),
        _edge("good1", "RG", "AG", "ride", "alight", "alight", good[1], cost=1.0),
        _edge("bad2", "AB", "EB", "alight", "egress", "egress", None, cost=0.1),
        _edge("good2", "AG", "EG", "alight", "egress", "egress", None, cost=1.0),
        _edge("bad3", "EB", "D", "egress", "destination", "egress", None, cost=0.1),
        _edge("good3", "EG", "D", "egress", "destination", "egress", None, cost=1.0),
    ]


def _v5_search(*, generic=False, max_paths=256):
    return TypedSafeBudgetSearch(
        ServiceAutomaton(),
        config=SearchConfig(
            no_completion_value_guidance=True,
            lambda_edge_validity=0.0,
            lambda_learned_feasibility=0.0,
            lambda_frontier_ranker=0.0,
            use_viability_kernel=True,
            viability_pruning=True,
            viability_typed_pruning=True,
            viability_generic_certificates=generic,
            viability_max_paths_per_state=max_paths,
            viability_max_depth=16,
        ),
    )


def test_v5_typed_viability_prunes_future_budget_dead_branch_and_preserves_success():
    transitions = _two_branch_graph()
    compiled = CapabilityCompiler().compile(_contract(10.0))
    pred = HeuristicTransitionPredictor().predict(transitions)

    base = TypedSafeBudgetSearch(
        ServiceAutomaton(),
        config=SearchConfig(
            no_completion_value_guidance=True,
            lambda_edge_validity=0.0,
            lambda_learned_feasibility=0.0,
            lambda_frontier_ranker=0.0,
        ),
    )
    v5 = _v5_search()

    sk0, _, d0 = base.search("ep", compiled, transitions, pred, initial_anchor="B", initial_phase="board")
    sk1, _, d1 = v5.search("ep", compiled, transitions, pred, initial_anchor="B", initial_phase="board")

    assert sk0 is not None and sk0.accepted
    assert sk1 is not None and sk1.accepted
    assert sk1.transitions[-1] == "good3"
    assert d1["viability_typed_pruned"] >= 1
    assert d1["viability_structural_pruned"] == 0
    assert d1["viability_path_checks"] >= 1
    assert d1["expansions"] < d0["expansions"]


def test_v5_structural_dead_end_propagates_concrete_failure_witness():
    invalid = TransitionTests(interface_valid=False, reasons=["door_interface_invalid"])
    transitions = [
        _edge("r0", "B", "R", "board", "ride", "ride", 1.0),
        _edge("r1", "R", "A", "ride", "alight", "alight", 1.0),
        _edge("r2", "A", "E", "alight", "egress", "egress", None),
        _edge("blocked", "E", "D", "egress", "destination", "egress", None, tests=invalid),
    ]
    compiled = CapabilityCompiler().compile(_contract(10.0))
    pred = HeuristicTransitionPredictor().predict(transitions)
    kernel = build_capability_viability_kernel(transitions, pred, ServiceAutomaton())

    assert not kernel.is_reachable(("R", "ride"))
    witness = kernel.failure_witness(("R", "ride"))
    assert witness is not None
    assert witness.transition_id == "blocked"
    assert witness.resource_type == "interface"
    assert witness.evidence_source == "transition_tests"

    sk, cert, diag = _v5_search().search(
        "ep", compiled, transitions, pred, initial_anchor="B", initial_phase="board"
    )
    assert sk is None
    assert cert is not None
    assert cert.resource_type == "interface"
    assert cert.evidence_source == "transition_tests"
    assert diag["viability_structural_pruned"] >= 1


def test_v5_proof_carrying_typed_certificate_beats_generic_pseudo_certificate():
    # Both one-step successors are locally feasible but every suffix pushes the
    # cumulative ride-time budget over the passenger threshold.
    transitions = _two_branch_graph(bad=(4.0, 7.0), good=(4.0, 7.0))
    compiled = CapabilityCompiler().compile(_contract(10.0))
    pred = HeuristicTransitionPredictor().predict(transitions)

    sk, cert, diag = _v5_search(generic=False).search(
        "ep", compiled, transitions, pred, initial_anchor="B", initial_phase="board"
    )
    gsk, gcert, gdiag = _v5_search(generic=True).search(
        "ep", compiled, transitions, pred, initial_anchor="B", initial_phase="board"
    )

    assert sk is None and gsk is None
    assert cert is not None and gcert is not None
    assert cert.resource_type == "ride_time_s"
    assert cert.evidence_source == "passenger_contract"
    assert gcert.resource_type == "typed_viability"
    assert gcert.evidence_source == "capability_viability_kernel"
    # Certificate presentation must not alter the search mechanism.
    assert diag["expansions"] == gdiag["expansions"]
    assert diag["viability_typed_pruned"] == gdiag["viability_typed_pruned"]


def test_v5_overflow_disables_typed_pruning_fail_open():
    # P -> S then two distinct suffixes. cap=1 makes S incomplete/overflow. A
    # false proof from the first stored path must never prune the state.
    transitions = [
        _edge("p0", "B", "S", "board", "ride", "ride", 1.0),
        _edge("s_bad", "S", "AB", "ride", "alight", "alight", 20.0, cost=0.1),
        _edge("s_good", "S", "AG", "ride", "alight", "alight", 1.0, cost=1.0),
        _edge("b2", "AB", "EB", "alight", "egress", "egress", None),
        _edge("g2", "AG", "EG", "alight", "egress", "egress", None),
        _edge("b3", "EB", "D", "egress", "destination", "egress", None),
        _edge("g3", "EG", "D", "egress", "destination", "egress", None),
    ]
    compiled = CapabilityCompiler().compile(_contract(10.0))
    pred = HeuristicTransitionPredictor().predict(transitions)
    kernel = build_capability_viability_kernel(
        transitions, pred, ServiceAutomaton(), max_paths_per_state=1, max_depth=16
    )
    assert kernel.is_reachable(("S", "ride"))
    assert kernel.overflowed(("S", "ride"))

    sk, _, diag = _v5_search(max_paths=1).search(
        "ep", compiled, transitions, pred, initial_anchor="B", initial_phase="board"
    )
    assert sk is not None and sk.accepted
    assert "s_good" in sk.transitions
    # The overflowed S state cannot be typed-pruned on entry.
    assert diag["viability_typed_pruned"] == 0


def test_v5_planner_wiring_enables_cvk_and_preserves_v2_static_ordering():
    from capplan.planning.planner import CapPlanPlanner, PlannerConfig

    p = CapPlanPlanner(PlannerConfig(algorithm_version="V5", evidence_grounded_runtime=True))
    assert p.searcher.config.use_viability_kernel
    assert p.searcher.config.viability_typed_pruning
    assert not p.searcher.config.use_continuation_envelope
    assert p.searcher.config.lambda_learned_feasibility == 0.20
    assert p.searcher.config.no_completion_value_guidance

    ref = CapPlanPlanner(PlannerConfig(
        algorithm_version="V5", evidence_grounded_runtime=True, v2_reference_runtime=True
    ))
    assert not ref.searcher.config.use_viability_kernel
    assert ref.searcher.config.lambda_learned_feasibility == 0.20
