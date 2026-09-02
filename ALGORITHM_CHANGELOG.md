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

## V2 — Evidence-Grounded CASA with dual-channel typed feasibility

**Status:** first algorithmic V2.  reviewfix12 closes the diagnostic attribution needed to justify this change.  The V1 *main* full-model attribution gate remains failed (full V1 still has zero PCR), so V2 does not claim that every symbolic component has already received a publication-quality gain attribution.  What is attribution-valid is the CASA/TSBS interface diagnosis: the corrected 2x2 mean/sigma factorial shows that learned demand and learned uncertainty independently corrupt hard feasibility, while restoring explicit saved evidence for both exactly recovers the verifier-feasible set on the frozen test benchmark.

### V2 motivation

CapPlan's core claim is retained: complete-trip functional requirements are compiled into executable passenger-complete acceptance semantics.  A neural estimator must therefore not be allowed to silently redefine an observed/derived physical fact before the symbolic contract is evaluated.  V1 violated this separation by overwriting numerical transition evidence with learned demand means and sigmas.

### V2 mechanism

1. **Hard evidence channel (new):** non-missing typed transition evidence and its evidence uncertainty are the authoritative inputs to the typed resource ledger. Missing hard evidence remains missing and therefore fail-closed according to the compiled contract. Neural imputation cannot convert missing evidence into feasible truth.
2. **Learned guidance channel (new):** CASA still predicts edge validity, dynamic availability, completion value, typed demand, and uncertainty. Learned demand/uncertainty are retained as audit/guidance outputs rather than overwriting hard evidence.
3. **Typed learned-feasibility prior (new):** numeric CASA demand/sigma predictions are compared with active capability-token thresholds to produce a soft per-transition feasibility prior. This prior affects queue ordering only; it is never a hard gate.
4. **Passenger Capability Compiler, service automaton, typed resource algebra, TSBS, and failure certificates are retained.** These are the executable acceptance semantics that implement the passenger-complete motivation.
5. **Evaluation fast path (engineering, not algorithmic):** when a frozen dataset already contains candidate transitions, evaluation no longer parses the full accessibility graph for every episode/variant because the planner does not consume that graph in this path. This changes wall time only, not planning semantics.
6. **T5 metric repair (evaluation):** retain phase accuracy for continuity, but add phase macro-F1 and whole-certificate exact match (phase + resource + source). The aggregate `DF` now uses phase macro-F1 rather than majority-sensitive phase accuracy.

### Why V2 does not simply feed physical targets back into a regressor

The frozen benchmark already stores processed path/interface evidence such as access distance, slope, path width, and clearance in `CandidateTransition.resource_evidence`. Feeding those exact values as neural inputs while training the same values as demand targets would create target-equivalent leakage and an artificially easy regression task. V2 therefore treats explicit evidence as evidence, not as a target to be re-guessed. A future raw-perception/heterogeneous-graph CASA can learn these quantities only when lower-level sensor/map primitives are exposed separately from the verifier resource targets.

### V2 preregistered experiments

- `full V2` versus `no_evidence_grounding`: tests whether the new hard/learned channel separation removes V1 collapse.
- `no_learned_feasibility_guidance`: tests whether the learned typed prior improves search efficiency without changing hard feasibility.
- `no_completion_value_guidance`: separates the new typed feasibility prior from the historical completion-value prior.
- `no_conservative_margins`: must be interpreted with oracle-referenced false-accept metrics, not PCR alone.
- symbolic compiler/automaton/ledger ablations remain useful, but the current scalar-ledger ablation should not be over-interpreted as a calibrated alternative until its scalar budget baseline is independently tuned.

### Fast-iteration policy

`run_v2_fast_experiments.sh` evaluates a deterministic 256-episode subset and runs independent variant groups concurrently on two GPUs. `run_v2_full_experiments.sh` performs the confirmatory full test only after the fast gate passes. Neither script invokes the other, and neither runs nuPlan closed-loop simulation during algorithm iteration.

## V2-fast seed13 decision — evidence grounding validated; completion value retired

The preregistered 256-episode V2-fast experiment (`2048` passenger requests) validates the V2 mechanism and determines the next algorithmic direction:

- **Full V2** exactly matches the frozen verifier: `OraclePCR=PCR=0.05078125`, `TP=104`, `FP=0`, `FN=0`, `PCDecisionF1=1.0`, `CF_success_flip_precision=1.0`, and `CF_success_flip_recall=1.0`.
- **w/o evidence grounding** reproduces the V1 collapse: `PCR=0`, `PCDecisionF1=0`, `PCFalseRejectRate=1.0`, and `TSBS p95=1`.  Thus explicit typed evidence is an essential hard-feasibility channel rather than an engineering workaround.
- **w/o learned feasibility guidance** preserves every hard passenger-complete decision but increases TSBS expansions from `18.879 -> 20.441` on average and `79 -> 87.95` at p95.  Request-paired analysis gives a mean reduction of `1.562` expansions per request; episode-cluster bootstrap 95% CI is approximately `[0.98, 2.23]`.  Learned guidance is therefore useful, but its proper role is search ordering only.
- **w/o completion-value guidance** is effectively unchanged (`18.889` mean expansions vs `18.879` full) and has slightly lower measured latency.  The historical completion-value head is therefore removed from the default algorithmic path rather than retained for architectural complexity.
- **w/o conservative margins** increases raw PCR but produces `132` false accepts on the fast subset (`PCDecisionPrecision=0.441`, `PCFalseAcceptRate=0.0679`) and degrades counterfactual precision.  Conservative evidence remains part of executable acceptance semantics.

**Decision:** V2 establishes the correct authority structure (evidence-grounded hard acceptance + non-authoritative learned guidance).  The next algorithm should not return to physical-demand overwrite, and it should not invest further in the global completion-value BCE head.  The remaining research problem is to learn a stronger *state-dependent* ordering of the already feasible typed search frontier.

## V3 — Executable Capability Frontier (ECF) + Frontier-Guided TSBS

**Status:** algorithm candidate for CCF-A-level development.  V3 retains the validated V2 hard acceptance semantics and replaces the weak transition-static learned guidance with a state-dependent capability-frontier ranker.

### Motivation

CapPlan's paper-level claim is that complete-trip functional requirements are compiled into executable acceptance semantics.  Therefore a learned module should reason *inside the feasible frontier* rather than redefine it.  V2 validates this separation but its learned feasibility prior is transition-static: it compares predicted single-edge demand with thresholds before considering the current accumulated typed ledger.  Passenger-complete planning is state-dependent because the same transition can be attractive or unattractive depending on already consumed access/wait/ride budgets, bottleneck residuals, future phase requirements, and the remaining lifecycle.

### V3 mechanism

1. **Executable hard acceptance is unchanged from V2.**  Passenger Capability Compiler + service automaton + evidence-grounded typed ledger + conservative uncertainty + TSBS remain authoritative.  Missing hard evidence stays fail-closed.
2. **Executable Capability Frontier (new planning representation).**  After a candidate successor has passed the symbolic one-step checks, V3 constructs a state-dependent frontier representation from:
   - current service phase and remaining lifecycle;
   - successor typed ledger residuals under the concrete compiled contract;
   - which typed resources have been observed;
   - which typed requirements remain active in future phases;
   - transition cost, dynamic availability, evidence confidence/missingness, and symbolic transition validity.
   The representation uses runtime physical evidence only to form normalized contract residuals.  It never uses oracle skeleton membership or completion labels as input features.
3. **Frontier-relative ranking (new learning objective).**  Training replays offline verifier skeletons.  At each oracle search state, the oracle successor is ranked against alternative successors that already pass the same symbolic one-step feasibility checks.  The default objective is pairwise logistic ranking rather than global completion-value BCE.  This removes the extreme global class-imbalance problem and learns the local decision search actually needs.
4. **Frontier-Guided TSBS (new inference rule).**  For all hard-feasible sibling successors of one popped label, the ranker scores the frontier in one batch.  Scores are converted to a within-frontier softmax prior and affect queue ordering only.  A single feasible successor receives prior 1 and no learned penalty.  Hard feasibility, acceptance, and failure certification remain independent of the learned ranker.
5. **Completion-value head retired from V3 default.**  V2-fast shows no measurable search benefit.  It remains only as a backward V2-reference control.
6. **V2 static typed-demand guidance retired from V3 default.**  It remains available through `v2_reference_runtime` for paired comparison.  V3 does not use learned demand/sigma as its principal search signal.
7. **Tiny-frontier inference is CPU-optimized.**  The V3 ranker uses a NumPy implementation of the trained MLP for small sibling frontiers, avoiding one GPU kernel launch per TSBS expansion while CASA can remain on an A30.

### V3 attribution experiments

The first V3 fast experiment uses the same deterministic 256-episode subset as V2-fast and compares:

- `v3_full_pairwise`: full state-dependent ECF features + pairwise frontier ranking;
- `v3_no_frontier`: identical hard planner with no ECF ranker;
- `v2_reference`: exact V2 static learned-feasibility + completion-value ordering in the V3 codebase;
- `v3_structural_pairwise`: pairwise ranker with typed ledger residual/future-requirement channels removed;
- `v3_full_bce`: identical full ECF representation trained with global BCE rather than frontier-relative pairwise ranking.

Primary safety gate (must be unchanged): `PCDecisionF1 >= 0.99`, `PCFalseAcceptRate=0`, `PCFalseRejectRate=0`, and counterfactual success-flip precision/recall must not regress materially.

Primary algorithmic gain: compared pairwise on identical requests, V3 should reduce TSBS expansions relative to both `v3_no_frontier` and `v2_reference`.  Search-expansion deltas are reported with an episode-cluster bootstrap 95% CI; planner latency is secondary because GPU/CPU timing is noisier.

Mechanism gates:

- `v3_full_pairwise` better than `v3_structural_pairwise` supports the value of typed ledger residual + future requirement features;
- `v3_full_pairwise` better than `v3_full_bce` supports frontier-relative ranking rather than global completion classification;
- if V3 does not improve over the V2 reference with a positive paired CI, the ECF ranker is not retained merely for novelty.

### Scope and novelty boundary

Pairwise search ranking and learned heuristics are established ideas in automated planning; V3 does **not** claim ranking itself as novel.  The intended contribution is the passenger-complete, capability-compiled search state: a learned ranker operates on typed residual/future-requirement frontiers only after executable hard semantics admit a successor, so learning can accelerate complete-trip planning without becoming the authority that defines service feasibility.
