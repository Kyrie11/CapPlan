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

## V3 full seed13 decision — hard semantics PASS; ECF ranker NO-GO against V2

The full frozen-test run (`997` episodes / `7976` passenger requests) is attribution-valid for the top-level V3 mechanism comparison.  All paired variants use the same frozen requests, evidence-grounded hard semantics, and CASA checkpoint; the V3 ranker changes only queue ordering after one-step symbolic feasibility.  The full method retains exact verifier decisions (`PCDecisionF1=1.0`, `PCFalseAcceptRate=0`, `PCFalseRejectRate=0`, capability success-flip precision/recall `=1.0`).  The expected failure controls also behave coherently: removing evidence grounding reproduces total false rejection, while removing conservative margins creates false accepts.

The preregistered efficiency gate, however, fails:

- V3 full vs no-frontier: `18.5903` vs `19.0181` mean expansions, a modest `2.25%` reduction; episode-clustered paired delta (reference minus V3) is about `+0.428`, 95% CI `[+0.319,+0.549]`.
- V3 full vs V2 reference: `18.5903` vs `17.9919` mean expansions.  V3 is **3.33% worse**; paired delta (V2 minus V3) is about `-0.598`, 95% CI `[-0.775,-0.438]`.
- V3 also adds substantial runtime overhead in the current Python implementation: roughly `+14.3 ms/request` relative to V2 on the full paired run.

The degradation is continuation-depth dependent.  Relative to V2, V3 adds only about `0.15` expansions on access-failure requests, but about `+2.90` on alight failures and `+4.58` on egress failures; on the `506` oracle-success requests it adds about `+2.26` expansions on average.  Therefore the dominant V3 bottleneck is not hard feasibility or dataset construction.  It is insufficient *suffix/continuation reasoning* in the learned ordering rule.

Code audit explains the mismatch between the V3 name and its operational information.  `future_requirement` is largely a binary indicator that a resource is active in a later phase; the ranker receives no explicit lower bound on the typed burden/affordance still required to reach destination.  Training is also trace-imitation biased: only oracle-success contracts generate frontier pairs, the recorded oracle successor is the single positive, and all other one-step feasible siblings are negatives even when they may admit another valid passenger-complete continuation.  This under-supervises the majority infeasible requests and makes the objective closer to imitating one verifier trace than minimizing TSBS work over the executable feasible set.

**Decision:** per the V3 preregistration, the V3 frontier ranker is not retained as the default merely for novelty.  Its result remains a useful negative result demonstrating that local feasible-frontier ranking is insufficient without a faithful continuation representation.

## V4 — Capability Continuation Envelope (CCE) + Continuation-Aware TSBS

**Status:** next algorithm candidate.  V4 is a major mechanism change justified by the full V3 NO-GO result; it requires no new neural training for the first attribution round.

### V4 motivation

Passenger-complete planning is a typed *prefix + suffix* feasibility problem.  The forward TSBS ledger answers what capability budget has already been consumed, but V3 only approximates what remains through a learned binary future-requirement representation.  V4 compiles the same passenger contract into a second executable object: an optimistic typed continuation envelope from every service state to an accepting destination.

The intended paper-level structure becomes:

`Passenger-complete semantics → capability compilation → evidence-grounded typed feasibility → forward typed ledger + backward typed continuation envelope → continuation-aware TSBS → executable acceptance / diagnostic rejection`.

This keeps learning subordinate to executable semantics and strengthens the algorithm around the paper's core rather than around a neural backbone.

### V4 mechanism

1. **Validated hard authority is unchanged.**  V2 evidence grounding, the Passenger Capability Compiler, service automaton, conservative typed resource algebra, dynamic-availability gate, and final symbolic acceptance remain authoritative.
2. **Capability Continuation Envelope (new).**  For one compiled passenger contract and the saved candidate-transition graph, V4 computes from every `(anchor, phase)`:
   - exact structural reachability to an accepting `destination` under lifecycle/static/dynamic hard transition gates;
   - minimum remaining service-transition cost;
   - an optimistic suffix value for each non-group hard numeric typed resource.
3. **Type-specific backward composition (new).**  The suffix uses the same resource-extension algebra as forward TSBS: sum for cumulative resources, max for upper bottlenecks, min for lower affordances, and independent-risk composition for probabilistic resources.  Each resource is relaxed independently over its own best suffix path.  The resulting vector need not correspond to one common path; this is intentional because it is an optimistic relaxation.
4. **Sound impossibility pruning (new).**  For a forward label with ledger `R`, V4 composes `R` with the optimistic suffix bound.  If even this independently optimistic relaxation violates an ungrouped hard numeric capability clause, no concrete common suffix can satisfy that clause; the successor can therefore be pruned without changing returned-plan soundness.  Categorical and `any_of/not` requirement-group members are deliberately excluded from independent-resource pruning to avoid turning disjunctions into unsafe conjunctions.
5. **Continuation-aware priority (new).**  Non-pruned labels receive a soft ordering term from minimum remaining service cost and optimistic capability headroom.  This term never changes `Allow` or final `Sat`.
6. **V2 static learned typed-feasibility prior is restored as a secondary ordering signal.**  Full V3 shows that replacing it with the local ECF ranker is harmful.  V4 initially combines the empirically useful V2 per-transition prior with the symbolic CCE; ablation decides whether that learned prior remains necessary after CCE.
7. **V3 pairwise ranker and global completion-value head are absent from the V4 default.**  No V4 retraining is required for the first fast experiment.

### V4 soundness boundary

V4 does **not** claim novelty for generic bidirectional search, resource-constrained A*, or admissible lower bounds.  Those are established search ideas.  The intended contribution is the capability-compiled *heterogeneous typed continuation relaxation* coupled to the passenger lifecycle: forward consumption and optimistic suffix obligations are represented in the same non-substitutable resource algebra used by executable passenger-complete acceptance.

For every independently relaxed numeric hard clause, the suffix value is chosen optimistically.  Therefore an actual common continuation cannot be better than the relaxed value on that dimension.  If the optimistic composition already violates the clause, pruning is sound.  The current implementation intentionally does not use this argument for grouped categorical predicates.

### V4 preregistered fast experiment

Use the same deterministic `256`-episode / roughly `2048`-request test subset before any full confirmation.  No training is run.

Required variants:

- `v4_full`: CCE pruning + CCE priority + V2 static learned feasibility;
- `v4_no_cce`: identical V4 runtime without the continuation envelope;
- `v2_reference`: exact V2 ordering control;
- `v4_priority_only`: CCE priority, no CCE pruning;
- `v4_pruning_only`: CCE pruning, no CCE priority;
- `v4_no_static_guidance`: CCE with the V2 learned static prior removed.

Safety gate:

- `PCDecisionF1 >= 0.99`;
- `PCFalseAcceptRate = 0` and `PCFalseRejectRate = 0`;
- capability success-flip precision/recall must not materially regress;
- T5 phase/resource/source macro-F1 and certificate exact match must not materially regress versus the V2 reference (use a 0.01 absolute drop as the fast-review trigger, not as an automatic paper claim).

Primary algorithm gate:

- paired `V4 full` vs `V2 reference` TSBS expansion saving must be `> 0`;
- the episode-cluster bootstrap 95% CI lower bound for `(V2 expansions - V4 expansions)` must be `> 0`.

Mechanism interpretation:

- if full beats priority-only, sound continuation pruning contributes beyond ordering;
- if full beats pruning-only, continuation headroom/cost ordering contributes beyond pruning;
- if `v4_no_static_guidance` is statistically indistinguishable from full, remove the legacy learned static prior and simplify the final method;
- if CCE preserves decisions but cannot beat V2, do not proceed to the full split.  The next branch should then expose lower-level raw spatial/dynamic graph primitives and build a genuine heterogeneous encoder rather than adding another local ranking loss.

### Fast/full execution policy

- `scripts/run_v4_fast_experiments.sh`: deterministic 256-episode subset, two-GPU parallel variant groups, no training and no real nuPlan closed loop.
- `scripts/run_v4_full_experiments.sh`: 997-episode frozen test confirmation, only after the fast gate passes.
- `scripts/compare_search_efficiency.py`: request-paired comparison with episode-cluster bootstrap.

Real nuPlan method-specific closed-loop simulation remains deferred until passenger/service algorithm selection is stable; `mock_strict` remains development-only and cannot support the final vehicle-planning SOTA claim.

## V4-fast seed13 decision — efficiency submechanism PASS; full V4 STOP

**Status:** V4 as a complete paper mechanism is **STOP** under its own preregistered gate.  The fast run is nevertheless attribution-valid for hard passenger-complete decisions and search-efficiency mechanism analysis, and it identifies one useful submechanism to carry forward.

The deterministic fast subset contains `256` episodes / `2048` passenger requests and every compared variant evaluates the same requests with the same frozen CASA seed-13 checkpoint.  All six requested V4 controls complete without runtime/traceback failures.  V4 full preserves verifier decisions exactly (`PCDecisionF1=1.0`, `PCFalseAcceptRate=0`, `PCFalseRejectRate=0`) and capability success-flip precision/recall remain `1.0`, so the search comparison is not confounded by a hard-semantics collapse.

### Search result

V4 full reduces mean TSBS expansions from the exact V2 reference `18.8794` to `12.1270`, a paired saving of `6.7524` expansions/request (`35.77%`).  The episode-clustered 95% CI for `(V2 - V4)` is `[4.9253, 8.7862]`, with zero passenger-complete decision mismatches.  Therefore the preregistered *primary efficiency* gate passes strongly.

Mechanism controls isolate where the gain comes from:

- `priority-only`: `18.8569` mean expansions, essentially V2;
- `pruning-only`: `12.1592` mean expansions, essentially full V4;
- full V4 vs pruning-only saves only `0.0322` expansion/request (about `0.27%`, CI `[0.0088,0.0674]`).

Thus the V4 continuation priority is not a meaningful mechanism and is retired.  The useful V4 component is backward dead-end pruning.  The V2 static learned typed-feasibility prior remains a secondary ordering signal: removing it from the pruned V4 runtime costs about `+1.006` expansion/request on this paired subset.

### Why full V4 still fails preregistration

The preregistered T5 no-regression gate fails materially against V2:

- phase macro-F1: `0.8297 -> 0.5817` (`-0.2480`);
- resource macro-F1: `0.6646 -> 0.5791` (`-0.0855`);
- source macro-F1: `0.6301 -> 0.5942` (`-0.0359`);
- certificate exact match: `0.7675 -> 0.6322` (`-0.1353`).

Code/result audit explains this failure.  Across all V4-full CCE pruning violations in the fast run, `1991/1991` are `continuation_reachability`; **zero** final pruning proofs are produced by the intended typed numeric continuation relaxation.  Consequently the observed `35.8%` expansion reduction cannot be attributed to a capability-typed CCE.  Operationally it is backward *structural dead-end reachability* pruning.  The generic synthetic `capability_continuation_envelope / continuation_reachability` violation often wins certificate selection, replacing the concrete phase/resource/source failure that T5 is designed to diagnose.

**Promotion decision:** retain backward structural dead-end pruning as an implementation/search optimization, but do **not** promote V4's typed CCE or continuation priority as the paper's main novelty.  The next version must demonstrate capability-specific backward reasoning beyond graph reachability and must carry a concrete failure proof rather than a pseudo-resource certificate.

## V5 — Proof-Carrying Capability Viability Kernel (CVK) + Viability-Guided TSBS

**Status:** next algorithm candidate.  V5 is designed directly from the attribution-valid V4-fast failure.  The first fast round uses the same frozen CASA seed-13 checkpoint and requires no new neural training.

### V5 research question

V4 establishes that backward pruning can greatly reduce search work, but it does not establish that *typed passenger capability semantics* are responsible for the gain.  V5 therefore asks a stricter algorithmic question:

> Can the compiled passenger contract induce a backward capability-viability object that (i) soundly prunes infeasible forward prefixes, (ii) produces a concrete phase/resource/source witness when it prunes, and (iii) yields capability-specific pruning beyond ordinary structural dead-end reachability?

This is the next question that must be answered before a CCF-A paper can claim a typed continuation mechanism.

### V5 mechanism

1. **Validated hard authority remains unchanged.** Evidence-grounded typed transition evidence, the Passenger Capability Compiler, service automaton, conservative typed resource algebra, dynamic-availability gate, and final `Accept AND Safe AND Sat` decision remain authoritative.
2. **Exact structural viability kernel (new).** On the frozen candidate-transition graph, V5 computes exact backward reachability through lifecycle/static/interface/dynamic hard-valid transitions.  Structural dead ends can be pruned soundly.
3. **Concrete suffix witnesses (new).** For each structurally reachable `(anchor,phase)` state, V5 stores a bounded set of concrete simple-state suffix transition sequences to an accepting destination.  This preserves cross-resource and requirement-group coupling that V4's independently relaxed per-resource vector discarded.
4. **Fail-open completeness guard (new).** Typed pruning is permitted only when the concrete suffix set is complete within the configured path/depth guards.  If suffix enumeration hits a path-count or depth guard, the state is marked overflow and typed pruning is disabled there.  Approximation may therefore lose speed but cannot create a false hard rejection.
5. **Exact typed suffix replay (new).** Given the actual forward typed ledger, V5 replays every complete stored suffix with the **same** `_try_expand`, conservative margins, categorical/group semantics, and final `satisfy_all` used by forward TSBS.  A state is typed-pruned only if every executable suffix fails.  This is a path-coupled viability proof rather than V4's independent-resource relaxation.
6. **Proof-carrying structural diagnosis (new).** When structural reachability fails, the kernel propagates a concrete downstream transition-test witness (`interface`, `physical`, `availability`, etc.) backward instead of emitting the generic `continuation_reachability` pseudo-resource.
7. **Proof-carrying typed diagnosis (new).** When every typed suffix fails, the certificate candidate is the best real violation encountered during exact suffix replay (`ride_time_s`, `path_width_m`, grouped interface requirement, missing/low-confidence evidence, etc.), not `typed_viability` unless the explicit generic-certificate control is enabled.
8. **No new continuation-priority head.** V4 shows that continuation priority has negligible independent value.  V5 uses the exact kernel for pruning/proof only and retains the empirically useful V2 static learned typed-feasibility prior as a secondary queue-ordering signal.
9. **No V3 ranker and no global completion-value head.** They remain retired unless later evidence establishes a new learning target with independent value.

### Soundness/completeness boundary

Structural reachability is exact on the frozen hard-valid candidate graph.  Typed pruning is fail-open under bounded suffix materialization: a state whose suffix set is incomplete is never typed-pruned.  Cyclic wait/replan suffixes are not materialized because the CapPlan hard resource algebra is monotone: an extra cycle cannot reduce cumulative/probabilistic burden, improve a bottleneck affordance, or repair a categorical/interface predicate.  Thus any feasible cyclic suffix has a no-worse simple-state witness.

Returned-plan soundness remains identical to V2 because V5 never relaxes `_try_expand` or final `satisfy_all`.  As with previous versions, finite `max_expansions` means search-time completeness is an empirical/runtime property rather than an unconditional theorem.

### Learning target after V5

V5 deliberately does **not** retrain CASA.  A network should not relearn verifier-equivalent processed values such as the same slope/width/distance already present in authoritative `resource_evidence`, and it should not regain hard-feasibility authority.  If V5 establishes a useful exact capability-viability target, the next learned module should amortize one of two lower-level problems:

- raw/dynamic evidence -> calibrated uncertainty/guidance; or
- compiled capability state + raw service graph -> **viability proposal / ordering** supervised by the exact CVK while symbolic CVK/TSBS remains the hard verifier.

Whether a genuine HGT/R-GCN is justified is therefore deferred until V5 tells us that learning is actually the dominant bottleneck.

### V5 preregistered fast experiment

Use the same deterministic `256`-episode subset and the same frozen CASA checkpoint.  Required controls:

- `v5_full`: proof-carrying structural + typed viability pruning, V2 static learned ordering;
- `v5_no_kernel`: no backward viability kernel;
- `v5_structural_only`: structural reachability/proof only, typed suffix pruning disabled;
- `v2_reference`: exact V2 reference runtime;
- `v5_generic_certificate`: identical V5 search but replace concrete viability witnesses by generic pseudo-certificates;
- `v5_no_static_guidance`: remove the V2 learned static ordering signal;
- `v4_reference`: exact V4 full on the same subset.

Hard/semantic gate:

- `PCDecisionF1 >= 0.99`, `FAR=0`, `FRR=0`;
- T4 success-flip precision/recall do not regress versus V2;
- each of `DF_phase_macro_f1`, `DF_resource_macro_f1`, `DF_source_macro_f1`, and `DF_certificate_exact_match` may drop by **at most 0.01** versus V2 on the fast review.

Primary efficiency gate:

- paired `(V2 expansions - V5 expansions)` mean `> 0`;
- episode-cluster bootstrap 95% CI lower bound `> 0`;
- zero paired passenger-complete decision mismatches.

Capability-specific promotion gate:

- `CVK_typed_pruned_mean > 0`; and
- full V5 must beat `v5_structural_only` in paired expansions with a positive 95% CI lower bound.

Without this gate, backward reachability may remain a useful engineering optimization, but the experiment still does not support a paper claim that capability-typed backward viability is the source of the gain.

Proof-carrying diagnosis gate:

- `v5_generic_certificate` and full V5 must have identical hard decisions and identical expansion counts;
- concrete proof witnesses must improve at least one preregistered T5 macro/exact metric by >= `0.01` over the generic-certificate control while satisfying the V2 no-regression gate.

`run_v5_fast_experiments.sh` executes these controls and writes `v5_fast_gate.json` automatically so the GO/STOP decision is not selected after seeing the results.  Run `run_v5_full_experiments.sh` only if that file reports `status=GO`.

### Novelty boundary

V5 does **not** claim generic backward reachability, bidirectional resource-constrained search, or learned heuristics as novel.  Those are established planning/search techniques.  The intended paper-level contribution is a **passenger-complete, capability-compiled viability semantics** in which heterogeneous non-substitutable capability requirements induce both a forward consumed-resource state and a backward proof-carrying executable-continuation state, while learning remains a subordinate accelerator rather than the authority that defines passenger feasibility.
