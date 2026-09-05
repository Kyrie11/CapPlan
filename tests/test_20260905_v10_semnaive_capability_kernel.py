from __future__ import annotations

from capplan.data.schemas import CandidateTransition, CapabilityClause, CapabilityContract, ResourceEvidence, TransitionTests
from capplan.models.predictors import HeuristicTransitionPredictor
from capplan.planning.capability_projected_precondition_kernel import build_capability_projected_acceptance_kernel
from capplan.planning.capability_viability_kernel import build_capability_viability_kernel
from capplan.planning.planner import CapPlanPlanner, PlannerConfig
from capplan.planning.semnaive_capability_projected_kernel import build_semnaive_capability_projected_acceptance_kernel
from capplan.planning.typed_safe_budget_search import SearchConfig, TypedSafeBudgetSearch
from capplan.semantics.capability_compiler import CapabilityCompiler
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


def _branching_graph():
    # Several paths merge repeatedly. V9's state worklist re-propagates the
    # whole child frontier after each admission, whereas V10 should propagate
    # only newly admitted deltas.
    rows = []
    for i, x in enumerate((1.0, 2.0, 3.0, 4.0)):
        rows.append(_edge(f"r{i}", "B", f"R{i}", "board", "ride", "ride", [
            _ev("ride_time_s", "cumulative", x),
            _ev("slope", "upper", 0.01 * (5-i)),
        ], cost=0.2 + i * 0.1))
        rows.append(_edge(f"a{i}", f"R{i}", "A", "ride", "alight", "alight", [
            _ev("ride_time_s", "cumulative", 0.5 * i)
        ], cost=1.0))
    rows += [
        _edge("e0", "A", "E0", "alight", "egress", "egress", cost=1.0),
        _edge("e1", "A", "E1", "alight", "egress", "egress", cost=1.1),
        _edge("d0", "E0", "D", "egress", "destination", "egress", cost=1.0),
        _edge("d1", "E1", "D", "egress", "destination", "egress", cost=1.0),
    ]
    return rows


def _sig_rows(antichain):
    # Compare semantic effects/preconditions, not arbitrary witness ordering.
    def norm(v):
        if hasattr(v, "ok"):
            return ("pred", bool(v.ok), repr(v.observed), repr(v.required), str(v.operator))
        try:
            return round(float(v), 9)
        except Exception:
            return repr(v)
    out = {}
    for state, rows in antichain.summaries.items():
        out[state] = sorted((
            tuple(sorted((k, norm(v)) for k, v in row.effects.items())),
            tuple(sorted(row.required_observed)),
            tuple(sorted(row.active_clause_ids)),
            tuple(sorted(row.active_group_ids)),
        ) for row in rows)
    return out


def test_v10_semnaive_and_packed_reproduce_v9_projected_fixed_point():
    transitions = _branching_graph()
    compiled = CapabilityCompiler().compile(_ride_contract())
    pred = HeuristicTransitionPredictor().predict(transitions)
    kernel = build_capability_viability_kernel(transitions, pred, ServiceAutomaton(), enumerate_suffixes=False)
    v9 = build_capability_projected_acceptance_kernel(kernel, compiled, pred)
    v10 = build_semnaive_capability_projected_acceptance_kernel(kernel, compiled, pred)
    assert _sig_rows(v10) == _sig_rows(v9)
    assert v10.complete == v9.complete
    assert v10.projected_resource_count == v9.projected_resource_count == 1
    assert v10.delta_propagations > 0
    assert v10.frontier_packed_fastpath > 0


def test_v10_delta_and_packed_controls_are_each_semantically_exact():
    transitions = _branching_graph()
    compiled = CapabilityCompiler().compile(_ride_contract())
    pred = HeuristicTransitionPredictor().predict(transitions)
    kernel = build_capability_viability_kernel(transitions, pred, ServiceAutomaton(), enumerate_suffixes=False)
    full = build_semnaive_capability_projected_acceptance_kernel(kernel, compiled, pred)
    no_delta = build_semnaive_capability_projected_acceptance_kernel(
        kernel, compiled, pred, use_delta_propagation=False, use_packed_dominance=True,
    )
    no_packed = build_semnaive_capability_projected_acceptance_kernel(
        kernel, compiled, pred, use_delta_propagation=True, use_packed_dominance=False,
    )
    assert _sig_rows(full) == _sig_rows(no_delta) == _sig_rows(no_packed)
    assert full.complete == no_delta.complete == no_packed.complete


def test_v10_search_reproduces_v9_decision_and_expansions():
    transitions = _branching_graph()
    compiled = CapabilityCompiler().compile(_ride_contract(limit=6.0))
    pred = HeuristicTransitionPredictor().predict(transitions)
    common = dict(
        no_completion_value_guidance=True, lambda_edge_validity=0.0,
        lambda_learned_feasibility=0.0, lambda_frontier_ranker=0.0,
        use_viability_kernel=True, viability_pruning=True,
        viability_typed_pruning=True, use_precondition_antichain=True,
        use_rejection_antichain=False, viability_use_proof_envelope=False,
    )
    v9_search = TypedSafeBudgetSearch(ServiceAutomaton(), config=SearchConfig(
        **common, use_capability_projected_acceptance_kernel=True,
    ))
    v10_search = TypedSafeBudgetSearch(ServiceAutomaton(), config=SearchConfig(
        **common, use_semnaive_projected_acceptance_kernel=True,
    ))
    sk9, cert9, d9 = v9_search.search("ep", compiled, transitions, pred, initial_anchor="B", initial_phase="board")
    sk10, cert10, d10 = v10_search.search("ep", compiled, transitions, pred, initial_anchor="B", initial_phase="board")
    assert bool(sk10) == bool(sk9)
    assert (cert10 is None) == (cert9 is None)
    assert d10["expansions"] == d9["expansions"]
    assert d10["projected_resource_count"] == d9["projected_resource_count"]
    assert d10["delta_propagations"] > 0


def test_v10_planner_wiring_and_v9_reference_controls():
    planner = CapPlanPlanner(PlannerConfig(algorithm_version="V10", evidence_grounded_runtime=True))
    assert planner.searcher.config.use_semnaive_projected_acceptance_kernel
    assert not planner.searcher.config.use_capability_projected_acceptance_kernel
    assert planner.searcher.config.semnaive_delta_propagation
    assert planner.searcher.config.packed_frontier_dominance
    assert planner.diagnostic_searcher is not None

    ref = CapPlanPlanner(PlannerConfig(
        algorithm_version="V10", evidence_grounded_runtime=True, v9_reference_runtime=True,
    ))
    assert ref.searcher.config.use_capability_projected_acceptance_kernel
    assert not ref.searcher.config.use_semnaive_projected_acceptance_kernel

    no_delta = CapPlanPlanner(PlannerConfig(
        algorithm_version="V10", evidence_grounded_runtime=True, no_semnaive_delta_propagation=True,
    ))
    assert no_delta.searcher.config.use_semnaive_projected_acceptance_kernel
    assert not no_delta.searcher.config.semnaive_delta_propagation

    no_packed = CapPlanPlanner(PlannerConfig(
        algorithm_version="V10", evidence_grounded_runtime=True, no_packed_frontier_dominance=True,
    ))
    assert no_packed.searcher.config.use_semnaive_projected_acceptance_kernel
    assert not no_packed.searcher.config.packed_frontier_dominance

def test_v10_packed_dominance_matches_reference_on_numeric_and_categorical_cases():
    from capplan.planning.capability_precondition_antichain import SuffixEffectSummary, _summary_dominates
    from capplan.planning.semnaive_capability_projected_kernel import _make_plan, _pack_summary, _packed_dominates
    from capplan.semantics.resource_registry import DEFAULT_REGISTRY
    from capplan.semantics.typed_resource_algebra import PredicateState

    compiled = CapabilityCompiler().compile(CapabilityContract("p", [
        CapabilityClause("ride_time_s", ["ride"], "<=", 10.0, "cumulative", clause_id="rt"),
        CapabilityClause("path_width_m", ["access"], ">=", 1.0, "lower", clause_id="pw"),
        CapabilityClause("step_free", ["board"], "requires", True, "categorical", clause_id="sf"),
    ]))
    support = {"ride_time_s", "path_width_m", "step_free"}
    plan = _make_plan(compiled, DEFAULT_REGISTRY, support)
    pred_ok = PredicateState(True, True, True, "requires", "test", 1.0, [])
    pred_bad = PredicateState(False, False, True, "requires", "test", 1.0, [])
    rows = [
        {"ride_time_s": 2.0, "path_width_m": 2.0, "step_free": pred_ok},
        {"ride_time_s": 3.0, "path_width_m": 1.5, "step_free": pred_ok},
        {"ride_time_s": 2.0, "path_width_m": 2.0, "step_free": pred_bad},
    ]
    summaries = [SuffixEffectSummary((),0.0,r,{}, {}, {}, ("rt","pw","sf"), ()) for r in rows]
    for a in summaries:
        for b in summaries:
            stats = {}
            pa, pb = _pack_summary(a, plan, DEFAULT_REGISTRY), _pack_summary(b, plan, DEFAULT_REGISTRY)
            assert _packed_dominates(a, pa, b, pb, DEFAULT_REGISTRY, stats) == _summary_dominates(a, b, DEFAULT_REGISTRY)
