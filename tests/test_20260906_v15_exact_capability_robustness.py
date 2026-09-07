from __future__ import annotations

from tests.test_20260905_v10_semnaive_capability_kernel import _branching_graph, _ride_contract
from capplan.models.predictors import HeuristicTransitionPredictor
from capplan.planning.capability_precondition_antichain import evaluate_precondition_teacher_target
from capplan.planning.capability_viability_kernel import build_capability_viability_kernel
from capplan.planning.planner import CapPlanPlanner, PlannerConfig
from capplan.planning.semnaive_capability_projected_kernel import build_semnaive_capability_projected_acceptance_kernel
from capplan.semantics.capability_compiler import CapabilityCompiler
from capplan.semantics.resource_registry import DEFAULT_REGISTRY
from capplan.semantics.service_automaton import ServiceAutomaton
from capplan.semantics.typed_resource_algebra import init_ledger


def test_v15_exact_robustness_target_is_multi_path_exact_and_state_dependent():
    transitions=_branching_graph(); compiled=CapabilityCompiler().compile(_ride_contract(limit=6.0))
    pred=HeuristicTransitionPredictor().predict(transitions)
    kernel=build_capability_viability_kernel(transitions,pred,ServiceAutomaton(),enumerate_suffixes=False)
    antichain=build_semnaive_capability_projected_acceptance_kernel(kernel,compiled,pred)
    ledger=init_ledger({'ride_time_s'},DEFAULT_REGISTRY)
    r0=evaluate_precondition_teacher_target(('R0','ride'),ledger,compiled,antichain)
    r1=evaluate_precondition_teacher_target(('R1','ride'),ledger,compiled,antichain)
    assert r0.viable and r1.viable
    assert r0.robust_margin is not None and r1.robust_margin is not None
    assert r0.robust_margin > r1.robust_margin
    assert r0.checked_summaries > 0 and r1.checked_summaries > 0


def test_v15_full_uses_exact_robustness_only_as_ordering_on_frozen_sncpk():
    p=CapPlanPlanner(PlannerConfig(algorithm_version='V15',evidence_grounded_runtime=True))
    sc=p.searcher.config
    assert sc.use_semnaive_projected_acceptance_kernel
    assert sc.use_precondition_antichain and sc.viability_typed_pruning
    assert sc.use_exact_robustness_ordering and not sc.exact_robustness_count_only
    assert sc.lambda_frontier_ranker > 0
    assert sc.lambda_edge_validity == 0.0 and sc.lambda_learned_feasibility == 0.0
    assert p.searcher.frontier_ranker is None
    assert p.diagnostic_searcher is not None


def test_v15_count_only_has_matched_antichain_scan_but_discards_typed_margin():
    p=CapPlanPlanner(PlannerConfig(algorithm_version='V15',evidence_grounded_runtime=True,exact_robustness_count_only=True))
    assert p.searcher.config.use_exact_robustness_ordering
    assert p.searcher.config.exact_robustness_count_only
    assert p.searcher.frontier_ranker is None


def test_v15_exact_and_legacy_controls_are_causally_separated():
    exact=CapPlanPlanner(PlannerConfig(algorithm_version='V15',evidence_grounded_runtime=True,no_exact_robustness_ordering=True))
    assert not exact.searcher.config.use_exact_robustness_ordering
    assert exact.searcher.config.lambda_frontier_ranker == 0.0
    assert exact.searcher.config.lambda_edge_validity == 0.0
    assert exact.searcher.config.lambda_learned_feasibility == 0.0
    legacy=CapPlanPlanner(PlannerConfig(algorithm_version='V15',evidence_grounded_runtime=True,no_exact_robustness_ordering=True,v15_legacy_static_guidance=True,no_learned_feasibility_guidance=False))
    assert not legacy.searcher.config.use_exact_robustness_ordering
    assert legacy.searcher.config.lambda_edge_validity > 0.0
    assert legacy.searcher.config.lambda_learned_feasibility > 0.0

from capplan.data.schemas import CapabilityClause, CapabilityContract, RequirementGroup
from capplan.planning.capability_precondition_antichain import _hard_semantic_robustness_margin


def test_v15r_robustness_respects_any_of_as_one_semantic_hard_unit():
    clauses=[
        CapabilityClause('ramp',['board'],'requires',True,'categorical',clause_id='ramp'),
        CapabilityClause('lift',['board'],'requires',True,'categorical',clause_id='lift'),
        CapabilityClause('door_width_m',['board'],'>=',0.8,'lower',clause_id='door'),
    ]
    contract=CapabilityContract('p',clauses,groups=[RequirementGroup('boarding',['board'],'any_of',['ramp','lift'],hard=True)])
    compiled=CapabilityCompiler().compile(contract)
    ok,semantic,flat=_hard_semantic_robustness_margin({'ramp':True,'lift':False,'door_width_m':0.9},compiled)
    assert ok
    assert semantic is not None and semantic >= 0.0
    assert flat is not None and flat < 0.0


def test_v15r_robustness_all_of_still_uses_worst_member():
    clauses=[
        CapabilityClause('ramp',['board'],'requires',True,'categorical',clause_id='ramp'),
        CapabilityClause('lift',['board'],'requires',True,'categorical',clause_id='lift'),
    ]
    contract=CapabilityContract('p',clauses,groups=[RequirementGroup('both',['board'],'all_of',['ramp','lift'],hard=True)])
    compiled=CapabilityCompiler().compile(contract)
    ok,semantic,flat=_hard_semantic_robustness_margin({'ramp':True,'lift':False},compiled)
    assert not ok
    assert semantic == -1.0 and flat == -1.0


def test_v15r_counterfactual_axis_falls_back_to_frozen_profile_id():
    from types import SimpleNamespace
    from capplan.evaluation.closed_loop import _counterfactual_axis
    contract=SimpleNamespace(passenger_id='episode42:cf_ramp_or_lift_required', metadata={})
    assert _counterfactual_axis(contract) == 'ramp_lift'
    base=SimpleNamespace(passenger_id='episode42:basic_service_complete', metadata={})
    assert _counterfactual_axis(base) == 'base'
