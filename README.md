# CapPlan: Passenger-Complete Autonomous Mobility Planning

## 1. Overview

CapPlan implements passenger-complete planning for autonomous mobility.  The planner does **not** treat success as merely driving a vehicle to a route goal.  A plan is successful only when a concrete passenger can complete the entire entrance-to-entrance service chain:

`origin → access → wait → board → ride → alight → egress → destination`

under an explicit passenger capability contract.  The returned object is either:

* a passenger-complete service skeleton and typed resource ledger, or
* a diagnostic failure certificate explaining the failed phase, transition, resource, signed margin, evidence source, and confidence.

The implementation follows the paper architecture:

* passenger-complete service semantics;
* passenger-complete service automaton;
* capability semantic type system;
* implementation-oriented resource registry;
* typed resource algebra;
* CASA transition construction;
* CASA-Net compatible transition/demand predictor interface;
* typed safe-budget search;
* conservative evidence under uncertainty;
* diagnostic failure certificate construction;
* nuPlan-based dataset interface with deterministic synthetic fallback;
* closed-loop or mock closed-loop evaluation;
* ablations and metric computation.

## 2. Core non-negotiable implementation requirements

The repository enforces the following design constraints.

* Hard capability clauses remain hard constraints in the full planner.  They are evaluated by symbolic typed feasibility checks in `capplan/semantics/typed_resource_algebra.py` and `capplan/planning/typed_safe_budget_search.py`.
* Typed resources are not collapsed into one scalar reward.  Cumulative burdens, upper bottleneck burdens, lower bottleneck affordances, categorical predicates, and probabilistic risks have separate update and dominance rules.
* Categorical interface requirements are predicates.  Door side, ramp, lift, low-floor, curb-ramp, step-free, and identification modality are evaluated as compatibility conditions, not numeric costs.
* Lower-bounded affordances and upper-bounded burdens use different conservative evidence directions.  Upper/cumulative burdens use `x_hat + beta * sigma_hat`; lower affordances use `x_hat - beta * sigma_hat`; categorical fields keep predicate-normalized values.
* Failure certificates are generated from infeasible search frontiers.  Every rejected transition produces violation records, and the returned certificate is selected by most severe normalized signed margin, then higher confidence, then earlier failed phase.
* All ablations are controlled by config or command-line flags.  Ablations modify only the intended component and do not silently disable unrelated hard checks unless the explicit `--soft_only_capability` ablation is selected.

## 3. Installation

Create and activate an environment, then install the package in editable mode:

```bash
cd capplan
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -e .
```

Run the tests:

```bash
pytest -q
```

Optional nuPlan dependencies can be installed separately according to your local nuPlan setup.  This repository does not require nuPlan for tests or examples because `NuPlanAdapter` provides a deterministic fallback with the same downstream schema.

```bash
# Optional, environment-dependent:
# pip install nuplan-devkit
```

## 4. Data preparation

The benchmark builder uses nuPlan as the traffic-scene substrate when available and otherwise emits deterministic synthetic scenarios with the same schema.

```bash
python scripts/build_dataset.py \
  --nuplan_root /path/to/nuplan \
  --split mini \
  --max_scenarios 16 \
  --output_dir outputs/datasets/abilitybench_av \
  --accessibility_source synthetic \
  --num_contracts_per_scene 2 \
  --seed 13
```

For a no-nuPlan smoke test:

```bash
python scripts/build_dataset.py \
  --max_scenarios 4 \
  --output_dir outputs/datasets/synthetic \
  --num_contracts_per_scene 2 \
  --seed 13
```

The pipeline performs these steps:

1. Load nuPlan scenarios or scenario metadata through `capplan/data/nuplan_adapter.py`.  The returned scene record includes ego history, agent history, map context, route corridor, drivable-area semantics, and lane/traffic metadata placeholders.
2. Extract ego history, agent history, route corridor, map context, and drivable/lane semantics into episode metadata.
3. Generate or attach origin and destination entrances.
4. Generate or attach a pedestrian accessibility graph with sidewalk/path width, length, slope, cross-slope, surface, curb-ramp presence, step-free continuity, obstacle state, lighting, shelter, and confidence.
5. Generate candidate pickup/drop-off anchors from curbside geometry, legal stopping fields, adjacent sidewalk width, door-side clearance, dynamic blockage risk, and map confidence.
6. Generate vehicle-interface metadata: door side, ramp, lift, low-floor, door width, deployment clearance, notification modalities, dwell policy, and kneeling capability.
7. Generate passenger capability contracts, including same-scene counterfactual contract pairs.
8. Generate CASA candidate transitions for access, wait, board, ride, alight, egress, and replan actions.
9. Generate transition labels, typed resource labels, passenger-complete skeleton labels, and failure certificate labels with the offline `LabelOracle`.

The builder writes:

```text
outputs/datasets/<name>/
  episodes.jsonl
  accessibility_graphs/
  pudo_anchors.jsonl
  vehicle_interfaces.jsonl
  capability_contracts.jsonl
  candidate_transitions.jsonl
  resource_labels.jsonl
  skeleton_labels.jsonl
  certificate_labels.jsonl
```

## 5. Passenger capability labels

The label structure has three levels.

### A. Contract labels

Contract labels are typed capability clauses.  They include phase scope, operator, threshold, resource kind, confidence, risk metadata, evidence source, and consent scope.

Example:

```json
{
  "passenger_id": "synthetic_mini_0000:p0",
  "clauses": [
    {
      "resource_name": "access_distance_m",
      "phase_scope": ["access"],
      "operator": "<=",
      "threshold": 220.0,
      "kind": "cumulative",
      "confidence": 0.95,
      "risk_tolerance": null,
      "source": "onboarding",
      "consent_scope": "trip_planning"
    },
    {
      "resource_name": "ramp",
      "phase_scope": ["board", "alight"],
      "operator": "requires",
      "threshold": true,
      "kind": "categorical",
      "confidence": 1.0,
      "risk_tolerance": null,
      "source": "onboarding",
      "consent_scope": "trip_planning"
    }
  ],
  "metadata": {"profile": "default_accessible"}
}
```

### B. Transition/resource labels

Transition labels describe whether a candidate edge is physically, topologically, interface-wise, and dynamically valid.  Resource labels give typed evidence values for each candidate transition.

Example:

```json
{
  "transition_id": "synthetic_mini_0000:access:pudo_0",
  "from_anchor": "origin",
  "to_anchor": "pudo_0",
  "from_phase": "origin",
  "to_phase": "access",
  "action": "access",
  "resource_evidence": [
    {"resource_name": "access_distance_m", "kind": "cumulative", "value": 65.0, "sigma": 1.95, "confidence": 0.93, "source": "pedestrian_graph"},
    {"resource_name": "path_width_m", "kind": "lower", "value": 1.6, "sigma": 0.05, "confidence": 0.93, "source": "accessibility_map"}
  ],
  "availability": 1.0,
  "map_confidence": 0.93
}
```

### C. Skeleton/certificate labels

Feasible episodes receive a passenger-complete service skeleton with a typed ledger at each step.  Infeasible episodes receive a failure certificate.

Skeleton example:

```json
{
  "episode_id": "synthetic_mini_0000",
  "passenger_id": "synthetic_mini_0000:p0",
  "accepted": true,
  "transitions": ["...access...", "...wait...", "...board...", "...ride...", "...alight...", "...egress...", "...dest..."],
  "final_ledger": {"access_distance_m": 66.95, "path_width_m": 1.55, "motion_exposure": 1.2},
  "cost": 430.0
}
```

Failure certificate example:

```json
{
  "episode_id": "synthetic_mini_0001",
  "passenger_id": "synthetic_mini_0001:p1",
  "phase": "access",
  "transition_id": "synthetic_mini_0001:access:pudo_2",
  "resource_type": "path_width_m",
  "signed_margin": -0.18,
  "evidence_source": "accessibility_map",
  "confidence": 0.86,
  "reason": "resource_or_interface"
}
```

## 6. Data schema

The canonical dataclasses live in `capplan/data/schemas.py`.  JSONL records are the JSON form of these dataclasses.

### Episode metadata: `episodes.jsonl`

```json
{
  "episode_id": "synthetic_mini_0000",
  "scenario_id": "scenario_0000",
  "split": "mini",
  "origin_anchor": "origin",
  "destination_anchor": "destination",
  "request_time_s": 1000.0,
  "route_length_m": 4000.0,
  "shortest_route_length_m": 3600.0,
  "seed": 13,
  "nuplan_available": false,
  "metadata": {"source": "synthetic_fallback"}
}
```

### Accessibility graph: `accessibility_graphs/<episode_id>.jsonl`

```json
{
  "episode_id": "synthetic_mini_0000",
  "nodes": [{"node_id": "origin", "x": 0.0, "y": 0.0, "kind": "entrance", "confidence": 0.98}],
  "edges": [{
    "edge_id": "origin_to_pudo_0",
    "from_node": "origin",
    "to_node": "pudo_0",
    "length_m": 65.0,
    "width_m": 1.6,
    "slope": 0.025,
    "cross_slope": 0.015,
    "surface": "paved",
    "curb_ramp": true,
    "step_free": true,
    "obstacle": false,
    "lighting": "day",
    "shelter": true,
    "confidence": 0.93
  }]
}
```

### PUDO anchor: `pudo_anchors.jsonl`

```json
{
  "anchor_id": "pudo_0",
  "episode_id": "synthetic_mini_0000",
  "x": 20.0,
  "y": 0.0,
  "side": "right",
  "legal_stop": true,
  "curb_height_m": 0.04,
  "sidewalk_width_m": 1.6,
  "deployment_clearance_m": 1.8,
  "blockage_risk": 0.03,
  "map_confidence": 0.95,
  "lighting": "day",
  "shelter": true
}
```

### Vehicle interface: `vehicle_interfaces.jsonl`

```json
{
  "vehicle_id": "veh_0",
  "episode_id": "synthetic_mini_0000",
  "door_side": "right",
  "ramp": true,
  "lift": false,
  "low_floor": true,
  "door_width_m": 1.05,
  "deployment_clearance_m": 1.6,
  "notification_modes": ["visual", "audio", "app"],
  "dwell_time_s": 60.0,
  "kneeling": true
}
```

### Capability contract: `capability_contracts.jsonl`

A `CapabilityContract` contains `passenger_id`, `clauses`, and `metadata`.  Each clause has:

```text
resource_name: string
phase_scope: list[string]
operator: <= | >= | = | in | requires | forbids
threshold: number | string | bool | list
kind: cumulative | upper | lower | categorical | probabilistic
confidence: float
risk_tolerance: float | null
source: string
consent_scope: string
```

### Candidate transition: `candidate_transitions.jsonl`

A `CandidateTransition` contains:

```text
transition_id: string
episode_id: string
from_anchor: string
to_anchor: string
from_phase: origin|access|wait|board|ride|alight|egress|destination
to_phase: origin|access|wait|board|ride|alight|egress|destination
action: access|wait|board|ride|alight|egress|replan
resource_evidence: list[ResourceEvidence]
availability: float
map_confidence: float
interface: dict
dynamic: dict
cost: float
completion_value: float
```

### Resource evidence: `resource_labels.jsonl`

```text
resource_name: string
kind: cumulative | upper | lower | categorical | probabilistic
value: number | bool | string | list
sigma: float
confidence: float
source: string
```

### Passenger-complete skeleton: `skeleton_labels.jsonl`

```text
episode_id: string
passenger_id: string
accepted: bool
transitions: list[string]
steps: list[LedgerStep]
final_ledger: dict[string, number|bool]
cost: float
```

### Failure certificate: `certificate_labels.jsonl`

```text
episode_id: string
passenger_id: string
phase: string
transition_id: string
resource_type: string
signed_margin: float
evidence_source: string
confidence: float
reason: string
violations: list[ViolationRecord]
```

### Metric output: `episode_metrics.jsonl` and `metrics.json`

Per-episode records contain vehicle fields, passenger-complete fields, margins, certificate dictionaries, and cost baselines.  Aggregated metrics are stored as:

```json
{"CR": 0.0, "RC": 1.0, "PCR": 0.75, "TSPIR": 0.25, "CVR": 0.0, "CSM": 0.18}
```

## 7. Training

The current repository includes a CASA-Net-compatible deterministic baseline so the pipeline runs without GPU training.  The script writes a checkpoint manifest and preserves the same input/output interface for replacing the baseline with an HGT/MLP model.

Capability compiler training or validation:

```bash
python scripts/train_casa.py \
  --dataset_dir outputs/datasets/abilitybench_av \
  --output_dir outputs/checkpoints/compiler_baseline
```

Train or calibrate CASA components:

```bash
python scripts/train_casa.py \
  --dataset_dir outputs/datasets/abilitybench_av \
  --output_dir outputs/checkpoints/casa_baseline \
  --train_phase_predictor \
  --train_transition_predictor \
  --train_resource_predictor \
  --calibrate_uncertainty \
  --train_completion_value
```

The model interface is:

* Input: service graph, active capability tokens, phase belief, ego/agent/map features, candidate transitions.
* Output: typed evidence predictions, uncertainty predictions, dynamic availability, completion value, and phase belief.

The default `CASANet` class is in `capplan/models/casa_net.py`.  The output contract is defined by `CASAOutput` and `TransitionPrediction`.

## 8. Planning

Run the full planner on a synthetic episode:

```bash
python scripts/run_planner.py \
  --episode_id demo_episode \
  --output outputs/plans/demo_plan.json
```

The full planner performs:

1. Compile passenger capability contracts into phase-scoped hard clauses, budgets, interface predicates, uncertainty clauses, and CASA tokens.
2. Construct the passenger-complete service automaton.
3. Generate CASA transitions for access, wait, board, ride, alight, egress, and replan.
4. Run CASA-Net compatible prediction to obtain typed evidence, uncertainty, dynamic availability, and completion value.
5. Convert evidence conservatively by resource type.
6. Run typed safe-budget search.
7. Return a success ledger if an accepting service state is reached and all active clauses satisfy the typed algebra.
8. Otherwise return a failure certificate generated from infeasible search frontiers.
9. Refine the selected skeleton into a deterministic trajectory placeholder when nuPlan closed-loop integration is unavailable.

A successful plan contains:

```json
{
  "success": true,
  "skeleton": {"accepted": true, "transitions": ["..."], "final_ledger": {"...": "..."}},
  "certificate": null,
  "diagnostics": {"expansions": 8, "trajectory": {"available": true}}
}
```

An infeasible plan contains:

```json
{
  "success": false,
  "skeleton": null,
  "certificate": {"phase": "access", "resource_type": "path_width_m", "signed_margin": -0.18}
}
```

## 9. Closed-loop evaluation

Run closed-loop or mock closed-loop evaluation:

```bash
python scripts/run_closed_loop_eval.py \
  --dataset_dir outputs/datasets/abilitybench_av \
  --output_dir outputs/metrics/closed_loop
```

If full nuPlan simulation is available, replace `refine_trajectory` and `ClosedLoopRunner` hooks with the official simulator and metrics.  If nuPlan is unavailable, the deterministic mock runner uses scenario route lengths and selected skeleton trajectories while still computing all passenger-complete metrics.

Outputs:

```text
outputs/metrics/closed_loop/
  episode_metrics.jsonl
  plans.jsonl
  metrics.json
```

## 10. Ablations

Ablations are exposed through `PlannerConfig`, `configs/ablations.yaml`, and CLI flags.

Run all ablations:

```bash
python scripts/run_ablations.py \
  --dataset_dir outputs/datasets/abilitybench_av \
  --output_dir outputs/tables
```

Single-plan ablation flags:

```bash
python scripts/run_planner.py --no_completion_value_guidance
python scripts/run_planner.py --no_conservative_margins
python scripts/run_planner.py --soft_only_capability
```

Ablation definitions:

* `--no_capability_compiler`: removes typed semantic compilation and, in the ablation path, prevents structured clauses from being converted into normal hard planning tables.
* `--no_service_automaton`: disables lifecycle legality checks while leaving resources and interface checks available.  This isolates the automaton effect.
* `--no_casa_net_transitions`: removes CASA-Net value/availability guidance and uses deterministic transition evidence.
* `--no_typed_resource_ledger`: collapses resource evidence into a scalar budget inside search.  This is intentionally an ablation and is not used by the full method.
* `--no_conservative_margins`: uses point estimates instead of uncertainty-adjusted conservative evidence.
* `--no_completion_value_guidance`: removes learned completion value from the priority function while retaining hard feasibility checks.
* `--soft_only_capability`: removes hard typed feasibility checks and keeps capability fields only as soft/planning metadata.  This ablation is expected to admit avoidable hard capability violations.

## 11. Metrics

Metrics are implemented in `capplan/evaluation/metrics.py`.  Each function has a docstring with definition and input schema.

* Collision Rate (CR): fraction of episodes with any collision.
* Route Completion (RC): completed route length divided by planned route length.
* Traffic Rule Violation (TRV): fraction of episodes with any lane, red-light, drivable-area, stopping-rule, or right-of-way violation; optional count per km.
* Travel Time (TT): time from request to destination completion or failure.
* Detour Ratio (DR): vehicle distance divided by shortest traffic-feasible route distance.
* Passenger Completion Rate (PCR): fraction of episodes satisfying passenger-complete success.
* Traffic-Safe Passenger-Incomplete Rate (TSPIR): fraction with no collision, route completion above threshold, and passenger completion false.
* Phase Acceptance Rate (PAR): fraction whose service trace reaches the accepting automaton state.
* Capability Violation Rate (CVR): average fraction of active hard capability clauses with negative signed margin.
* Capability Safety Margin (CSM): worst normalized slack over active capability clauses.
* First/Last-Meter Feasibility (FLF): indicator that access and egress distance, slope, width, surface/curb/crossing, and confidence constraints hold.
* Boarding/Alighting Feasibility (BAF): indicator that door side, ramp/lift, curb height, clearance, dwell, and interface constraints hold.
* Motion Exposure Ratio (MER): motion exposure divided by passenger motion budget.
* Motion Violation Rate (MVR): fraction violating acceleration, jerk, braking, or motion-exposure clauses.
* Safe Budget Residual (SBR): minimum normalized remaining resource margin after completion.
* Inconclusive Rate (IR): fraction failing uncertainty or confidence clauses.
* Diagnostic Fidelity (DF): accuracy over failed phase, resource type, and evidence source compared with oracle certificate.
* Signed Margin Error (SME): mean absolute error between reported and verifier-computed signed margins.
* Capability Responsiveness (CRsp): fraction of same-scene counterfactual pairs with verifier-approved path, interface, trajectory, or certificate change.
* Efficiency Cost of Accommodation (ECA): `(TT_cap - TT_std)/(TT_std + eps)` or analogous cost ratio.

Compute metrics from saved episode records:

```bash
python scripts/compute_metrics.py \
  --episode_metrics outputs/metrics/closed_loop/episode_metrics.jsonl \
  --output outputs/tables/metrics.json
```

## 12. Experiment commands

Build benchmark:

```bash
python scripts/build_dataset.py \
  --nuplan_root /path/to/nuplan \
  --split mini \
  --max_scenarios 32 \
  --output_dir outputs/datasets/abilitybench_av \
  --accessibility_source synthetic \
  --num_contracts_per_scene 2 \
  --seed 13
```

Main results:

```bash
python scripts/run_closed_loop_eval.py \
  --dataset_dir outputs/datasets/abilitybench_av \
  --output_dir outputs/metrics/main
```

Ablations:

```bash
python scripts/run_ablations.py \
  --dataset_dir outputs/datasets/abilitybench_av \
  --output_dir outputs/tables
```

Same-scene capability counterfactuals:

```bash
python scripts/build_dataset.py \
  --max_scenarios 16 \
  --num_contracts_per_scene 2 \
  --output_dir outputs/datasets/counterfactuals \
  --seed 21
python scripts/run_closed_loop_eval.py \
  --dataset_dir outputs/datasets/counterfactuals \
  --output_dir outputs/metrics/counterfactuals
```

Failure diagnosis:

```bash
python scripts/build_dataset.py \
  --max_scenarios 16 \
  --num_contracts_per_scene 3 \
  --output_dir outputs/datasets/diagnosis \
  --seed 31
python scripts/run_closed_loop_eval.py \
  --dataset_dir outputs/datasets/diagnosis \
  --output_dir outputs/metrics/diagnosis
python scripts/compute_metrics.py \
  --episode_metrics outputs/metrics/diagnosis/episode_metrics.jsonl \
  --output outputs/tables/diagnosis_results.json
```

Uncertainty robustness:

```bash
python scripts/run_ablations.py \
  --dataset_dir outputs/datasets/abilitybench_av \
  --output_dir outputs/tables/uncertainty
```

Metric aggregation:

```bash
python scripts/compute_metrics.py \
  --episode_metrics outputs/metrics/main/episode_metrics.jsonl \
  --output outputs/tables/main_metrics.json
```

## 13. Tests

Run all tests:

```bash
pytest -q
```

The test suite verifies:

* cumulative resources sum correctly;
* upper bottleneck resources use `max`;
* lower bottleneck affordances use `min`;
* categorical resources use predicate conjunction;
* conservative evidence direction is correct;
* stricter contracts do not admit infeasible plans accepted by weaker contracts;
* typed safe-budget search never returns a skeleton violating hard clauses;
* failure certificates match the most severe normalized violation with tie-breakers;
* ablation flags disable the intended components;
* dataset builder emits the required passenger capability labels and schemas;
* all metrics are present and computable.

## 14. Expected outputs

The repository writes all generated artifacts under `outputs/`:

```text
outputs/
  datasets/
    abilitybench_av/
    synthetic/
  checkpoints/
    casa_baseline/
  plans/
    demo_plan.json
  certificates/
  metrics/
    closed_loop/
      episode_metrics.jsonl
      plans.jsonl
      metrics.json
  tables/
    main_results.csv
    ablation_results.csv
    counterfactual_results.csv
    diagnosis_results.csv
    uncertainty_results.csv
```

Each run saves enough information for inspection: config flags, success ledgers, final typed resource states, margins, failure certificates, and aggregate metrics.

## Repository map

```text
capplan/
  README.md
  IMPLEMENTATION_SPEC.md
  requirements.txt
  pyproject.toml
  configs/
    default.yaml
    dataset_nuplan.yaml
    experiments.yaml
    ablations.yaml
  capplan/
    data/
      nuplan_adapter.py
      accessibility_layer.py
      pudo_interface_layer.py
      capability_contracts.py
      label_oracle.py
      schemas.py
    semantics/
      service_automaton.py
      resource_registry.py
      typed_resource_algebra.py
      capability_compiler.py
    models/
      casa_net.py
      predictors.py
    planning/
      transition_generator.py
      typed_safe_budget_search.py
      trajectory_refinement.py
      certificates.py
      planner.py
    evaluation/
      metrics.py
      closed_loop.py
      ablations.py
      experiment_runner.py
    utils/
      geometry.py
      graph.py
      logging.py
      serialization.py
  scripts/
    build_dataset.py
    train_casa.py
    run_planner.py
    run_closed_loop_eval.py
    run_ablations.py
    compute_metrics.py
  tests/
    test_resource_algebra.py
    test_capability_compiler.py
    test_service_automaton.py
    test_transition_generation.py
    test_typed_safe_budget_search.py
    test_certificates.py
    test_metrics.py
    test_dataset_labels.py
```

## Reproduction quickstart

```bash
cd capplan
pip install -e .
pytest -q
python scripts/build_dataset.py --max_scenarios 4 --output_dir outputs/datasets/synthetic --num_contracts_per_scene 2 --seed 13
python scripts/run_closed_loop_eval.py --dataset_dir outputs/datasets/synthetic --output_dir outputs/metrics/closed_loop
python scripts/run_ablations.py --dataset_dir outputs/datasets/synthetic --output_dir outputs/tables
python scripts/compute_metrics.py --episode_metrics outputs/metrics/closed_loop/episode_metrics.jsonl --output outputs/tables/metrics.json
```
