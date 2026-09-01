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

### reviewfix12 — V1 attribution closure (engineering/evaluation only; algorithm version remains V1)

The uploaded post-reviewfix11 seed-13 run still **fails the preregistered V1 attribution gate**: full learned V1 has `PCR=0`, `PlanReturnRate=0`, and `TSBS_expansions_p95=1`.  Therefore this revision deliberately does **not** define V2.

The repaired run nevertheless localizes the V1 failure much more sharply:

- the historical `no_learned_demand` diagnostic returns exactly the verifier-feasible set (`506/7976` successes), but code audit shows that reviewfix11 accidentally disabled both learned demand mean **and** learned uncertainty in that branch;
- `no_learned_uncertainty` lets search traverse much deeper but still returns zero passenger-complete plans, showing that learned demand mean alone remains incompatible with the typed feasibility boundary;
- `no_learned_availability` is identical to full, so availability is not the current dominant bottleneck;
- removing conservative margins recovers many oracle-feasible requests but also creates many oracle-infeasible accepts, so margin removal is not a valid algorithmic solution.

reviewfix12 closes the remaining attribution leakage and strengthens evaluation:

1. `no_learned_demand` now replaces **only** the learned point estimate with saved symbolic evidence while retaining the learned uncertainty head.
2. `no_learned_uncertainty` retains the learned point estimate while restoring saved symbolic sigma.
3. `no_learned_demand_uncertainty` is added as the explicit fully-symbolic factorial control.  The four mean/sigma combinations can therefore be compared without hidden coupling.
4. Passenger-complete decisions are now scored against the frozen exhaustive verifier with `OraclePCR`, `PCDecisionPrecision`, `PCDecisionRecall`, `PCDecisionF1`, `PCFalseAcceptRate`, `PCFalseRejectRate`, and confusion counts.  This prevents relaxed ablations from appearing superior merely because they return more plans.
5. T4 now reports capability-induced success-flip **precision** as well as recall, plus per-axis flip support.
6. `run_v1_experiments.sh` adds a deterministic-evidence symbolic-core attribution suite so capability compiler / service automaton / typed ledger can be tested independently of the collapsed learned evidence layer.
7. The same V1 experiment script remains the single entry point; use a new `V1_ROOT` (for example `outputs/eval/v1_attr12`) rather than creating additional scripts or package lineages.

**Decision:** reviewfix12 is an attribution/evaluation repair, not a new planning mechanism.  Formal CCF-A V2 design remains blocked until the corrected 2x2 demand/uncertainty factorial and deterministic-evidence symbolic-core suite are complete.

## V2 — reserved

V2 is intentionally **not defined yet**.  The next algorithm version should be chosen from attribution-valid V1 evidence, with the goal of a CCF-A-level contribution rather than incremental tuning.  Candidate directions (true heterogeneous service-graph reasoning, constrained probabilistic resource inference, or another mechanism) must be justified by the repaired V1 bottleneck evidence.
