from __future__ import annotations

from capplan.data.schemas import CapabilityClause, CapabilityContract, RequirementGroup
from capplan.models.predictors import HeuristicTransitionPredictor
from capplan.planning.parametric_capability_kernel import capability_program_shape
from capplan.planning.planner import CapPlanPlanner, PlannerConfig
from capplan.planning.typed_safe_budget_search import SearchConfig, TypedSafeBudgetSearch
from capplan.semantics.capability_compiler import CapabilityCompiler
from capplan.semantics.service_automaton import ServiceAutomaton
from tests.test_20260905_v10_semnaive_capability_kernel import _branching_graph, _ride_contract


def test_v16_program_shape_erases_only_safe_numeric_monotone_thresholds():
    c1 = CapabilityCompiler().compile(_ride_contract(limit=6.0))
    c2 = CapabilityCompiler().compile(_ride_contract(limit=4.0))
    s1, n1 = capability_program_shape(c1, erase_numeric_thresholds=True)
    s2, n2 = capability_program_shape(c2, erase_numeric_thresholds=True)
    assert s1 == s2 and n1 == n2 == 1
    f1, _ = capability_program_shape(c1, erase_numeric_thresholds=False)
    f2, _ = capability_program_shape(c2, erase_numeric_thresholds=False)
    assert f1 != f2

    # Categorical/interface predicates and group topology are structural and
    # therefore must never be erased by the parametric-kernel equivalence.
    ca = CapabilityContract('p', [
        CapabilityClause('ramp',['board'],'requires',True,'categorical',clause_id='r'),
        CapabilityClause('lift',['board'],'requires',True,'categorical',clause_id='l'),
    ], groups=[RequirementGroup('g',['board'],'any_of',['r','l'])])
    cb = CapabilityContract('p', [
        CapabilityClause('ramp',['board'],'requires',True,'categorical',clause_id='r'),
        CapabilityClause('lift',['board'],'requires',True,'categorical',clause_id='l'),
    ], groups=[RequirementGroup('g',['board'],'all_of',['r','l'])])
    assert capability_program_shape(CapabilityCompiler().compile(ca))[0] != capability_program_shape(CapabilityCompiler().compile(cb))[0]




def test_v16_confidence_threshold_is_structural_not_late_bound():
    base = CapabilityContract('p', [
        CapabilityClause('map_confidence',['access'],'>=',0.60,'lower',clause_id='m'),
    ])
    strict = CapabilityContract('p', [
        CapabilityClause('map_confidence',['access'],'>=',0.80,'lower',clause_id='m'),
    ])
    s1, n1 = capability_program_shape(CapabilityCompiler().compile(base), erase_numeric_thresholds=True)
    s2, n2 = capability_program_shape(CapabilityCompiler().compile(strict), erase_numeric_thresholds=True)
    assert s1 != s2
    assert n1 == n2 == 0


def _searcher(*, full_key: bool = False):
    return TypedSafeBudgetSearch(ServiceAutomaton(), config=SearchConfig(
        no_completion_value_guidance=True,
        lambda_edge_validity=0.0, lambda_learned_feasibility=0.0, lambda_frontier_ranker=0.0,
        use_viability_kernel=True, viability_pruning=True, viability_typed_pruning=True,
        use_precondition_antichain=True, use_semnaive_projected_acceptance_kernel=True,
        capability_projection=True, semnaive_delta_propagation=True, packed_frontier_dominance=True,
        use_parametric_capability_kernel=True,
        parametric_kernel_full_contract_key=full_key,
    ))


def test_v16_threshold_factored_reuse_is_exact_across_numeric_counterfactuals():
    transitions = _branching_graph()
    pred = HeuristicTransitionPredictor().predict(transitions)
    loose = CapabilityCompiler().compile(_ride_contract(limit=6.0))
    strict = CapabilityCompiler().compile(_ride_contract(limit=4.0))
    searcher = _searcher(full_key=False)
    sk1, cert1, d1 = searcher.search('same_scene', loose, transitions, pred, initial_anchor='B', initial_phase='board')
    sk2, cert2, d2 = searcher.search('same_scene', strict, transitions, pred, initial_anchor='B', initial_phase='board')
    assert d1['parametric_kernel_cache_miss'] == 1 and d1['parametric_kernel_cache_hit'] == 0
    assert d1['precondition_build_ms'] >= d1['parametric_kernel_source_build_ms']
    assert d2['parametric_kernel_cache_hit'] == 1 and d2['parametric_kernel_cache_miss'] == 0
    assert d2['parametric_kernel_thresholds_erased'] == 1
    assert d2['precondition_build_ms'] == d2['parametric_kernel_lookup_ms']

    # Compare the strict request against a fresh historical exact build.
    exact = TypedSafeBudgetSearch(ServiceAutomaton(), config=SearchConfig(
        no_completion_value_guidance=True,
        lambda_edge_validity=0.0, lambda_learned_feasibility=0.0, lambda_frontier_ranker=0.0,
        use_viability_kernel=True, viability_pruning=True, viability_typed_pruning=True,
        use_precondition_antichain=True, use_semnaive_projected_acceptance_kernel=True,
        capability_projection=True, semnaive_delta_propagation=True, packed_frontier_dominance=True,
    ))
    skx, certx, dx = exact.search('same_scene', strict, transitions, pred, initial_anchor='B', initial_phase='board')
    assert bool(sk2) == bool(skx)
    assert (cert2 is None) == (certx is None)
    assert d2['expansions'] == dx['expansions']


def test_v16_full_contract_key_is_matched_memo_control_without_threshold_reuse():
    transitions = _branching_graph(); pred = HeuristicTransitionPredictor().predict(transitions)
    searcher = _searcher(full_key=True)
    c1 = CapabilityCompiler().compile(_ride_contract(limit=6.0))
    c2 = CapabilityCompiler().compile(_ride_contract(limit=4.0))
    _, _, d1 = searcher.search('same_scene', c1, transitions, pred, initial_anchor='B', initial_phase='board')
    _, _, d2 = searcher.search('same_scene', c2, transitions, pred, initial_anchor='B', initial_phase='board')
    assert d1['parametric_kernel_cache_miss'] == 1
    assert d2['parametric_kernel_cache_miss'] == 1
    assert d2['parametric_kernel_cache_hit'] == 0


def test_v16_planner_wires_frozen_exact_ordering_and_parametric_kernel_only():
    p = CapPlanPlanner(PlannerConfig(
        algorithm_version='V16', evidence_grounded_runtime=True,
        use_parametric_capability_kernel=True,
    ))
    sc = p.searcher.config
    assert sc.use_semnaive_projected_acceptance_kernel and sc.use_precondition_antichain
    assert sc.use_parametric_capability_kernel and not sc.parametric_kernel_full_contract_key
    assert not sc.use_exact_robustness_ordering
    assert sc.lambda_edge_validity == 0.0 and sc.lambda_learned_feasibility == 0.0
    assert sc.lambda_frontier_ranker == 0.0
    assert p.searcher.frontier_ranker is None
    assert p.diagnostic_searcher is not None
