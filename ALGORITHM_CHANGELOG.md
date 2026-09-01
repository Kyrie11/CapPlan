# CapPlan Algorithm Changelog

## Versioning policy

Algorithm versions are separated from dataset/reviewfix versions.  Dataset fixes do not automatically create a new algorithm version.  A new algorithm version is reserved for a change to the planning mechanism, learned architecture, objective, or inference rule that should be compared experimentally as a new method.

## V1 — Relation-aware CASA surrogate + symbolic passenger-complete planning

**Status:** baseline algorithm definition.  The seed-13 results produced by reviewfix10 are **not attribution-valid** and must not be used as final paper evidence because the runtime/ablation wiring contained engineering errors listed below.

### V1 mechanism

1. **Passenger capability compiler** converts each functional capability contract into phase-scoped hard clauses, typed budgets, interface predicates, uncertainty specifications, and capability tokens.
2. **Passenger-complete service automaton** constrains the lifecycle `origin → access → wait → board → ride → alight → egress → destination`.
3. **CASA V1 surrogate** is a relation-aware transition MLP conditioned on transition action/source phase/target phase and compact capability tokens.  It predicts passenger-edge feasibility, typed numerical demand, uncertainty, dynamic availability, and completion value.  It is **not** a true heterogeneous-graph HGT/R-GCN implementation.
4. **Typed Safe-Budget Search (TSBS)** performs symbolic lifecycle, anchoring, interface, typed-resource, uncertainty, and dynamic-availability checks and uses learned priors only for evidence/search ordering.
5. The planner returns either a passenger-complete skeleton or a diagnostic failure certificate.

### reviewfix11 — V1 attribution repair (engineering only; algorithm version remains V1)

This repair intentionally does **not** introduce a new algorithmic mechanism.  It fixes experiment/runtime semantics so the next V1 run can support reliable attribution.

- `no_casa_net_transitions` now truly replaces learned CASA demand/uncertainty/availability/value outputs with deterministic geometric/service evidence.  Previously it still used the learned predictor and only neutralized completion value, making the ablation nearly identical to `no_completion_value_guidance`.
- Learned passenger-independent edge-validity probability is no longer multiplied into dynamic availability and therefore cannot trigger the hard dynamic-availability gate.  Edge validity is now only a search-order prior; symbolic transition tests remain authoritative.
- Categorical resources such as `ramp`, `lift`, `step_free`, and `door_side` are never overwritten by the continuous typed-demand head.  Only supervised numerical resources can replace numerical evidence.
- Added head-isolation diagnostics (`no_learned_demand`, `no_learned_uncertainty`, `no_learned_availability`) to localize a collapsed learned pipeline without changing the frozen benchmark.
- Added outcome-aware T4 metrics, including oracle-change response accuracy and success-flip recall, so a model that predicts failure for every passenger cannot obtain a deceptively high counterfactual score from mostly both-fail pairs.
- Added explicit attribution warnings when PCR/plan-return collapse or TSBS terminates at the initial frontier.
- Added direct CASA checkpoint evaluation on the frozen split to separate neural-head quality from downstream TSBS behavior.

### V1 attribution gate

A V1 run is considered suitable for component attribution only when all of the following hold:

- `PCR > 0` and `PlanReturnRate > 0` on the test split;
- `TSBS_expansions_p95 > 1` (the search actually traverses the service graph);
- `algorithm_attribution_ready=true` in `evaluation_semantics.json`;
- `no_casa_net_transitions` uses `HeuristicTransitionPredictor` and differs from full learned CASA where expected;
- CASA head metrics are reported separately (`edge_AUPRC`, `value_AUPRC`, per-resource normalized demand error, uncertainty coverage, availability error);
- T4 reports `CF_success_flip_recall` and conditional oracle-change response, not only aggregate `CRsp`;
- publication vehicle metrics come from method-specific integrated nuPlan closed-loop simulation, not `mock_strict`.

### Current dominant uncertainty before rerun

The reviewfix10 seed-13 run cannot distinguish algorithmic causes because every request fails before an accepting skeleton is found.  After reviewfix11, the head-isolation diagnostics should determine whether the remaining dominant bottleneck is learned typed demand, learned uncertainty/conservative margins, learned availability, or the limited V1 relation-MLP representation.  Only after that rerun should a V2 algorithm be designed.

## V2 — reserved

V2 is intentionally **not defined yet**.  The next algorithm version should be chosen from attribution-valid V1 evidence, with the goal of a CCF-A-level contribution rather than incremental tuning.  Candidate directions (true heterogeneous service-graph reasoning, constrained probabilistic resource inference, or another mechanism) must be justified by the repaired V1 bottleneck evidence.
