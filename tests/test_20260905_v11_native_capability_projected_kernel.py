from __future__ import annotations

from capplan.models.predictors import HeuristicTransitionPredictor
from capplan.planning.capability_projected_precondition_kernel import build_capability_projected_acceptance_kernel
from capplan.planning.capability_viability_kernel import build_capability_viability_kernel
from capplan.planning.native_capability_projected_kernel import build_native_capability_projected_acceptance_kernel
from capplan.planning.planner import CapPlanPlanner, PlannerConfig
from capplan.planning.semnaive_capability_projected_kernel import build_semnaive_capability_projected_acceptance_kernel
from capplan.planning.typed_safe_budget_search import SearchConfig, TypedSafeBudgetSearch
from capplan.semantics.capability_compiler import CapabilityCompiler
from capplan.semantics.service_automaton import ServiceAutomaton
from tests.test_20260905_v10_semnaive_capability_kernel import _branching_graph, _ride_contract, _sig_rows


def _fixture(limit=10.0):
    transitions = _branching_graph()
    compiled = CapabilityCompiler().compile(_ride_contract(limit))
    pred = HeuristicTransitionPredictor().predict(transitions)
    kernel = build_capability_viability_kernel(
        transitions, pred, ServiceAutomaton(), enumerate_suffixes=False,
    )
    return transitions, compiled, pred, kernel


def test_v11_native_quotient_matches_v10_and_v9_fixed_point():
    _, compiled, pred, kernel = _fixture()
    v9 = build_capability_projected_acceptance_kernel(kernel, compiled, pred)
    v10 = build_semnaive_capability_projected_acceptance_kernel(kernel, compiled, pred)
    v11 = build_native_capability_projected_acceptance_kernel(kernel, compiled, pred)
    assert _sig_rows(v11) == _sig_rows(v10) == _sig_rows(v9)
    assert v11.complete == v10.complete == v9.complete
    assert v11.direct_build_candidates_total == v10.direct_build_candidates_total
    assert v11.native_projected_compositions > 0
    assert v11.native_projected_materializations > 0
    assert v11.native_projected_fallbacks == 0
    assert v11.fused_frontier_passes > 0


def test_v11_fused_insertion_is_semantically_exact():
    _, compiled, pred, kernel = _fixture()
    full = build_native_capability_projected_acceptance_kernel(kernel, compiled, pred)
    two_pass = build_native_capability_projected_acceptance_kernel(
        kernel, compiled, pred, use_fused_frontier_insertion=False,
    )
    assert _sig_rows(full) == _sig_rows(two_pass)
    assert full.complete == two_pass.complete
    assert full.fused_frontier_passes > 0
    assert two_pass.fused_frontier_passes == 0


def test_v11_search_decision_and_expansions_match_v10():
    transitions, compiled, pred, _ = _fixture(limit=6.0)
    common = dict(
        no_completion_value_guidance=True, lambda_edge_validity=0.0,
        lambda_learned_feasibility=0.0, lambda_frontier_ranker=0.0,
        use_viability_kernel=True, viability_pruning=True,
        viability_typed_pruning=True, use_precondition_antichain=True,
        use_rejection_antichain=False, viability_use_proof_envelope=False,
    )
    s10 = TypedSafeBudgetSearch(ServiceAutomaton(), config=SearchConfig(
        **common, use_semnaive_projected_acceptance_kernel=True,
    ))
    s11 = TypedSafeBudgetSearch(ServiceAutomaton(), config=SearchConfig(
        **common, use_native_projected_acceptance_kernel=True,
    ))
    sk10, cert10, d10 = s10.search("ep", compiled, transitions, pred, initial_anchor="B", initial_phase="board")
    sk11, cert11, d11 = s11.search("ep", compiled, transitions, pred, initial_anchor="B", initial_phase="board")
    assert bool(sk11) == bool(sk10)
    assert (cert11 is None) == (cert10 is None)
    assert d11["expansions"] == d10["expansions"]
    assert d11["native_projected_compositions"] > 0
    assert d11["native_projected_fallbacks"] == 0


def test_v11_planner_wiring_and_v10_reference():
    # Confirmatory V11-A keeps the already validated V10 exact kernel and only
    # retires the net-negative transition-static learned guidance.
    full = CapPlanPlanner(PlannerConfig(algorithm_version="V11", evidence_grounded_runtime=True))
    assert full.searcher.config.use_semnaive_projected_acceptance_kernel
    assert not full.searcher.config.use_native_projected_acceptance_kernel
    assert full.searcher.config.lambda_learned_feasibility == 0.0
    assert full.diagnostic_searcher is not None

    # Historical V10 reference restores the legacy static guidance while keeping
    # exactly the same SN-CPK construction.
    v10 = CapPlanPlanner(PlannerConfig(
        algorithm_version="V11", evidence_grounded_runtime=True, v10_reference_runtime=True,
    ))
    assert v10.searcher.config.use_semnaive_projected_acceptance_kernel
    assert not v10.searcher.config.use_native_projected_acceptance_kernel
    assert v10.searcher.config.lambda_learned_feasibility > 0.0

    legacy = CapPlanPlanner(PlannerConfig(
        algorithm_version="V11", evidence_grounded_runtime=True, v11_legacy_static_guidance=True,
    ))
    assert legacy.searcher.config.use_semnaive_projected_acceptance_kernel
    assert legacy.searcher.config.lambda_learned_feasibility > 0.0

    # V11-B is an exploratory exact representation branch; it cannot determine
    # the confirmatory V11 GO/STOP decision.
    native = CapPlanPlanner(PlannerConfig(
        algorithm_version="V11", evidence_grounded_runtime=True, v11_native_quotient_experimental=True,
    ))
    assert native.searcher.config.use_native_projected_acceptance_kernel
    assert not native.searcher.config.use_semnaive_projected_acceptance_kernel
    assert native.searcher.config.lambda_learned_feasibility == 0.0

    no_fused = CapPlanPlanner(PlannerConfig(
        algorithm_version="V11", evidence_grounded_runtime=True,
        v11_native_quotient_experimental=True, no_fused_frontier_insertion=True,
    ))
    assert no_fused.searcher.config.use_native_projected_acceptance_kernel
    assert not no_fused.searcher.config.native_fused_frontier_insertion
