from capplan.data.accessibility_layer import synthetic_accessibility_graph
from capplan.data.capability_contracts import default_contract
from capplan.data.pudo_interface_layer import synthetic_pudo_anchors, synthetic_vehicle_interface
from capplan.models.frontier_ranker import build_frontier_features, frontier_feature_names
from capplan.planning.planner import CapPlanPlanner, PlannerConfig
from capplan.planning.typed_safe_budget_search import SearchLabel
from capplan.semantics.capability_compiler import CapabilityCompiler, RESOURCE_VOCAB
from capplan.semantics.typed_resource_algebra import init_ledger


class _ReverseRanker:
    def score_successors(self, rows, compiled, registry):
        return [float(len(rows) - i) for i in range(len(rows))]


def _first_feasible_successor():
    eid = "v3_feature"
    contract = default_contract("v3_feature:p0")
    planner = CapPlanPlanner(PlannerConfig(algorithm_version="V3", evidence_grounded_runtime=True, no_frontier_ranker=True))
    compiled = planner.compiler.compile(contract)
    graph = synthetic_accessibility_graph(eid)
    pudo = synthetic_pudo_anchors(eid)
    vehicle = synthetic_vehicle_interface(eid)
    transitions = planner.generator.generate(eid, graph, pudo, vehicle)
    first = next(e for e in transitions if e.from_phase == "origin" and e.action == "access")
    ledger = init_ledger({c.resource_name for c in compiled.clauses})
    label = SearchLabel(first.from_anchor, first.from_phase, ledger, 0.0, [], [])
    ok, new_ledger, step, _ = planner.searcher._try_expand(label, first, compiled, compiled.clauses, compiled.groups, None)
    assert ok
    succ = SearchLabel(first.to_anchor, first.to_phase, new_ledger, first.cost, [first], [step])
    return compiled, first, succ


def test_v3_frontier_feature_vector_is_stable_and_state_dependent():
    compiled, edge, succ = _first_feasible_successor()
    full = build_frontier_features(successor_label=succ, transition=edge, compiled=compiled, feature_mode="full")
    structural = build_frontier_features(successor_label=succ, transition=edge, compiled=compiled, feature_mode="structural")
    assert len(full) == len(frontier_feature_names()) == len(structural)
    # The last 3*R positions are the typed ledger observed/margin/future channels.
    tail = 3 * len(RESOURCE_VOCAB)
    assert any(abs(v) > 1e-9 for v in full[-tail:])
    assert all(abs(v) < 1e-9 for v in structural[-tail:])


def test_v3_ranker_changes_order_only_not_hard_acceptance():
    eid = "v3_safety"
    contract = default_contract("v3_safety:p0")
    graph = synthetic_accessibility_graph(eid)
    pudo = synthetic_pudo_anchors(eid)
    vehicle = synthetic_vehicle_interface(eid)

    base = CapPlanPlanner(PlannerConfig(algorithm_version="V3", evidence_grounded_runtime=True, no_frontier_ranker=True))
    r0 = base.plan(eid, contract, graph, pudo, vehicle)
    assert r0.success

    ranked = CapPlanPlanner(PlannerConfig(algorithm_version="V3", evidence_grounded_runtime=True, no_frontier_ranker=True))
    ranked.searcher.frontier_ranker = _ReverseRanker()
    ranked.searcher.config.lambda_frontier_ranker = 0.35
    r1 = ranked.plan(eid, contract, graph, pudo, vehicle)
    assert r1.success
    assert r0.skeleton is not None and r1.skeleton is not None


def test_v3_default_removes_v2_static_and_completion_value_guidance():
    planner = CapPlanPlanner(PlannerConfig(algorithm_version="V3", evidence_grounded_runtime=True, no_frontier_ranker=True))
    assert planner.searcher.config.no_completion_value_guidance is True
    assert planner.searcher.config.lambda_learned_feasibility == 0.0
    ref = CapPlanPlanner(PlannerConfig(algorithm_version="V3", evidence_grounded_runtime=True, v2_reference_runtime=True, no_frontier_ranker=True))
    assert ref.searcher.config.no_completion_value_guidance is False
    assert ref.searcher.config.lambda_learned_feasibility > 0.0
