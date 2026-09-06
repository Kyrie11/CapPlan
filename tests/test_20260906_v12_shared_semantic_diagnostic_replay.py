from __future__ import annotations

from capplan.models.predictors import HeuristicTransitionPredictor
from capplan.planning.planner import CapPlanPlanner, PlannerConfig
from capplan.planning.typed_safe_budget_search import (
    ExactTransitionSemanticCache,
    SearchConfig,
    TypedSafeBudgetSearch,
)
from capplan.semantics.capability_compiler import CapabilityCompiler
from capplan.semantics.service_automaton import ServiceAutomaton
from tests.test_20260905_v10_semnaive_capability_kernel import _branching_graph, _ride_contract


def _cert_sig(cert):
    if cert is None:
        return None
    return (
        cert.phase,
        cert.transition_id,
        cert.resource_type,
        round(float(cert.signed_margin), 12),
        cert.evidence_source,
        round(float(cert.confidence), 12),
        cert.reason,
    )


def _sk_sig(sk):
    if sk is None:
        return None
    return (bool(sk.accepted), tuple(sk.transitions), round(float(sk.cost), 12))


def test_v12_exact_transition_semantic_cache_replays_identical_search():
    transitions = _branching_graph()
    compiled = CapabilityCompiler().compile(_ride_contract(limit=2.0))
    predictions = HeuristicTransitionPredictor().predict(transitions)
    search = TypedSafeBudgetSearch(
        ServiceAutomaton(),
        config=SearchConfig(
            no_completion_value_guidance=True,
            lambda_edge_validity=0.0,
            lambda_learned_feasibility=0.0,
            lambda_frontier_ranker=0.0,
            use_viability_kernel=False,
        ),
    )
    memo = ExactTransitionSemanticCache()
    sk0, cert0, d0 = search.search(
        "ep", compiled, transitions, predictions,
        initial_anchor="B", initial_phase="board",
        transition_semantic_cache=memo,
        transition_semantic_cache_mode="populate",
    )
    assert memo.primary_stores > 0
    stores_after_primary = memo.stores
    sk1, cert1, d1 = search.search(
        "ep", compiled, transitions, predictions,
        initial_anchor="B", initial_phase="board",
        transition_semantic_cache=memo,
        transition_semantic_cache_mode="reuse",
    )
    assert _sk_sig(sk1) == _sk_sig(sk0)
    assert _cert_sig(cert1) == _cert_sig(cert0)
    assert d1["expansions"] == d0["expansions"]
    assert memo.hits > 0
    assert memo.misses == 0
    assert memo.stores == stores_after_primary


def test_v12_planner_wiring_freezes_v11_acceptance_and_only_changes_diagnosis_reuse():
    full = CapPlanPlanner(PlannerConfig(algorithm_version="V12", evidence_grounded_runtime=True))
    assert full.searcher.config.use_semnaive_projected_acceptance_kernel
    assert not full.searcher.config.use_native_projected_acceptance_kernel
    assert full.searcher.config.lambda_learned_feasibility == 0.0
    assert full.diagnostic_searcher is not None
    assert full._shared_diagnostic_semantic_cache_enabled

    v11 = CapPlanPlanner(PlannerConfig(
        algorithm_version="V12", evidence_grounded_runtime=True, v11_reference_runtime=True,
    ))
    assert v11.searcher.config.use_semnaive_projected_acceptance_kernel
    assert v11.searcher.config.lambda_learned_feasibility == 0.0
    assert v11.diagnostic_searcher is not None
    assert not v11._shared_diagnostic_semantic_cache_enabled

    no_cache = CapPlanPlanner(PlannerConfig(
        algorithm_version="V12", evidence_grounded_runtime=True,
        no_shared_diagnostic_semantic_cache=True,
    ))
    assert not no_cache._shared_diagnostic_semantic_cache_enabled

    legacy = CapPlanPlanner(PlannerConfig(
        algorithm_version="V12", evidence_grounded_runtime=True,
        v12_legacy_static_guidance=True,
    ))
    assert legacy.searcher.config.lambda_learned_feasibility > 0.0
    assert legacy._shared_diagnostic_semantic_cache_enabled


def test_v12_v10_reference_preserves_legacy_guidance_and_disables_new_cache():
    ref = CapPlanPlanner(PlannerConfig(
        algorithm_version="V12", evidence_grounded_runtime=True, v10_reference_runtime=True,
    ))
    assert ref.searcher.config.use_semnaive_projected_acceptance_kernel
    assert ref.searcher.config.lambda_learned_feasibility > 0.0
    assert ref.diagnostic_searcher is not None
    assert not ref._shared_diagnostic_semantic_cache_enabled
