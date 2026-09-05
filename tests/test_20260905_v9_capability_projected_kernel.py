from __future__ import annotations

from capplan.data.schemas import CandidateTransition, CapabilityClause, CapabilityContract, ResourceEvidence, TransitionTests
from capplan.models.predictors import HeuristicTransitionPredictor
from capplan.planning.capability_projected_precondition_kernel import (
    build_capability_projected_acceptance_kernel,
    hard_contract_resource_support,
)
from capplan.planning.capability_viability_kernel import build_capability_viability_kernel
from capplan.planning.incremental_capability_precondition_kernel import build_incremental_acceptance_kernel
from capplan.planning.planner import CapPlanPlanner, PlannerConfig
from capplan.planning.typed_safe_budget_search import SearchConfig, TypedSafeBudgetSearch
from capplan.semantics.capability_compiler import CapabilityCompiler
from capplan.semantics.resource_registry import DEFAULT_REGISTRY
from capplan.semantics.service_automaton import ServiceAutomaton


def _edge(tid, a, b, p, q, action, evidence=(), *, cost=1.0):
    return CandidateTransition(
        transition_id=tid, episode_id="ep", from_anchor=a, to_anchor=b,
        from_phase=p, to_phase=q, action=action, resource_evidence=list(evidence),
        availability=1.0, map_confidence=1.0, interface={}, dynamic={}, cost=cost,
        tests=TransitionTests(),
    )


def _ev(name, kind, value):
    return ResourceEvidence(name, kind, value, sigma=0.0, source="test_evidence")


def _ride_contract(limit=10.0):
    return CapabilityContract("p", [CapabilityClause(
        "ride_time_s", ["ride"], "<=", limit, "cumulative",
        beta_tau=0.0, clause_id="ride", source="passenger_contract",
    )])


def _tradeoff_graph():
    # Branches have the same hard-contract ride effect but trade off two
    # passenger-irrelevant evidence dimensions. V8 cannot dominate either in
    # the full registry space; V9 is allowed to quotient them away.
    return [
        _edge("a0", "B", "RA", "board", "ride", "ride", [
            _ev("ride_time_s", "cumulative", 2.0),
            _ev("path_width_m", "lower", 2.0),
            _ev("slope", "upper", 0.20),
        ], cost=1.0),
        _edge("b0", "B", "RB", "board", "ride", "ride", [
            _ev("ride_time_s", "cumulative", 2.0),
            _ev("path_width_m", "lower", 1.0),
            _ev("slope", "upper", 0.10),
        ], cost=1.1),
        _edge("a1", "RA", "AA", "ride", "alight", "alight", cost=1.0),
        _edge("b1", "RB", "AB", "ride", "alight", "alight", cost=1.0),
        _edge("a2", "AA", "EA", "alight", "egress", "egress", cost=1.0),
        _edge("b2", "AB", "EB", "alight", "egress", "egress", cost=1.0),
        _edge("a3", "EA", "D", "egress", "destination", "egress", cost=1.0),
        _edge("b3", "EB", "D", "egress", "destination", "egress", cost=1.0),
    ]


def test_v9_support_is_defined_by_hard_capability_program():
    compiled = CapabilityCompiler().compile(_ride_contract())
    assert hard_contract_resource_support(compiled) == frozenset({"ride_time_s"})


def test_v9_projection_removes_irrelevant_tradeoff_and_compresses_frontier():
    transitions = _tradeoff_graph()
    compiled = CapabilityCompiler().compile(_ride_contract())
    pred = HeuristicTransitionPredictor().predict(transitions)
    kernel = build_capability_viability_kernel(
        transitions, pred, ServiceAutomaton(), enumerate_suffixes=False
    )
    v8 = build_incremental_acceptance_kernel(kernel, compiled, pred)
    v9 = build_capability_projected_acceptance_kernel(kernel, compiled, pred)
    start = ("B", "board")
    assert len(v8.state_summaries(start)) >= 2
    assert len(v9.state_summaries(start)) == 1
    row = v9.state_summaries(start)[0]
    assert set(row.effects) == {"ride_time_s"}
    assert v9.projected_evidence_dropped > 0
    assert v9.projected_resource_count == 1


def test_v9_projection_preserves_v8_hard_decision_and_expansions_on_typed_branching():
    transitions = [
        _edge("bad0", "B", "RB", "board", "ride", "ride", [_ev("ride_time_s", "cumulative", 6.0)], cost=0.1),
        _edge("good0", "B", "RG", "board", "ride", "ride", [_ev("ride_time_s", "cumulative", 3.0)], cost=1.0),
        _edge("bad1", "RB", "AB", "ride", "alight", "alight", [_ev("ride_time_s", "cumulative", 6.0)], cost=0.1),
        _edge("good1", "RG", "AG", "ride", "alight", "alight", [_ev("ride_time_s", "cumulative", 3.0)], cost=1.0),
        _edge("bad2", "AB", "EB", "alight", "egress", "egress"),
        _edge("good2", "AG", "EG", "alight", "egress", "egress"),
        _edge("bad3", "EB", "D", "egress", "destination", "egress"),
        _edge("good3", "EG", "D", "egress", "destination", "egress"),
    ]
    compiled = CapabilityCompiler().compile(_ride_contract())
    pred = HeuristicTransitionPredictor().predict(transitions)
    common = dict(
        no_completion_value_guidance=True,
        lambda_edge_validity=0.0,
        lambda_learned_feasibility=0.0,
        lambda_frontier_ranker=0.0,
        use_viability_kernel=True,
        viability_pruning=True,
        viability_typed_pruning=True,
        use_precondition_antichain=True,
        use_rejection_antichain=False,
        viability_use_proof_envelope=False,
    )
    v8_search = TypedSafeBudgetSearch(ServiceAutomaton(), config=SearchConfig(
        **common, use_incremental_acceptance_kernel=True,
    ))
    v9_search = TypedSafeBudgetSearch(ServiceAutomaton(), config=SearchConfig(
        **common, use_capability_projected_acceptance_kernel=True,
    ))
    sk8, cert8, d8 = v8_search.search("ep", compiled, transitions, pred, initial_anchor="B", initial_phase="board")
    sk9, cert9, d9 = v9_search.search("ep", compiled, transitions, pred, initial_anchor="B", initial_phase="board")
    assert cert8 is None and cert9 is None
    assert sk8 is not None and sk9 is not None and sk8.accepted and sk9.accepted
    assert d8["expansions"] == d9["expansions"]
    assert d9["projected_resource_count"] == 1


def test_v9_planner_wiring_keeps_lazy_exact_diagnosis_and_supports_v8_reference():
    planner = CapPlanPlanner(PlannerConfig(
        algorithm_version="V9", evidence_grounded_runtime=True
    ))
    assert planner.searcher.config.use_viability_kernel
    assert planner.searcher.config.use_precondition_antichain
    assert planner.searcher.config.use_capability_projected_acceptance_kernel
    assert not planner.searcher.config.use_incremental_acceptance_kernel
    assert planner.searcher.config.capability_projection
    assert planner.searcher.config.frontier_signature_index
    assert planner.diagnostic_searcher is not None

    ref = CapPlanPlanner(PlannerConfig(
        algorithm_version="V9", evidence_grounded_runtime=True,
        v8_reference_runtime=True,
    ))
    assert ref.searcher.config.use_incremental_acceptance_kernel
    assert not ref.searcher.config.use_capability_projected_acceptance_kernel

    no_projection = CapPlanPlanner(PlannerConfig(
        algorithm_version="V9", evidence_grounded_runtime=True,
        no_capability_projection=True,
    ))
    assert no_projection.searcher.config.use_capability_projected_acceptance_kernel
    assert not no_projection.searcher.config.capability_projection
