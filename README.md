# CapPlan: passenger capability-aware autonomous mobility planner

CapPlan implements a passenger capability-aware planner and dataset builder for autonomous mobility service chains. The implementation enforces the paper semantics:

```text
PC(Omega, p) = Accept(sigma) AND Safe(tau_v) AND Sat(sigma, tau_v, Psi_p)
Psi_p = Compile(K_p, S_r) = (G_p, B_p, I_p, U_p, Z_p)
Allow(label, e) = Legal AND Anchor AND Interface AND Resource AND Uncertain AND Available
```

Passenger capability clauses are hard feasibility constraints by default. They are converted to soft penalties only for the explicit `soft_only_capability` ablation.

## What the repository contains

The codebase provides:

- canonical dataset schemas for scenes, entrances, PUDO anchors, pedestrian accessibility graphs, vehicle interfaces, capability profiles/contracts, requirement groups, transition labels, passenger edge labels, skeleton labels, certificates, and counterfactual pairs;
- deterministic synthetic smoke scenes that are explicitly marked `source=synthetic`;
- strict nuPlan mode that requires the real nuPlan devkit and real nuPlan files and never silently synthesizes nuPlan data;
- a passenger capability compiler that emits `G_p`, `B_p`, `I_p`, `U_p`, and `Z_p`;
- typed resource algebra with cumulative, upper, lower, categorical, and probabilistic ledgers;
- service transition generation for access, wait, board, ride, alight, egress, destination acceptance, and replan transitions;
- typed safe budget search enforcing `Legal`, `Anchor`, `Interface`, `Resource`, `Uncertain`, and `Available` in order;
- an independent verifier/oracle for transition validity, passenger-specific edge feasibility, skeleton labels, and failure certificates;
- a learned-mode CASA training smoke implementation plus a separately named `heuristic_oracle_baseline`;
- strict mock trajectory safety and clear errors for unavailable nuPlan closed-loop execution;
- closed-loop evaluation that loads saved dataset artifacts from disk instead of regenerating graph/PUDO/vehicle records.

## Install

```bash
git clone <repo-url> CapPlan
cd CapPlan
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install -e .
pytest -q
```

Optional nuPlan support:

```bash
pip install -r requirements-nuplan.txt
python -c "import nuplan; print('nuPlan installed')"
```

The optional file `requirements-nuplan.txt` is only needed in environments where the nuPlan devkit is installed or installable. Synthetic smoke tests do not require nuPlan.

## Build synthetic smoke dataset

```bash
python scripts/build_dataset.py \
  --scene_source synthetic \
  --max_scenarios 8 \
  --accessibility_source synthetic_local \
  --num_contracts_per_scene 4 \
  --output_dir outputs/datasets/synthetic_smoke \
  --seed 13

python scripts/validate_dataset.py \
  --dataset_dir outputs/datasets/synthetic_smoke \
  --strict
```

This writes deterministic local scenes, routable pedestrian graphs, PUDO anchors linked to pedestrian nodes and road metadata, multiple vehicle interfaces, functional passenger profiles, compiled contracts, candidate service transitions, independent oracle labels, and counterfactual pairs.

## Build nuPlan passenger capability dataset

```bash
python scripts/build_dataset.py \
  --scene_source nuplan \
  --nuplan_data_root /data0/senzeyu2/dataset/nuplan/data/cache \
  --nuplan_map_root /data0/senzeyu2/dataset/nuplan/maps \
  --nuplan_db_root /data0/senzeyu2/dataset/nuplan/data/cache \
  --nuplan_db_dirs train_boston train_pittsburgh \
  --nuplan_map_version nuplan-maps-v1.0 \
  --split train \
  --max_scenarios 100 \
  --accessibility_source synthetic_local \
  --num_contracts_per_scene 4 \
  --output_dir outputs/datasets/abilitybench_av_train_boston_pittsburgh \
  --strict

python scripts/validate_dataset.py \
  --dataset_dir outputs/datasets/abilitybench_av_mini \
  --strict
```

In `--scene_source nuplan` mode, the builder requires official nuPlan devkit APIs and real nuPlan files. If they are unavailable or a critical field cannot be extracted, the command raises a clear error. It does not produce synthetic scenes labelled as nuPlan.

## Train CASA-Net

```bash
python scripts/train_casa.py \
  --dataset_dir outputs/datasets/abilitybench_av_mini \
  --output_dir outputs/models/casa_mini \
  --epochs 20 \
  --batch_size 16 \
  --lr 1e-3 \
  --seed 13 \
  --device auto
```

Training outputs:

```text
outputs/models/casa_mini/
  checkpoint.pt
  vocab.json
  config.json
  train_metrics.jsonl
  val_metrics.json
```

The default `train_casa.py` mode is `learned`. The deterministic baseline is named `heuristic_oracle_baseline` and is not described as a learned model.

## Run planner on one episode

```bash
python scripts/run_planner.py \
  --dataset_dir outputs/datasets/synthetic_smoke \
  --episode_id synthetic_0000 \
  --passenger_id synthetic_0000:p0 \
  --model_dir outputs/models/casa_mini \
  --output outputs/plans/demo_plan.json \
  --trajectory_mode mock_strict
```

The resulting plan JSON reports the selected service skeleton when one exists, the passenger-complete outcome, trajectory metrics, capability margins, and a failure certificate if planning or safety fails.

## Run closed-loop / strict mock evaluation

```bash
python scripts/run_closed_loop_eval.py \
  --dataset_dir outputs/datasets/synthetic_smoke \
  --output_dir outputs/eval/synthetic_smoke \
  --trajectory_mode mock_strict \
  --casa_mode heuristic_oracle_baseline

python scripts/compute_metrics.py \
  --episode_metrics outputs/eval/synthetic_smoke/episode_metrics.jsonl \
  --counterfactual_pairs outputs/datasets/synthetic_smoke/counterfactual_pairs.jsonl \
  --output outputs/eval/synthetic_smoke/metrics.json
```

`mock_strict` evaluates collision, drivable-area compliance, traffic-rule compliance, route completion, acceleration, jerk, and motion exposure from saved synthetic scene fields. It is a strict smoke evaluator, not a substitute for nuPlan paper-level closed-loop results.

`nuplan_closed_loop` is available as an explicit mode, but it must run inside a configured nuPlan simulation environment. Without nuPlan, it raises a clear error and does not report paper-level vehicle safety metrics.

## Run ablations

```bash
python scripts/run_ablations.py \
  --dataset_dir outputs/datasets/synthetic_smoke \
  --output_dir outputs/eval/ablations_synthetic \
  --trajectory_mode mock_strict
```

Implemented ablations:

```text
full
no_capability_compiler
no_service_automaton
no_casa_net_transitions
no_typed_resource_ledger
no_conservative_margins
no_completion_value_guidance
soft_only_capability
```

Each ablation changes only its named component. Symbolic hard constraints remain active unless `soft_only_capability` is selected.

## Dataset layout

`build_dataset.py` writes the canonical layout:

```text
outputs/datasets/<name>/
  dataset_manifest.json
  splits/
    train_episodes.txt
    val_episodes.txt
    test_episodes.txt
  scenes.jsonl
  episodes.jsonl
  entrances.jsonl
  accessibility_graphs/
    <episode_id>.nodes.jsonl
    <episode_id>.edges.jsonl
  pudo_anchors.jsonl
  vehicle_interfaces.jsonl
  capability_profiles.jsonl
  capability_contracts.jsonl
  requirement_groups.jsonl
  candidate_transitions.jsonl
  transition_labels.jsonl
  passenger_edge_labels.jsonl
  resource_labels.jsonl
  skeleton_labels.jsonl
  certificate_labels.jsonl
  counterfactual_pairs.jsonl
```

`dataset_manifest.json` records the scene source, accessibility source, builder settings, strict mode, counts, and nuPlan paths when nuPlan mode is requested.

## What each output proves

- `scenes.jsonl` proves the traffic scene source and stores route/map identifiers.
- `entrances.jsonl` stores passenger origin/destination service anchors.
- `accessibility_graphs/*.jsonl` stores the pedestrian/curb graph used by planning and evaluation.
- `pudo_anchors.jsonl` stores PUDO anchors linked to both vehicle stop metadata and adjacent pedestrian nodes.
- `vehicle_interfaces.jsonl` stores interface affordances such as ramp, lift, low-floor, door side, door width, clearance, dwell, and notification modes.
- `capability_profiles.jsonl` stores functional trip-planning profiles, not demographic or medical labels.
- `capability_contracts.jsonl` stores executable clauses and profile metadata.
- `requirement_groups.jsonl` stores grouped requirements such as `ramp OR lift OR low-floor+kneeling`.
- `candidate_transitions.jsonl` stores all generated service transitions and their evidence.
- `transition_labels.jsonl` stores passenger-independent `z_e` validity labels.
- `passenger_edge_labels.jsonl` stores passenger-specific `y_e,p` feasibility labels for every transition and passenger contract.
- `resource_labels.jsonl` stores typed resource evidence per transition.
- `skeleton_labels.jsonl` stores independently verified feasible service skeletons.
- `certificate_labels.jsonl` stores independent failure certificates.
- `counterfactual_pairs.jsonl` stores same-scene weak/strict or modality-change profile pairs used by CRsp.

## Metrics

Evaluation reports:

```text
CR      collision rate
RC      route completion
TRV     traffic-rule violation rate
TT      travel time
DR      detour ratio
PCR     passenger completion rate
TSPIR   traffic-safe route-complete passenger-incomplete rate
PAR     phase acceptance rate
CVR     capability violation rate
CSM     capability safety margin
FLF     first/last-meter feasibility
BAF     boarding/alighting feasibility
MER     motion exposure ratio
MVR     motion violation rate
SBR     safe budget residual
IR      uncertainty/inconclusive rate
DF      diagnostic fidelity against independent oracle certificates
SME     signed margin error against independent oracle certificates
CRsp    capability responsiveness over explicit counterfactual pairs
ECA     efficiency cost of accommodation
```

Passenger completion requires all three conditions: accepted service phase, vehicle safety, and capability satisfaction.

## Validation

Run:

```bash
python scripts/validate_dataset.py \
  --dataset_dir outputs/datasets/synthetic_smoke \
  --strict
```

The validator checks canonical files, reference integrity, saved graph availability, transition labels, passenger edge labels, resource labels, counterfactual pairs, and missing-evidence fail-closed semantics.

## Tests

```bash
pytest -q
```

The test suite covers extended schemas, capability profiles, grouped requirements, nuPlan mode errors, graph routing, PUDO spatial binding, transition labels, independent oracle behavior, dataset loading, passenger-complete semantics, metric semantics, CASA training smoke, ablation isolation, and README command presence.

## Limitations

- Synthetic mode is for smoke testing and deterministic regression tests.
- Real nuPlan dataset building requires the official nuPlan devkit and actual dataset/map files.
- Full nuPlan closed-loop execution must be run in a configured nuPlan simulation environment. The repository provides the explicit wrapper/error path and does not claim nuPlan closed-loop metrics from `mock_strict` results.
