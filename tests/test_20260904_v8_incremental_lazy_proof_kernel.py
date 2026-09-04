from __future__ import annotations

from capplan.data.schemas import CandidateTransition, CapabilityClause, CapabilityContract, ResourceEvidence, TransitionTests
from capplan.models.predictors import HeuristicTransitionPredictor
from capplan.planning.capability_precondition_antichain import _build_suffix_summary
from capplan.planning.capability_viability_kernel import SuffixWitness, build_capability_viability_kernel
from capplan.planning.incremental_capability_precondition_kernel import (
    build_incremental_acceptance_kernel,
    compose_suffix_summaries,
)
from capplan.planning.planner import CapPlanPlanner, PlannerConfig
from capplan.planning.typed_safe_budget_search import SearchConfig, TypedSafeBudgetSearch
from capplan.semantics.capability_compiler import CapabilityCompiler
from capplan.semantics.service_automaton import ServiceAutomaton
from capplan.semantics.resource_registry import DEFAULT_REGISTRY


def _edge(tid, a, b, p, q, action, ride=None, *, cost=1.0):
    return CandidateTransition(
        transition_id=tid, episode_id="ep", from_anchor=a, to_anchor=b,
        from_phase=p, to_phase=q, action=action,
        resource_evidence=([] if ride is None else [ResourceEvidence(
            "ride_time_s", "cumulative", ride, sigma=0.0, source="test_evidence"
        )]),
        availability=1.0, map_confidence=1.0, interface={}, dynamic={}, cost=cost,
        tests=TransitionTests(),
    )


def _contract(limit=10.0):
    return CapabilityContract("p", [CapabilityClause(
        "ride_time_s", ["ride"], "<=", limit, "cumulative",
        beta_tau=0.0, clause_id="ride", source="passenger_contract",
    )])


def _branching_graph():
    return [
        _edge("bad0", "B", "RB", "board", "ride", "ride", 6.0, cost=0.1),
        _edge("good0", "B", "RG", "board", "ride", "ride", 3.0, cost=1.0),
        _edge("bad1", "RB", "AB", "ride", "alight", "alight", 6.0, cost=0.1),
        _edge("good1", "RG", "AG", "ride", "alight", "alight", 3.0, cost=1.0),
        _edge("bad2", "AB", "EB", "alight", "egress", "egress"),
        _edge("good2", "AG", "EG", "alight", "egress", "egress"),
        _edge("bad3", "EB", "D", "egress", "destination", "egress"),
        _edge("good3", "EG", "D", "egress", "destination", "egress"),
    ]


def test_v8_incremental_composition_matches_full_path_summary_for_typed_effect():
    transitions = _branching_graph()
    compiled = CapabilityCompiler().compile(_contract())
    pred = HeuristicTransitionPredictor().predict(transitions)
    kernel = build_capability_viability_kernel(
        transitions, pred, ServiceAutomaton(), enumerate_suffixes=False
    )
    one0, intrinsic0 = _build_suffix_summary(
        SuffixWitness(("good0",), 1.0), kernel, compiled, pred, DEFAULT_REGISTRY,
        no_conservative_margins=False, default_beta=1.0,
    )
    one1, intrinsic1 = _build_suffix_summary(
        SuffixWitness(("good1",), 1.0), kernel, compiled, pred, DEFAULT_REGISTRY,
        no_conservative_margins=False, default_beta=1.0,
    )
    full, intrinsic_full = _build_suffix_summary(
        SuffixWitness(("good0", "good1"), 2.0), kernel, compiled, pred, DEFAULT_REGISTRY,
        no_conservative_margins=False, default_beta=1.0,
    )
    assert intrinsic0 is None and intrinsic1 is None and intrinsic_full is None
    assert one0 is not None and one1 is not None and full is not None
    inc = compose_suffix_summaries(one0, one1)
    assert inc.effects["ride_time_s"] == full.effects["ride_time_s"] == 6.0
    assert inc.active_clause_ids == full.active_clause_ids
    assert inc.active_group_ids == full.active_group_ids


def test_v8_incremental_acceptance_preserves_hard_decision_without_raw_or_rejection_universe():
    transitions = _branching_graph()
    compiled = CapabilityCompiler().compile(_contract())
    pred = HeuristicTransitionPredictor().predict(transitions)
    search = TypedSafeBudgetSearch(ServiceAutomaton(), config=SearchConfig(
        no_completion_value_guidance=True,
        lambda_edge_validity=0.0,
        lambda_learned_feasibility=0.0,
        lambda_frontier_ranker=0.0,
        use_viability_kernel=True,
        viability_pruning=True,
        viability_typed_pruning=True,
        use_precondition_antichain=True,
        use_incremental_acceptance_kernel=True,
        use_rejection_antichain=False,
        viability_use_proof_envelope=False,
    ))
    sk, cert, diag = search.search(
        "ep", compiled, transitions, pred, initial_anchor="B", initial_phase="board"
    )
    assert cert is None and sk is not None and sk.accepted
    assert "good0" in sk.transitions
    assert diag["precondition_raw_suffixes"] == 0
    assert diag["precondition_raw_proofs"] == 0
    assert diag["precondition_rejection_antichain_size"] == 0
    assert diag["precondition_proof_antichain_size"] == 0
    assert diag["direct_precondition_build_candidates"] > 0


def test_v8_incremental_kernel_fails_open_when_depth_cap_is_incomplete():
    transitions = _branching_graph()
    compiled = CapabilityCompiler().compile(_contract())
    pred = HeuristicTransitionPredictor().predict(transitions)
    kernel = build_capability_viability_kernel(
        transitions, pred, ServiceAutomaton(), enumerate_suffixes=False, max_depth=2
    )
    antichain = build_incremental_acceptance_kernel(
        kernel, compiled, pred, max_depth=2
    )
    # The start state cannot be safely declared impossible merely because the
    # backward compiler hit the configured depth bound.
    assert not antichain.state_complete(("B", "board"))
    assert antichain.direct_incomplete_states > 0


def test_v8_planner_wiring_uses_incremental_acceptance_and_lazy_exact_diagnosis():
    planner = CapPlanPlanner(PlannerConfig(
        algorithm_version="V8", evidence_grounded_runtime=True
    ))
    assert planner.searcher.config.use_viability_kernel
    assert planner.searcher.config.use_precondition_antichain
    assert planner.searcher.config.use_incremental_acceptance_kernel
    assert not planner.searcher.config.use_direct_dual_precondition_kernel
    assert not planner.searcher.config.use_rejection_antichain
    assert not planner.searcher.config.viability_use_proof_envelope
    assert planner.diagnostic_searcher is not None
    assert not planner.diagnostic_searcher.config.use_viability_kernel

    no_replay = CapPlanPlanner(PlannerConfig(
        algorithm_version="V8", evidence_grounded_runtime=True,
        no_lazy_diagnostic_replay=True,
    ))
    assert no_replay.diagnostic_searcher is None
