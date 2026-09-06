from __future__ import annotations

from copy import deepcopy

from capplan.models.predictors import HeuristicTransitionPredictor
from capplan.planning.compiled_transition_program import CompiledTransitionProgramCache
from capplan.planning.planner import CapPlanPlanner, PlannerConfig
from capplan.planning.typed_safe_budget_search import SearchConfig, SearchLabel, TypedSafeBudgetSearch
from capplan.semantics.capability_compiler import CapabilityCompiler
from capplan.semantics.service_automaton import ServiceAutomaton
from tests.test_20260905_v10_semnaive_capability_kernel import _branching_graph, _ride_contract


def _norm_result(res):
    ok, ledger, step, vios = res
    step_sig = None if step is None else (
        step.transition_id, step.phase, step.action,
        deepcopy(step.resource_state), deepcopy(step.margins), deepcopy(step.evidence),
    )
    vio_sig = [
        (v.phase, v.transition_id, v.resource_type, round(float(v.signed_margin), 12),
         v.evidence_source, round(float(v.confidence), 12), v.reason)
        for v in vios
    ]
    return bool(ok), deepcopy(ledger), step_sig, vio_sig


def test_v13_compiled_transition_program_is_exact_across_different_ledgers():
    transitions = _branching_graph()
    edge = next(e for e in transitions if e.transition_id == "r0")
    compiled = CapabilityCompiler().compile(_ride_contract(limit=3.0))
    predictions = HeuristicTransitionPredictor().predict(transitions)
    pred = predictions[edge.transition_id]
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
    cache = CompiledTransitionProgramCache()
    clauses, groups = compiled.clauses, compiled.groups

    # The same transition is evaluated under two different incoming ledgers.
    # V12 cannot reuse these because its state/ledger key differs; V13 should
    # reuse one compiled transition program while producing exact V11 results.
    labels = [
        SearchLabel("B", "board", {}, 0.0, [], []),
        SearchLabel("B", "board", {"ride_time_s": 2.5}, 0.0, [], []),
    ]
    for label in labels:
        ref = search._try_expand(label, edge, compiled, clauses, groups, pred)
        got = search._try_expand_with_compiled_transition_program(
            label, edge, compiled, clauses, groups, pred, cache,
        )
        assert _norm_result(got) == _norm_result(ref)

    assert cache.compiles == 1
    assert cache.misses == 1
    assert cache.hits == 1
    assert cache.applications == 2
    assert cache.fallbacks == 0


def test_v13_compiled_program_preserves_search_result_and_certificate():
    transitions = _branching_graph()
    compiled = CapabilityCompiler().compile(_ride_contract(limit=2.0))
    predictions = HeuristicTransitionPredictor().predict(transitions)
    common = dict(
        no_completion_value_guidance=True,
        lambda_edge_validity=0.0,
        lambda_learned_feasibility=0.0,
        lambda_frontier_ranker=0.0,
        use_viability_kernel=False,
    )
    ref = TypedSafeBudgetSearch(ServiceAutomaton(), config=SearchConfig(**common))
    opt = TypedSafeBudgetSearch(ServiceAutomaton(), config=SearchConfig(**common))
    sk0, cert0, d0 = ref.search(
        "ep", compiled, transitions, predictions, initial_anchor="B", initial_phase="board"
    )
    cache = CompiledTransitionProgramCache()
    sk1, cert1, d1 = opt.search(
        "ep", compiled, transitions, predictions, initial_anchor="B", initial_phase="board",
        compiled_transition_program_cache=cache,
    )
    assert (None if sk1 is None else (sk1.accepted, tuple(sk1.transitions), sk1.cost)) == (
        None if sk0 is None else (sk0.accepted, tuple(sk0.transitions), sk0.cost)
    )
    assert (None if cert1 is None else cert1.__dict__) == (None if cert0 is None else cert0.__dict__)
    assert d1["expansions"] == d0["expansions"]
    assert cache.compiles > 0
    assert cache.fallbacks == 0


def test_v13_planner_wiring_uses_program_only_for_diagnostic_replay():
    full = CapPlanPlanner(PlannerConfig(algorithm_version="V13", evidence_grounded_runtime=True))
    assert full.searcher.config.use_semnaive_projected_acceptance_kernel
    assert full.diagnostic_searcher is not None
    assert full._compiled_diagnostic_transition_program_enabled
    assert not full._shared_diagnostic_semantic_cache_enabled

    v12 = CapPlanPlanner(PlannerConfig(
        algorithm_version="V13", evidence_grounded_runtime=True, v12_reference_runtime=True,
    ))
    assert not v12._compiled_diagnostic_transition_program_enabled
    assert v12._shared_diagnostic_semantic_cache_enabled

    v11 = CapPlanPlanner(PlannerConfig(
        algorithm_version="V13", evidence_grounded_runtime=True, v11_reference_runtime=True,
    ))
    assert not v11._compiled_diagnostic_transition_program_enabled
    assert not v11._shared_diagnostic_semantic_cache_enabled

    no_program = CapPlanPlanner(PlannerConfig(
        algorithm_version="V13", evidence_grounded_runtime=True,
        no_compiled_diagnostic_transition_program=True,
    ))
    assert not no_program._compiled_diagnostic_transition_program_enabled

    legacy = CapPlanPlanner(PlannerConfig(
        algorithm_version="V13", evidence_grounded_runtime=True,
        v13_legacy_static_guidance=True,
    ))
    assert legacy.searcher.config.lambda_learned_feasibility > 0.0
    assert legacy._compiled_diagnostic_transition_program_enabled
