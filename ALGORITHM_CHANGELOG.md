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

## V5-fast seed13 decision — typed viability PROMOTED; exact suffix replay and incomplete proof semantics STOP

The preregistered V5-fast review uses the same deterministic `256` test episodes / `2048` passenger requests and the same frozen CASA seed-13 checkpoint as the V2–V4 mechanism studies.  The run is attribution-valid for the V5 mechanism controls: every requested variant completed, V5/V2/structural/generic-certificate comparisons are request-paired, hard passenger-complete decisions are identical to the frozen verifier, and the prewritten `v5_fast_gate.json` reports the GO/STOP decision without post-hoc threshold changes.

### What V5 proves

Hard semantics remain exact:

- `OraclePCR=PCR=0.05078125` (`104/2048` passenger-complete requests);
- `PCDecisionPrecision=PCDecisionRecall=PCDecisionF1=1.0`;
- `PCFalseAcceptRate=PCFalseRejectRate=0`;
- capability success-flip precision/recall remain `1.0` on the fast subset.

The primary search gain is strong.  V5 reduces mean TSBS expansions from V2's `18.8794` to `4.9307` (`73.88%`), with paired `V2 - V5 = +13.9487` expansions/request and episode-cluster bootstrap 95% CI `[11.2182, 16.8731]`; paired passenger-complete decision mismatches are zero.

Most importantly, V5 passes the preregistered **capability-specific promotion gate** rather than merely reproducing V4 structural reachability:

- `CVK_typed_pruned_mean = 1.2471 > 0`;
- structural-only mean expansions are `12.1592`, versus `4.9307` for full V5;
- paired `structural-only - V5 = +7.2285` expansions/request, 95% CI `[5.1089, 9.5796]`.

Therefore path-coupled, passenger-specific typed backward viability is promoted from a hypothesis to a main algorithmic mechanism candidate.  This is the first version in which the backward gain is demonstrably more than generic graph dead-end detection.

The proof-carrying control also behaves causally.  Full V5 and `generic_viability_certificates` have identical hard decisions and exactly identical expansions (`4.9307`), while concrete witnesses substantially improve T5 relative to generic pseudo-certificates.  Proof carrying is therefore useful independently of search trajectory.

### Why V5 as a whole still STOPs

The preregistered overall gate requires every T5 macro/exact metric to remain within `0.01` of V2.  V5 does not satisfy this condition:

- phase macro-F1: `0.8297 -> 0.7518` (`-0.0779`);
- resource macro-F1: `0.6646 -> 0.5743` (`-0.0902`);
- source macro-F1: `0.6301 -> 0.5751` (`-0.0551`);
- certificate exact match: `0.7675 -> 0.7073` (`-0.0602`).

Thus `v5_fast_gate.json` correctly reports `status=STOP`, even though both the typed-viability promotion gate and the proof-carrying diagnosis gate pass.  The result must not be reclassified as GO after seeing the large expansion reduction.

The remaining T5 error is not mainly a certificate-selector bug.  Among `359` oracle-failure requests on which typed pruning fires, the V2 canonical `(phase,resource,source)` witness is still present in V5's collected violations for `213`; V5 selects the same witness for `210/213`.  The other `146` V2 witnesses are absent because typed pruning prevents forward TSBS from ever visiting the rejected hard branch that generated them.  These missing witnesses are overwhelmingly downstream `board/alight` failures of `interface` or `physical` transition tests.  V5 carries failures of executable suffixes, but not the rejected hard branches inside a still-structurally-reachable subtree.

A second bottleneck is computational rather than semantic.  V5's exact suffix replay checks `215.45` concrete suffix paths/request on average (p95 about `1280`; heavy tail above `3000`) and raises planner latency from V2's about `19.9 ms` to `128.6 ms` mean and `745.5 ms` p95.  Per-request suffix-check count is strongly correlated with latency.  Structural-only viability remains near `21 ms`, showing that repeated query-time `_try_expand` replay, not backward graph construction itself, is the dominant runtime cost.

**Promotion/retirement decision after V5-fast:**

- **PROMOTE:** passenger-complete semantics; capability-as-typed-feasibility; evidence-grounded hard authority; conservative margins; exact structural dead-end pruning; **typed path-coupled backward viability**; proof-carrying explanation as a required semantic property; V2 static learned feasibility as a small secondary ordering aid.
- **RETIRE as final form:** explicit query-time replay of hundreds of concrete suffix paths.
- **INCOMPLETE:** V5 proof object, because it does not conditionally carry rejected hard branches that are reachable from the current typed ledger.
- **REMAIN RETIRED:** V3 ECF ranker, V4 continuation priority, global completion-value head, neural overwrite of authoritative typed evidence.

## V6 — Executable Capability Precondition Kernel (ECPK)

**Status:** next algorithm candidate.  V6 does not change the V5 mechanism being tested; it compiles it into a compact, proof-complete backward semantic object.  No new neural training is required for the first V6 experiment.

### Tightened paper object

The paper's method is now organized around one semantic object rather than a chain of loosely coupled modules:

`Passenger-complete contract -> executable typed transition semantics -> forward consumed-capability state × backward executable-capability precondition kernel -> TSBS -> accepted complete-trip plan OR concrete rejection proof`.

For a service state `s`, a complete hard-valid suffix `pi` induces a monotone typed transformer `phi_pi` over the forward ledger.  Let `A_acc(s)` be the nondominated antichain of these complete-suffix transformers.  Passenger-specific viability is

`V_Psi(s,R) = exists phi in A_acc(s) : Sat(phi(R), Psi) = 1`.

This is a weakest-precondition-style object specialized to CapPlan's phase-scoped, heterogeneous resource algebra.  It is not a scalar RCSP remaining-budget bound: cumulative, upper-bottleneck, lower-affordance, probabilistic, categorical/interface, missing-evidence, uncertainty, and requirement-group semantics remain non-substitutable and are evaluated by the same compiled contract used by forward TSBS.

### V6 mechanism

1. **Exact V5 suffix universe retained as the semantic reference.**  Structural reachability and the bounded-complete simple suffix universe remain exactly V5's.  Overflow remains fail-open: no typed pruning is permitted from an incomplete suffix universe.
2. **Executable Capability Precondition Antichain (new representation).**  Every complete V5 suffix is composed once into a typed suffix-effect transformer.  Effects are combined with the same associative resource algebra and conservative evidence used by forward TSBS.  Exact duplicate and conservatively dominated suffix transformers are removed.  Query-time viability therefore checks compact summaries rather than replaying every transition of every suffix.
3. **Exact-representation control.**  `v5_reference_runtime` runs the original path-by-path V5 query inside the V6 codebase.  V6 is preregistered to have zero decision mismatches and exactly equal TSBS expansion counts to this control.  A speedup is not accepted if the antichain silently changes the V5 pruning set.
4. **Conditional rejection-proof precondition antichain (new).**  V5 analysis shows that the missing T5 witness is often a hard-invalid `interface/physical` branch inside a subtree pruned earlier by typed viability.  V6 therefore compiles hard-valid prefixes leading to rejected hard branches into a second proof antichain.  A downstream witness may participate in certificate selection **only if its prefix precondition is satisfied by the current forward typed ledger**.  This conditionality prevents a proof from being propagated through a typed-infeasible prefix and creating an oracle-unreachable failure explanation.
5. **Same-object diagnosis.**  On typed or structural pruning, V6 selects between the typed viability failure and all conditionally reachable rejected-branch proofs using the same certificate ordering as TSBS/oracle.  Search and explanation are therefore two queries over the same capability precondition kernel rather than unrelated post-hoc modules.
6. **Learning remains subordinate.**  The validated V2 static learned feasibility prior remains only as queue ordering.  V6 does not train a network to approximate verifier-equivalent slope/width/distance values and does not give learning hard-feasibility authority.

### Why this is the next paper-level mechanism

V5 establishes the causal value of capability-specific backward viability, so V6 is not complexity added for novelty.  It addresses the two measured V5 bottlenecks directly:

- **scalability:** replace `O(number of stored suffix paths × suffix length)` query replay by antichain summary checks;
- **diagnostic equivalence:** replace prefix-independent downstream witness propagation with typed-reachability-conditioned proof preconditions.

Generic bidirectional A*, Pareto dominance, antichains, and weakest-precondition reasoning are established techniques and are **not** claimed as individually novel.  The intended contribution is the *passenger-complete capability compilation* that turns one complete-trip contract into a dual forward/backward executable semantics over phase-scoped, non-substitutable resources, with an accepting precondition antichain and a proof-carrying rejection antichain in the same planner object.

### V6-fast preregistration

Use the same `256`-episode deterministic subset and frozen CASA seed-13 checkpoint.  Controls:

- `v6_full`: ECPK accepting antichain + conditional proof antichain;
- `v5_reference_runtime`: exact V5 concrete suffix replay in the V6 codebase;
- `v2_reference_runtime`: validated V2 baseline;
- `no_typed_viability`: structural-only backward pruning;
- `no_viability_kernel`: no backward viability;
- `no_viability_proof_envelope`: identical V6 search/precondition antichain but no conditional rejected-branch proof;
- `no_learned_feasibility_guidance`: remove the small V2 static learned ordering signal.

Hard/semantic gate remains unchanged: `PCDecisionF1>=0.99`, FAR/FRR `=0`, no T4 success-flip regression, and every T5 phase/resource/source macro-F1 plus exact certificate match within `0.01` of V2.

Primary mechanism gate: V6 beats V2 in paired expansions with episode-cluster bootstrap 95% CI lower bound `>0`, with zero hard-decision mismatches.  Typed viability must still fire and beat structural-only with positive paired CI.

**Exact-representation gate:** V6 and exact V5 reference must have identical decisions and exactly equal TSBS expansions.  `ECPK antichain size <= raw suffix count`, and V6 summary checks must be lower than V5 concrete path checks.

**Scalability gate:** V6 must beat V5 mean planner latency with a positive episode-cluster bootstrap latency CI and must bring mean latency to no more than `2×` V2 on the fast subset.  This explicitly prevents the publication algorithm from trading an expansion win for an order-of-magnitude implementation slowdown.

**Proof-completeness gate:** disabling the conditional proof antichain must leave decisions and expansions unchanged; full V6 must improve at least one preregistered T5 metric by `>=0.01`, while the full semantic gate requires all T5 metrics to recover to within `0.01` of V2.

`run_v6_fast_experiments.sh` writes `v6_fast_gate.json` automatically.  Run `run_v6_full_experiments.sh` only when this file reports `status=GO`.

## V6-fast seed13 decision — representation-faithful but STOP; eager compilation and diagnostic dominance are the new bottlenecks

**Date:** 2026-09-03  
**Decision:** `STOP` under the V6 preregistered gate.  The fast run is attribution-valid for mechanism selection, but V6 is not promoted as the final ECPK implementation.

The V6-fast run uses the same deterministic `256` test episodes / `2048` passenger requests and frozen seed-13 CASA checkpoint as the V2–V5 mechanism studies.  All seven requested variants completed on the same request universe.  `algorithm_attribution_ready=true` with no attribution warnings, and request-level pairing is complete.  V6 and the exact V5-reference runtime have **zero passenger-decision mismatches and exactly equal TSBS expansion counts for all 2048 requests**, so the central question “does the compressed query reproduce the V5 typed-viability pruning set?” is answered cleanly.

### What V6 passes

Hard passenger semantics remain exact:

- `OraclePCR=PCR=0.05078125` (`104/2048`);
- `PCDecisionPrecision=PCDecisionRecall=PCDecisionF1=1.0`;
- `PCFalseAcceptRate=PCFalseRejectRate=0`;
- capability success-flip precision/recall remain `1.0` on the fast subset.

The already-promoted typed backward mechanism remains strong:

- V2 mean expansions: `18.8794`;
- V6 mean expansions: `4.9307`;
- paired `V2 - V6 = +13.9487` expansions/request, episode-cluster 95% CI `[11.2182, 16.8731]`;
- structural-only mean expansions: `12.1592`;
- paired `structural-only - V6 = +7.2285`, 95% CI `[5.1089, 9.5796]`;
- `CVK_typed_pruned_mean = 1.2471`.

Therefore V6 does **not** overturn the V5 promotion: passenger-specific, path-coupled typed backward viability remains a main mechanism.

The V6 accepting antichain also performs real *query compression*:

- raw complete suffixes materialized per request: `941.1484` mean;
- accepting antichain size: `63.3877` mean;
- V5 concrete path checks: `213.3643` mean;
- V6 query-time summary checks: `25.9990` mean;
- V6 concrete path replay checks: `0`.

The conditional proof envelope has an independent causal effect.  Full V6 and `no_viability_proof_envelope` have identical hard decisions and identical expansions, while the proof envelope improves T5 by `+0.0285/+0.0313/+0.0132/+0.0314` for phase/resource/source macro-F1 and exact match respectively.  Proof-carrying diagnosis remains a promoted semantic requirement.

The V2 static learned-feasibility ordering signal remains small but measurable: disabling it increases expansions from `4.9307` to `5.1968`; paired saving is `0.2661` expansion/request with 95% CI `[0.0688, 0.4942]`.  It remains a **secondary ordering mechanism**, not a paper-defining contribution and never a hard-feasibility authority.

### Why V6 STOPs

V6 fails two preregistered hard gates.

**1. T5 diagnostic equivalence still fails.**  Relative to V2:

- phase macro-F1: `0.82969 -> 0.56738` (`-0.26232`);
- resource macro-F1: `0.66456 -> 0.50911` (`-0.15545`);
- source macro-F1: `0.63013 -> 0.44926` (`-0.18088`);
- certificate exact match: `0.76749 -> 0.71759` (`-0.04990`).

The failure is not adequately described as “the proof envelope needs more examples.”  V6 exposes a more fundamental semantic asymmetry.  The accepting antichain is designed for an **existential** query: preserve at least one resource-wise best suffix such that `exists suffix: Sat(...)`.  Diagnostic rejection is a different query: when every completion fails, preserve a concrete and executable witness explaining *which unavoidable/downstream condition fails*.  An effect that is dominated for acceptance can still carry the canonical rejection witness.  Therefore **feasibility-preserving dominance and diagnosis-preserving dominance are not the same partial order**.  Reusing one aggressive “best suffix” antichain as the basis for both queries can preserve viability perfectly while erasing failure diversity.  On this fast subset, V6 even reduces the `wait` failure phase F1 to `0`, despite nonzero oracle support.

**2. V6 is not computationally compact despite query compression.**

- V2 mean planner latency: `22.33 ms`;
- V5 reference: `146.24 ms`;
- V6: `617.38 ms` (p95 `1496.65 ms`);
- paired `V5 - V6` latency delta: `-471.14 ms`, episode-cluster 95% CI `[-529.69, -415.73]`.

The reason is architectural: V6 is still **enumerate-then-compress**.  It first materializes the same bounded concrete suffix universe used by V5, compiles roughly `941` complete suffixes/request, and separately enumerates/compiles roughly `305` proof prefixes/request, before the antichain can reduce them to `63` accepting summaries and `141` proof summaries.  The work moved from query-time replay to eager request-time compilation rather than disappearing.  Consequently the preregistered scalability gate (`V6 < V5 latency`, positive latency CI, and `V6 <= 2x V2`) correctly fails.

Absolute fast-run latency has a system-contention caveat because a second worker runs diagnosis controls concurrently, so publication wall-clock claims require a serial calibration.  This does **not** rescue V6: the slowdown over V5 is hundreds of milliseconds/request and the paired clustered CI is entirely on the wrong side of zero.  V7 latency-critical controls are therefore deliberately scheduled without a competing GPU worker.

### Promotion / retirement after V6-fast

- **PROMOTE / core:** Passenger-Complete terminal semantics.
- **PROMOTE / core:** Capability-as-Typed-Feasibility with phase-scoped non-substitutable resource algebra.
- **PROMOTE:** evidence-grounded hard authority; neural predictions cannot overwrite authoritative typed evidence.
- **PROMOTE:** conservative typed safety semantics.
- **PROMOTE:** lifecycle/service automaton as executable service semantics (publication-level isolated stress test still required if the candidate graph structurally encodes lifecycle).
- **PROMOTE:** structural backward viability as a search optimization.
- **PROMOTE / main mechanism:** passenger-specific path-coupled typed backward viability.
- **PROMOTE / semantic requirement:** proof-carrying diagnostic rejection.
- **RETAIN secondary:** V2 static learned feasibility guidance (`~0.266` expansion/request saving on V6-fast).
- **RETAIN as reference only:** V5 exact suffix replay.
- **RETIRE as final implementation:** V6 enumerate-then-compress ECPK construction.
- **RETIRE as a shared compression rule:** one acceptance-style dominance relation for both completion and diagnosis.
- **REMAIN RETIRED:** V3 ECF pairwise ranker; V4 continuation priority; global completion-value head; neural hard-evidence overwrite.

### Dominant bottleneck after V6

The dominant bottleneck is no longer CASA prediction and no longer whether typed backward viability is useful.  It is now the **representation and compilation of backward executable semantics**:

1. **computational:** build capability preconditions directly, without first enumerating the raw suffix/proof universe;
2. **semantic:** distinguish an existential acceptance frontier from a certificate-preserving rejection frontier;
3. **proof:** preserve downstream failure witnesses only when the current typed ledger can execute the prefix that reaches them;
4. **boundedness:** any frontier/depth cap must fail open so an incomplete compiler can only lose pruning, never create a false reject.

This becomes the only algorithmic target for the next fast iteration.  A larger HGT/raw-evidence model is deliberately postponed: on the frozen benchmark hard passenger decisions are already exact, whereas the measured V6 failure lies in executable-precondition representation.  The current relation-aware CASA surrogate remains a paper/implementation gap for later raw-evidence generalization, not the immediate V7 bottleneck.

## V7 — Asymmetric Direct Capability Precondition Kernel (A-DCPK)

**Status:** implemented next candidate; correctness-first direct compiler.  No retraining is required for the first V7 experiment.

### Core design change

V7 replaces V6's `enumerate concrete paths -> compile -> antichain` pipeline by **direct backward fixed-point propagation on the hard-valid lifecycle graph**.  Partial precondition summaries are propagated edge-by-edge and dominance-pruned immediately; the full raw complete-suffix universe and full raw proof-prefix universe are never materialized by the V7 path.

V7 explicitly separates three backward frontiers:

1. **Acceptance frontier `A_acc(s)` — best-effect / existential order.**  This frontier answers: “does there exist an executable passenger-complete continuation?”  It may discard a suffix when another suffix is no worse in every typed resource under the same compiled semantics.  It is allowed to be aggressive because the query is existential.
2. **Typed-rejection frontier `A_rej(s)` — certificate-preserving reverse order.**  This frontier answers: “if completion is impossible, which typed resource failure remains executable and diagnostic?”  Rejection summaries use the **opposite resource preference direction** and are compressed only under a compatible witness/precondition signature.  A suffix that is useless for acceptance can therefore remain necessary for explanation.
3. **Hard-proof frontier `A_proof(s)` — easiest executable prefix to a concrete rejected transition.**  Interface/physical/topology/availability rejection witnesses are propagated backward together with the precondition needed to reach the rejecting transition.  A witness is eligible only when the current forward ledger satisfies that prefix.  It cannot cross a typed-infeasible prefix.

This is the main V7 conceptual result: **acceptance dominance and rejection-proof dominance are intentionally asymmetric**.  Antichains remain an implementation tool; the paper-level object is the passenger capability program compiled into dual executable preconditions whose order depends on the query being answered.

### Soundness / boundedness contract

- Hard returned-plan acceptance is unchanged and remains evidence-grounded TSBS acceptance.
- V7 typed pruning is enabled only when direct acceptance compilation for the relevant state is complete.
- A depth/frontier cap overflow marks the state/compiler incomplete and **fails open** to normal forward TSBS; bounded approximation may reduce acceleration but must not create a false reject.
- Rejection frontiers never alter allow/accept decisions or expansion order.  They affect only which concrete failure witness is available after a prune/rejection.
- `v5_reference_runtime` and `v6_reference_runtime` remain available as frozen mechanism/reference controls.

### Why V7 is more consistent with the tightened CCF-A story

The intended final method is no longer “CASA-Net plus a constrained search.”  It is:

`Passenger-Complete Planning -> Compiled Capability Program -> Forward consumed capability × asymmetric backward executable preconditions -> Proof-Carrying Typed Safe-Budget Search -> accepting complete-trip execution OR concrete executable rejection proof`.

The novelty claim must **not** be “a new antichain algorithm,” “first bidirectional resource search,” or “first weakest-precondition planner.”  Those generic ideas are established.  The candidate contribution is the passenger-complete compilation that makes lifecycle-indexed, heterogeneous typed capability semantics simultaneously define forward state, backward acceptance viability, backward rejection reachability, pruning soundness, and failure proof.

### V7-fast preregistration

Use the same deterministic `256` test episodes and frozen seed-13 CASA checkpoint.  No retraining and no real nuPlan closed loop in this selection run.

Latency-critical controls are intentionally run **serially** before the diagnosis controls, so the V7/V6/V5/V2 wall-clock comparison is not confounded by a competing second worker.

Main controls:

- `v7_full`: direct asymmetric acceptance + typed-rejection + hard-proof frontiers;
- `v6_reference_runtime`: frozen V6 enumerate-then-compress runtime;
- `v5_reference_runtime`: frozen V5 exact suffix replay;
- `v2_reference_runtime`: validated V2 baseline;
- `no_typed_viability`: structural-only backward viability;
- `no_viability_kernel`: no backward kernel;
- `no_rejection_kernel`: identical V7 pruning/expansions with the **typed-rejection antichain query** disabled while the hard-proof prefix envelope remains active, isolating the new `A_rej` contribution;
- `no_learned_feasibility_guidance`: tests whether the small V2 static ordering signal is still useful.

Preregistered GO conditions:

1. **Hard semantics:** `PCDecisionF1>=0.99`, FAR=FRR=`0`, no T4 success-flip regression.
2. **T5:** phase/resource/source macro-F1 and certificate exact match must each be `>= V2 - 0.01`.
3. **Primary search:** V7 must beat V2 in paired expansions with episode-cluster 95% CI lower bound `>0` and zero decision mismatches.
4. **Typed-specific:** V7 must beat structural-only in paired expansions with CI lower bound `>0` and typed pruning must fire.
5. **V5 mechanism preservation:** V7 must have zero decision mismatches and exactly equal expansions to the exact V5 reference.  If this fails, the direct compiler changed the promoted mechanism rather than merely representing it better.
6. **Direct representation:** V7 must report zero raw suffix/proof enumeration, an active direct compiler, and less direct candidate-composition work than V6's raw suffix+proof universe.  A smaller final frontier without lower construction work is not accepted as scalability progress.
7. **Runtime:** V7 must beat **both V6 and V5** mean latency with positive clustered latency CIs and must be `<=2x` V2 mean latency on the fast subset.
8. **Asymmetric diagnosis:** `no_rejection_kernel` must have identical hard decisions and expansions.  Full V7 must not reduce any preregistered T5 metric and must produce a material diagnosis gain (`>=0.02` on at least one preregistered macro/exact metric) while rejection/proof frontiers demonstrably fire.

Run `run_v7_full_experiments.sh` only when `v7_fast_gate.json` reports `status=GO`.

### V7 implementation note

This first direct compiler is deliberately correctness-first.  Candidate propagation is direct and raw universes are removed, but a candidate summary may still rescan a short stored transition prefix while composing its typed effect.  If V7 preserves semantics/T5 but narrowly misses the `<=2x V2` runtime gate, the next change should be **incremental associative edge-transformer composition and episode-level precondition caching**, not a new semantic algorithm and not a neural backbone change.

## V8 — Incremental Acceptance / Lazy Proof Kernel (IALP-K)

**Status:** implemented next candidate after the V7-fast audit; fast experiment not yet run on the server. No CASA retraining is required for the first V8 selection experiment.

### Why V7 is STOP

The uploaded `v7_fast_seed13` package contains a complete latency-critical GPU0 group (256 episodes / 2048 requests) but the GPU1 `no_rejection_kernel` control stops at roughly 86%, so the generated package has no `v7_fast_gate.json`.  The completed controls are nevertheless sufficient to make GO impossible under the preregistered V7 gate:

- hard passenger semantics remain exact (`PCDecisionF1=1`, FAR=FRR=0; 104 TP / 0 FP / 0 FN);
- V7 reduces mean expansions from V2 `18.879 -> 3.956` (paired saving `14.923`, episode-cluster CI `[12.038,18.004]`);
- V7 also beats structural-only `12.159 -> 3.956`, so typed backward viability remains a promoted mechanism;
- however all four T5 metrics are below `V2-0.01`;
- V7 changes the promoted V5 search behavior (`4.931 -> 3.956` expansions), violating the representation-equivalence preregistration;
- latency is catastrophic: V2 `22.4 ms`, V5 `145.6 ms`, V6 `602.3 ms`, V7 `18,347.7 ms/request` (p95 `109.3 s`), so every preregistered scalability gate fails.

The latency diagnosis is structural rather than noise: request-level V7 latency correlates strongly with direct build candidates (`rho≈0.953`) and rejection-frontier size (`rho≈0.904`).  The compiler produces on average `461` direct candidates and a `370`-element rejection antichain per request.  Code inspection identifies two causes:

1. every backward candidate calls `_build_suffix_summary()` over its full stored transition path, so a "direct" candidate is repeatedly recompiled from the first edge;
2. the reverse rejection antichain requires an extremely specific witness/precondition signature and therefore compresses poorly.

The diagnostic assumption itself is also retired.  A resource-wise reverse order over a complete suffix is not a proof that the reported downstream failure is executable: a suffix may already violate an earlier passenger-specific condition.  In the complete GPU0 results V7 and V2 disagree on the `(phase,resource,source)` certificate signature for 180 oracle-failure requests, with common shifts from wait/board/alight failures to deeper egress failures.

### Promotion / retirement after V7-fast

- **PROMOTE / core:** Passenger-Complete terminal semantics.
- **PROMOTE / core:** Capability-as-Typed-Feasibility and the phase-scoped non-substitutable typed resource algebra.
- **PROMOTE:** evidence-grounded hard authority and conservative typed evidence semantics.
- **PROMOTE / main planning mechanism:** passenger-specific typed backward viability; V5/V6/V7 all show large independent expansion savings over structural/no-kernel controls.
- **PROMOTE as an algorithmic principle:** direct backward compilation, but **not** the V7 path-recompilation implementation.
- **RETAIN secondary:** V2 static learned-feasibility ordering; it never owns hard feasibility.
- **RETIRE:** V7 global reverse `A_rej` antichain. Diagnosis is not obtained by simply reversing the acceptance resource order.
- **RETIRE:** V7 full-path recompilation during backward candidate propagation.
- **RETAIN as references only:** V5 exact suffix replay, V6 enumerate-then-compress, and V7 direct-dual implementation.
- **REMAIN RETIRED:** V3 ECF pairwise ranker, V4 continuation priority, global completion-value head, and neural hard-evidence overwrite.

### Core V8 design

V8 separates the existential acceptance query from diagnostic proof generation instead of trying to encode both with one eagerly materialized frontier.

#### 1. Incremental accepting precondition kernel

For each hard-valid edge `e`, compile a local monotone typed transformer `T_e` exactly once.  The accepting frontier is computed directly by

`A_acc(d)={Id}`

`A_acc(s)=ND_acc( Union_{e:s->s'} T_e o A_acc(s') )`.

Composition uses the same associative cumulative / upper-bottleneck / lower-affordance / probabilistic / categorical algebra as forward TSBS.  A candidate therefore costs one edge-summary composition rather than a full re-scan of its stored suffix path.  No raw suffix/proof universe and no rejection antichain are materialized.

Frontier/depth bounds remain **fail-open**.  An incomplete backward state disables typed pruning at that state and all affected ancestors; approximation may lose acceleration but must not create a false passenger reject.

#### 2. Lazy exact diagnostic replay

V7 showed that explanation fidelity should not be purchased by eagerly compiling a huge reverse rejection universe for every request.  V8 therefore produces a concrete certificate only when the primary accepting search actually fails.

The first implementation invokes an exact no-kernel forward replay using the same `_try_expand`, typed ledger, conservative evidence and certificate selector.  Its result may replace only the failure certificate.  If replay unexpectedly finds a passenger-complete plan, V8 explicitly **fails open to the rescued plan**, exposing the event in diagnostics rather than returning a false reject.

This is intentionally a correctness-first implementation of proof-on-demand.  If V8-fast passes semantics/T5/runtime, a later optimization may restrict replay to recorded kernel cut obligations; it must remain certificate-equivalent to the exact replay.

### V8 theory targets

The paper-facing claims to validate are:

1. **Returned-plan soundness:** any plan returned by V8 satisfies the same evidence-grounded `Accept ∧ Safe ∧ Sat` predicate as V2+.
2. **Fail-open pruning soundness:** an incomplete incremental frontier cannot prune; bounded compilation can only reduce acceleration.
3. **Associative compilation:** for monotone registered resource algebras, incremental composition of edge transformers is equivalent to composing the same transition sequence from scratch.
4. **Lazy-diagnosis equivalence:** when exact replay is complete and uses the same transition set/typed semantics, the returned failure certificate is identical to the no-kernel forward diagnostic result.
5. **Learning non-authority:** CASA static guidance may change ordering only; it cannot modify hard evidence, the accepting frontier, or the final verifier predicate.

### V8-fast preregistration

Use the same deterministic 256 test episodes and frozen seed-13 CASA checkpoint.  All latency-critical controls run serially.

Main controls:

- `full`: incremental acceptance + lazy exact diagnostic replay;
- `v2_reference_runtime`;
- `v5_reference_runtime`;
- `no_typed_viability`: structural-only backward pruning;
- `no_viability_kernel`;
- `no_lazy_diagnostic_replay`: identical primary search, diagnosis ablated;
- `no_learned_feasibility_guidance`.

GO requires all of:

1. `PCDecisionF1>=0.99`, FAR=FRR=0 and zero decision mismatch vs V2;
2. every preregistered T5 macro/exact metric `>= V2-0.01`;
3. paired expansion saving vs V2 and vs structural-only with episode-cluster CI lower bound `>0`;
4. typed pruning fires;
5. zero raw suffixes, zero raw proofs, zero eager rejection-antichain elements, active incremental direct compiler, and zero incomplete states on the fast subset;
6. mean latency beats V5 with positive paired clustered latency CI and is `<=2x` V2 mean latency;
7. `full` and `no_lazy_diagnostic_replay` have identical hard decisions and identical primary expansions; lazy replay does not reduce any T5 metric and improves at least one by `>=0.02`;
8. diagnostic replay is actually exercised on failures and `DiagnosticReplayRescueRate=0` on the selection subset (a nonzero rescue is a soundness warning requiring investigation, not a hidden success).

Only a V8-fast `status=GO` permits the 997-episode confirmatory run.  Real method-specific nuPlan closed loop remains postponed until the passenger/service algorithm is frozen.

## V8-fast seed13 decision — semantic/search GO, runtime STOP

**Date:** 2026-09-05  
**Decision:** `STOP` under the preregistered V8 gate.  The run is attribution-valid for the 256-episode / 2048-request passenger-service mechanism study; the failure is confined to the preregistered runtime gate, not to hard semantics, typed backward viability, or proof-on-demand diagnosis.

### Reliability / preregistration outcome

All V8 fast variants complete on the same deterministic request universe and report `algorithm_attribution_ready=true` with no attribution warnings.  Full V8 has `PCDecisionF1=1`, FAR=FRR=`0`, zero decision mismatch against V2, and exactly reproduces V2's T5 phase/resource/source macro-F1 and certificate exact-match (`0.82969/0.66456/0.63013/0.76749`).  Lazy exact replay is exercised on failures (`DiagnosticReplayRate=0.3413`) and never rescues a plan (`DiagnosticReplayRescueRate=0`).

The promoted typed backward mechanism remains very strong:

- V2 mean expansions `18.8794 -> V8 3.9561`, a `79.05%` reduction; paired episode-cluster 95% CI for `V2-V8` is `[12.038,18.004]` expansions/request;
- structural-only `12.1592 -> 3.9561`, a `67.46%` reduction;
- V5 exact typed-suffix reference `4.9307 -> 3.9561`, showing that incremental acceptance does not merely reproduce the older representation but finds the same hard decisions with a stronger compiled viability query;
- `CVK_typed_pruned_mean=0.6543`.

The V8 representation and diagnosis targets pass: no raw suffix/proof universe, no eager rejection antichain, active incremental compiler, zero incomplete states on the fast subset, and full/no-lazy have identical primary decisions/expansions.  Lazy replay improves T5 by `+0.2916/+0.1896/+0.1987/+0.0823` over the no-lazy control while adding only a small aggregate runtime difference; diagnosis is therefore retained.

### Why V8 STOPs

Only the runtime gate fails, decisively:

- V2 mean latency: `22.76 ms/request`;
- V5 reference: `139.68 ms/request`;
- V8: `644.42 ms/request`, p95 `3145.54 ms`;
- paired `V5-V8` latency delta `-504.74 ms`, 95% CI `[-731.34,-316.996]`;
- paired `V2-V8` latency delta `-621.66 ms`, 95% CI `[-864.46,-416.84]`.

Request-level profiling localizes the cost to **acceptance-kernel construction/frontier maintenance**, not TSBS expansion or lazy diagnosis.  Latency is strongly associated with the V8 accepting-frontier work (`Spearman rho≈0.876` for antichain size/build candidates; Pearson `r≈0.910` for build candidates).  Median build candidates are only `56`, but p95 is about `2346` and p99 about `3991`; antichain p95 is about `450`.  Search expansions have much weaker latency association.

Code inspection exposes the representation cause: `_build_suffix_summary()` currently carries every available evidence resource into an acceptance summary even when that resource appears in **no hard passenger capability clause/group**.  `_summary_dominates()` then compares the union of all these effects.  Passenger-irrelevant evidence dimensions can therefore prevent two acceptance-equivalent suffixes from dominating one another and inflate the Pareto frontier.  V8 also rebuilds exact signatures and repeatedly sorts/scans the entire frontier on candidate insertion.

The same-scene counterfactual benchmark amplifies the waste: each retained scene has eight passenger contracts over the same service graph/OD/vehicle.  In the V8 fast trace, all eight requests have exactly the same direct build-candidate and antichain counts for roughly `84.8%` of episodes, showing that much of the expensive graph-side compilation is repeatedly rediscovered.  This is a secondary amortization opportunity, but scene caching alone is not promoted as a paper contribution.

### Promotion / retirement after V8-fast

- **PROMOTE / core:** Passenger-Complete terminal semantics.
- **PROMOTE / core:** Capability-as-Typed-Feasibility with phase-scoped, non-substitutable typed algebra.
- **PROMOTE:** evidence-grounded hard authority and conservative typed margins.
- **PROMOTE / main mechanism:** passenger-specific typed backward executable viability.
- **PROMOTE / main representation principle:** incremental edge-transformer composition; V8 removes V7's full-path recompilation and eager reverse rejection universe.
- **PROMOTE / semantic requirement:** lazy exact proof-on-demand diagnosis; it restores V2 certificate fidelity without changing primary search.
- **RETAIN secondary:** V2 static learned-feasibility ordering.  On V8-fast it saves only about `0.266` expansion/request and is not a paper-defining mechanism; its wall-clock value must be rechecked after the symbolic kernel is fast.
- **RETIRE as final representation:** full-registry acceptance frontier.  Acceptance should distinguish suffixes only along resources observable by the hard passenger capability program.
- **REMAIN RETIRED:** V7 reverse rejection antichain, V6 enumerate-then-compress, V5 query-time suffix replay as final implementation, V4 continuation priority, V3 ECF ranker, completion-value head, and neural overwrite of hard typed evidence.

## V9 — Capability-Projected Incremental Precondition Kernel (CP-IPK)

**Status:** implemented next candidate; no retraining is required for the first V9 experiment.

### Core design

V9 treats the compiled hard passenger capability program as an **observation map over resource space**.  Let

`Supp(Psi) = {r | r occurs in a hard clause or in a hard requirement group}`.

For an accepting suffix transformer `phi`, define `Pi_Psi(phi)` by deleting resource effects outside `Supp(Psi)` while retaining the same fail-closed observation requirements, active clause/group semantics, and typed effects for every observable resource.  The V9 accepting recurrence is

`A_Psi(destination) = {Id}`

`A_Psi(s) = ND_Psi ( Union_{e:s->s'} Pi_Psi( T_e o phi ), phi in A_Psi(s') )`.

The key claim is **capability-projection invariance**: if two suffix transformers agree on every resource observable by the hard compiled capability program (and on its missing-observation preconditions), then `Sat(·,Psi)` cannot distinguish them for any forward ledger.  Keeping both suffixes is therefore representational redundancy, not extra planning power.  This is the paper-facing V9 mechanism; it is specific to capability-compiled passenger-complete planning rather than a generic “faster Pareto set” trick.

V9 retains V8's incremental edge-local typed composition and lazy exact diagnostic replay.  It additionally uses exact-signature indexing and defers deterministic frontier sorting until fixed-point completion.  Those two changes are engineering specializations and have separate controls; they are not claimed as novelty.

### New instrumentation

V9 reports capability-support size, number of projected-away evidence values, exact-signature hits, dominance comparisons, peak frontier size, and precondition-build time.  The purpose is to distinguish a genuine semantic-quotient reduction from a low-level timing fluctuation.

### V9-fast controls

- `full`: capability projection + indexed incremental accepting frontier + V8 lazy exact diagnosis;
- `v8_reference_runtime`: exact frozen V8 full representation;
- `v2_reference_runtime` and `v5_reference_runtime`;
- `no_typed_viability` and `no_viability_kernel`;
- `no_capability_projection`: V9 compiler/indexing in the full evidence-resource space;
- `no_frontier_signature_index`: capability projection retained, indexing ablated;
- `no_lazy_diagnostic_replay`;
- `no_learned_feasibility_guidance`.

### V9-fast preregistration

GO requires all of:

1. `PCDecisionF1>=0.99`, FAR=FRR=`0`, zero passenger-decision mismatch vs exact V8;
2. **zero request-level TSBS expansion mismatch vs V8**.  V9 is intended to change the representation, not the viability predicate;
3. all four T5 macro/exact metrics `>= V2-0.01`;
4. typed V9 beats structural-only in paired expansions with episode-cluster CI lower bound `>0`, and typed pruning fires;
5. capability projection is actually active: projected-away evidence count `>0`, projected antichain size and direct build-candidate count are both lower than `no_capability_projection`, no raw suffix/rejection universe is materialized, and fast states remain complete;
6. V9 mean latency beats exact V8 **and V5** with positive episode-clustered latency CI lower bounds and satisfies the original `<=2x V2` mean-latency publication gate;
7. full/no-lazy have identical hard decisions and primary expansions, lazy replay improves at least one preregistered T5 metric by `>=0.02`, replay fires, and rescue rate remains `0`;
8. signature indexing is retained only if its paired mean latency is non-worse than the projection-only control.  If not, drop the index and keep capability projection; this engineering choice does not invalidate the semantic V9 mechanism.

Only `v9_fast_gate.json: status=GO` permits the 997-episode V9 confirmatory run.  A genuine heterogeneous learned evidence encoder and method-specific nuPlan closed loop remain separate later gates; neither is allowed to mask a failure of the symbolic executable-precondition backbone.

## V9-fast seed13 decision — projection GO, exact-backbone runtime still STOP

**Date:** 2026-09-05  
**Decision:** `STOP` under the preregistered V9-fast gate, with the capability-projection mechanism **PROMOTED**.  The only failing gate is the unchanged `mean latency <= 2x V2` runtime condition; hard passenger semantics, V8-equivalent search behavior, typed-specific pruning, projection compression, and lazy exact diagnosis all pass.

### Reliable V9-fast evidence

The deterministic fast suite contains `256` episodes / `2048` passenger requests for every control, with complete request-level pairing and `algorithm_attribution_ready=true` without warnings. Full V9 preserves the frozen oracle decision exactly (`PCDecisionF1=1`, FAR=FRR=`0`) and has zero decision/TSBS-expansion mismatch against exact V8. It also reproduces the V2 T5 reference (`phase/resource/source macro-F1 = 0.82969/0.66456/0.63013`, certificate exact `0.76749`).

The promoted typed-search mechanism is unchanged: V2 `18.879 -> V9 3.956` mean expansions (`79.05%` reduction), structural-only `12.159 -> 3.956`, and typed pruning fires.  Therefore the semantic spine is no longer under redesign pressure.

### Capability projection is causally validated

Against `no_capability_projection`, primary passenger decisions and expansions are identical while:

- accepting antichain mean `115.43 -> 54.74`;
- direct build candidates mean about `483.46 -> 239.07`;
- mean planner latency `191.30 -> 72.13 ms/request`;
- paired latency saving is about `119.18 ms/request` with a positive episode-clustered 95% CI.

The exact-signature index also has an independent paired runtime contribution: `no_frontier_signature_index` is about `106.51 ms/request` versus V9 `72.13 ms/request`, with zero hard/search change. It is retained as engineering, not claimed as a headline semantic contribution.

Lazy exact proof-on-demand remains necessary for T5. Disabling replay keeps primary decisions/expansions fixed but degrades phase/resource/source/exact diagnosis; full V9 pays roughly `14.6 ms/request` average for the exact replay and `DiagnosticReplayRescueRate=0`.

### Why V9 still STOPs

V9 beats V8 (`641.7 -> 72.1 ms`) and V5 (`135.1 -> 72.1 ms`) but still misses the preregistered publication-oriented bound against V2 (`21.2 ms`): V9 is about `3.4x V2`, not `<=2x`.

Request-level profiling localizes the remaining cost to backward fixed-point construction rather than TSBS or diagnosis. V9 precondition build time is extremely correlated with direct build candidates and dominance comparisons (Spearman roughly `0.98`). The current state-worklist implementation re-propagates the whole child frontier whenever a child state changes; exact-signature filtering catches many repetitions only *after* suffix composition and comparison work has already been paid. The eight same-scene counterfactual passengers also expose large repeated graph-side structure, but cross-passenger caching is treated only as a later amortization/engineering opportunity and must not be used to game cold single-request latency.

The existing relation-aware learned feasibility guidance remains secondary. On this fast subset it saves about `0.266` expansion/request with a positive clustered CI, but increases mean latency by about `4.2 ms/request`; a larger neural backbone is therefore not justified as the next hot-path change.

### Mainline frozen after V9-fast

Freeze the **semantic spine** (subject to later full-test soundness confirmation):

`Passenger-Complete Planning -> Compiled Capability Program -> Evidence-Grounded Typed Service Semantics -> Forward Consumed Capability Ledger × Capability-Projected Backward Executable Preconditions -> Proof-on-Demand Typed Safe-Budget Search -> accepting complete-trip execution OR exact diagnostic rejection`.

Future versions may improve representation/runtime or add learned evidence/guidance, but should not re-open this problem/semantic hierarchy without contradictory evidence.

## V10 — Semi-Naive Capability-Projected Kernel (SN-CPK)

**Status:** implemented exact-construction closure candidate; **not yet promoted into the paper method**. No retraining is required for the first V10 experiment.

### Motivation

V10 does not introduce a new passenger-feasibility predicate. It asks whether the exact V9 projected fixed point can be constructed near the V2 runtime envelope without surrendering the typed backward mechanism.

### Exact semi-naive recurrence

Let `A_k(s)` be the already admitted capability-projected accepting summaries and `Delta_k(s)` the newly admitted summaries not yet propagated.  V10 evaluates only the differential recurrence

`Delta_{k+1}(s) = ND_Psi( Union_{e:s->s'} Pi_Psi(T_e o Delta_k(s')) ) \ A_k(s)`

`A_{k+1}(s) = ND_Psi( A_k(s) Union Delta_{k+1}(s) )`.

Each newly admitted summary is propagated upstream once. A queued summary that has already been dominated before propagation is skipped. Under the registered monotone typed edge transformers, if child transformer `a` is dominated by `b`, then `T_e o a` is dominated by `T_e o b`; therefore an old propagated image is only redundant and does not require semantic retraction.

### Capability-compiled packed dominance

V10 additionally precompiles the *same V9 partial order*:

- required-observation, active-clause and active-group subset relations become integer bit masks;
- cumulative/upper/probabilistic and lower-affordance effects are normalized into a common `smaller is no worse` numeric vector;
- categorical/interface transforms preserve V9's conservative semantic-identity rule;
- unusual/missing representations fall back to the exact historical comparator.

This is an implementation of V9 dominance, not a new feasibility relaxation.

### V10 invariants

- returned-plan soundness is unchanged;
- capability projection and fail-open frontier/depth bounds are unchanged;
- V10 may not change a V9 passenger decision or request-level TSBS expansion count;
- lazy exact diagnostic replay remains unchanged and a nonzero rescue remains a soundness warning;
- if the packed fast path ever changes the V9 relation, the mechanism is rejected rather than calibrated to the desired result.

### V10-fast preregistration

Use the same deterministic `256`-episode subset and frozen seed-13 CASA checkpoint. Timing controls are serial.

Controls:

- `full`: projection + semi-naive delta propagation + packed dominance + lazy exact proof;
- `v9_reference_runtime`: frozen exact V9 builder;
- `v2_reference_runtime`, `v5_reference_runtime`;
- `no_typed_viability`, `no_viability_kernel`;
- `no_semnaive_delta_propagation`: the same packed comparator but V9-style full-frontier state propagation;
- `no_packed_frontier_dominance`: semi-naive deltas with the exact V9 object comparator;
- `no_lazy_diagnostic_replay`;
- `no_learned_feasibility_guidance`.

GO requires all of:

1. `PCDecisionF1>=0.99`, FAR=FRR=`0`;
2. zero decision mismatch and **zero request-level TSBS expansion mismatch vs V9**;
3. every preregistered T5 macro/exact metric `>=V2-0.01`;
4. typed viability still beats structural-only with positive clustered expansion CI and typed pruning fires;
5. semi-naive propagation is active, preserves primary semantics, and lowers build candidates **and** dominance comparisons relative to `no_semnaive_delta_propagation`;
6. packed dominance preserves decisions/expansions, exercises its exact fast path, and is paired-latency non-worse than `no_packed_frontier_dominance`;
7. full V10 beats V9 and V5 latency with positive clustered CIs, lowers precondition-build time, and finally satisfies `mean latency <=2x V2`;
8. lazy replay preserves primary decisions/expansions, materially improves T5, fires on failures, and rescue rate remains zero.

Only `v10_fast_gate.json: status=GO` permits the 997-episode V10 confirmatory run. A genuine heterogeneous evidence network remains a later, separately attributable layer; it is not allowed to mask an unresolved exact-kernel construction bottleneck.
