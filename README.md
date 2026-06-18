# CapPlan: passenger-complete planning implementation

CapPlan implements the passenger-complete planning stack described in `main.tex`.

Core semantics used by the code:

```text
PC(Omega, p) = Accept(sigma) AND Safe(tau_v) AND Sat(sigma, tau_v, Psi_p)
Psi_p = Compile(K_p, S_r) = (G_p, B_p, I_p, U_p, Z_p)
Allow(label, e) = Legal AND Anchor AND Interface AND Resource AND Uncertain AND Available
```

The repository keeps two execution families strictly separated:

- **smoke mode**: deterministic synthetic/local artifacts for CI and fast debugging. It may use `synthetic`, `synthetic_local`, `synthetic_smoke`, and `mock_strict`.
- **paper mode**: real-data path for main experiments. It rejects synthetic/proxy/toy graph, PUDO, service, profile, and mock trajectory sources; missing external data raises an explicit error instead of silently falling back to synthetic data.

`mock_strict` is a strict smoke evaluator, not a substitute for nuPlan paper-level closed-loop results.

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

Optional nuPlan environment:

```bash
pip install -r requirements-nuplan.txt
python -c "import nuplan; print('nuPlan installed')"
```

## Smoke pipeline

```bash
python scripts/build_dataset.py \
  --scene_source synthetic \
  --max_scenarios 8 \
  --accessibility_source synthetic_local \
  --service_layer_source synthetic_smoke \
  --num_contracts_per_scene 4 \
  --output_dir outputs/datasets/synthetic_smoke \
  --seed 13

python scripts/validate_dataset.py \
  --dataset_dir outputs/datasets/synthetic_smoke \
  --strict

python scripts/audit_dataset_quality.py \
  --dataset_dir outputs/datasets/synthetic_smoke

python scripts/train_casa.py \
  --dataset_dir outputs/datasets/synthetic_smoke \
  --output_dir outputs/models/casa_smoke \
  --epochs 2 \
  --batch_size 16 \
  --device cpu \
  --model_type linear_smoke

python scripts/run_closed_loop_eval.py \
  --dataset_dir outputs/datasets/synthetic_smoke \
  --output_dir outputs/eval/synthetic_smoke \
  --trajectory_mode mock_strict \
  --casa_mode heuristic_oracle_baseline \
  --ablation full
```

## Paper-mode accessibility graph build

Prepare real node/edge records from nuPlan HD map plus OSM/OpenSidewalks/city GIS/DEM/curb inventory, then run:

```bash
python scripts/build_accessibility_graphs.py \
  --scene_dataset_dir /data/capplan/scenes \
  --nuplan_map_root /data/nuplan/maps \
  --nuplan_map_version nuplan-maps-v1.0 \
  --osm_source /data/osm_or_opensidewalks \
  --city_gis_dir /data/city_gis \
  --elevation_source /data/dem \
  --nodes_jsonl /data/prepared/access_nodes.jsonl \
  --edges_jsonl /data/prepared/access_edges.jsonl \
  --episode_ids train_episodes.txt \
  --output_graph_dir outputs/prepared/accessibility_graphs \
  --min_nodes_per_episode 100 \
  --min_edges_per_episode 150 \
  --fail_on_synthetic
```

This script is a real-data materializer/validator. It does not generate toy graphs when inputs are missing.

## Paper-mode PUDO evidence build

```bash
python scripts/build_pudo_evidence.py \
  --scene_dataset_dir /data/capplan/scenes \
  --accessibility_graph_dir outputs/prepared/accessibility_graphs \
  --nuplan_map_root /data/nuplan/maps \
  --curb_regulation_dir /data/curb_regulations \
  --city_gis_dir /data/city_gis \
  --input_pudo_evidence_jsonl /data/prepared/raw_pudo_evidence.jsonl \
  --curb_inventory_jsonl /data/prepared/curb_inventory.jsonl \
  --curb_regulation_jsonl /data/prepared/curb_regulations.jsonl \
  --output_pudo_evidence_jsonl outputs/prepared/pudo_evidence.jsonl \
  --candidate_radius_m 80 \
  --max_route_deviation_m 250 \
  --max_core_missing_rate 0.05 \
  --fail_on_missing_core_evidence
```

Core PUDO evidence fields are curbside legality, vehicle stop feasibility, curb height, deployment clearance, sidewalk width, dynamic occupancy, availability, and confidence. For `--pudo_source evidence_jsonl`, rows are used as audited PUDO candidates directly and must include a curb pose through `curb_pose`, `curb_x/curb_y`, `x/y`, or point/polyline `geometry`; route-derived PUDO candidates are used only when `--pudo_source nuplan_route`.

## Passenger-service layer build

```bash
python scripts/build_service_layer.py \
  --scene_dataset_dir /data/capplan/scenes \
  --accessibility_graph_dir outputs/prepared/accessibility_graphs \
  --service_requests_jsonl /data/prepared/service_requests.jsonl \
  --capability_profiles_jsonl /data/prepared/capability_profiles.jsonl \
  --fleet_jsonl /data/prepared/fleet_interfaces.jsonl \
  --output_service_requests_jsonl outputs/prepared/service_requests.validated.jsonl \
  --report_json outputs/prepared/service_layer_report.json
```

Each request must bind a real `episode_id`, origin entrance, destination entrance, request time, passenger profile, and fleet vehicle. Capability profiles may be JSONL or YAML and can include mobility, interface, resource, uncertainty, and same-scene counterfactual fields.

## Paper-mode dataset build

```bash
python scripts/build_dataset.py \
  --paper_mode \
  --scene_source nuplan \
  --nuplan_data_root /data/nuplan/data/cache \
  --nuplan_map_root /data/nuplan/maps \
  --nuplan_db_root /data/nuplan/data/cache \
  --nuplan_db_dirs train_boston train_pittsburgh \
  --nuplan_map_version nuplan-maps-v1.0 \
  --split train \
  --max_scenarios 100 \
  --accessibility_source prepared_jsonl \
  --accessibility_graph_dir outputs/prepared/accessibility_graphs \
  --pudo_source evidence_jsonl \
  --pudo_evidence_jsonl outputs/prepared/pudo_evidence.jsonl \
  --service_layer_source real_jsonl \
  --service_requests_jsonl outputs/prepared/service_requests.validated.jsonl \
  --capability_profiles_jsonl /data/prepared/capability_profiles.jsonl \
  --fleet_jsonl /data/prepared/fleet_interfaces.jsonl \
  --reject_synthetic_accessibility \
  --reject_proxy_entrances \
  --min_graph_nodes 100 \
  --min_graph_edges 150 \
  --max_core_pudo_missing_rate 0.05 \
  --min_edge_positive_rate 0.10 \
  --min_skeleton_positive_rate 0.10 \
  --output_dir outputs/datasets/abilitybench_av_train \
  --strict
```

## Dataset validation and audit

```bash
python scripts/validate_dataset.py \
  --dataset_dir outputs/datasets/abilitybench_av_train \
  --strict

python scripts/audit_dataset_quality.py \
  --dataset_dir outputs/datasets/abilitybench_av_train \
  --paper_mode \
  --fail_if_not_publication_ready \
  --min_graph_nodes 100 \
  --min_graph_edges 150 \
  --max_core_pudo_missing_rate 0.05 \
  --min_edge_positive_rate 0.10 \
  --min_skeleton_positive_rate 0.10
```

Paper-mode audit fails on proxy/synthetic sources, toy graphs, high core PUDO missingness, low positive label rate, profile sets with no feasible positives, and untrained phase/demand supervision.

## CASA-Net training

Smoke/CI linear path:

```bash
python scripts/train_casa.py \
  --dataset_dir outputs/datasets/synthetic_smoke \
  --output_dir outputs/models/casa_smoke \
  --epochs 2 \
  --batch_size 16 \
  --device cpu \
  --model_type linear_smoke
```

Paper-mode multi-head CASA path:

```bash
python scripts/train_casa.py \
  --paper_mode \
  --dataset_dir outputs/datasets/abilitybench_av_train \
  --output_dir outputs/models/casa_paper_hgt \
  --epochs 20 \
  --batch_size 64 \
  --lr 1e-3 \
  --device auto \
  --model_type hgt \
  --phase_supervision \
  --predict_typed_demand \
  --predict_uncertainty \
  --predict_availability \
  --value_target offline_tsbs \
  --profile_balanced_sampler \
  --action_balanced_sampler \
  --save_calibration_report
```

The implemented paper path trains multi-head edge, value, phase, typed-demand, calibration, uncertainty, and availability heads over the current transition feature schema. The full heterogeneous graph message-passing architecture still requires integration with PyG/DGL or a nuPlan-scale graph backend before claiming final paper-model parity.

## nuPlan closed-loop main evaluation

```bash
python scripts/run_closed_loop_eval.py \
  --paper_mode \
  --dataset_dir outputs/datasets/abilitybench_av_train \
  --output_dir outputs/eval/nuplan_closed_loop_full \
  --planner capplan \
  --ablation full \
  --trajectory_mode nuplan_closed_loop \
  --nuplan_sim_config configs/nuplan_closed_loop.yaml \
  --casa_mode learned \
  --casa_checkpoint outputs/models/casa_paper_hgt/checkpoint.pt \
  --vehicle_metrics outputs/eval/nuplan_closed_loop_full/vehicle_metrics.json \
  --passenger_metrics outputs/eval/nuplan_closed_loop_full/passenger_metrics.json
```

Without a configured nuPlan simulation environment, `nuplan_closed_loop` raises a clear error. `mock_strict` must not be reported as nuPlan closed-loop.

## Baselines

Use `--ablation full` for CapPlan full, and configure comparison planners in the experiment harness as separate output directories for:

```text
standard_av
nearest_legal_stop
accessibility_only
resource_constrained_route_planner
comfort_planner
motion_sickness
preference_weighted
capplan_full
```

The repository currently contains CapPlan and ablation switches. External baselines must provide their planner outputs or wrappers; they are not silently simulated by CapPlan.

## Ablations

Every single-run evaluation supports:

```bash
python scripts/run_closed_loop_eval.py \
  --dataset_dir outputs/datasets/synthetic_smoke \
  --output_dir outputs/eval/one_ablation \
  --trajectory_mode mock_strict \
  --ablation no_typed_resource_ledger
```

All paper/smoke ablation names:

```text
--ablation full
--ablation no_capability_compiler
--ablation no_service_automaton
--ablation no_casa_net_transitions
--ablation no_typed_resource_ledger
--ablation no_conservative_margins
--ablation no_completion_value_guidance
--ablation soft_only_capability
```

Batch ablation table:

```bash
python scripts/run_ablations.py \
  --dataset_dir outputs/datasets/synthetic_smoke \
  --output_dir outputs/eval/ablations_synthetic \
  --trajectory_mode mock_strict
```

Paper-mode batch ablations:

```bash
python scripts/run_ablations.py \
  --paper_mode \
  --dataset_dir outputs/datasets/abilitybench_av_train \
  --output_dir outputs/eval/ablations_paper \
  --trajectory_mode nuplan_closed_loop \
  --nuplan_sim_config configs/nuplan_closed_loop.yaml \
  --casa_mode learned \
  --casa_checkpoint outputs/models/casa_paper_hgt/checkpoint.pt \
  --variants full no_capability_compiler no_service_automaton no_casa_net_transitions no_typed_resource_ledger no_conservative_margins no_completion_value_guidance soft_only_capability
```

## Dataset layout

```text
outputs/datasets/<name>/
  dataset_manifest.json
  splits/train_episodes.txt
  splits/val_episodes.txt
  splits/test_episodes.txt
  scenes.jsonl
  episodes.jsonl
  entrances.jsonl
  accessibility_graphs/<episode_id>.nodes.jsonl
  accessibility_graphs/<episode_id>.edges.jsonl
  pudo_anchors.jsonl
  vehicle_interfaces.jsonl
  service_requests.jsonl
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

## Metrics

Evaluation reports vehicle metrics `CR`, `RC`, `TRV`, `TT`, `DR`; passenger-complete metrics `PCR`, `TSPIR`, `PAR`, `CVR`, `CSM`, `FLF`, `BAF`, `MER`, `MVR`, `SBR`, `IR`; diagnostic metrics `DF`, `SME`, `CRsp`; and efficiency metric `ECA`.

Passenger completion requires accepted service phase, vehicle safety, and capability satisfaction.

## Current implementation status

Implemented now:

- source-separated smoke/paper command paths;
- prepared real accessibility graph ingestion and audit checks;
- PUDO evidence ingestion and core-missingness checks;
- passenger service request/profile/fleet loaders;
- profile-to-contract binding with same-scene counterfactual support in the schema path;
- multi-head CASA training path with phase, edge, demand, calibration, value, uncertainty, and availability outputs;
- command-line ablation switches for all required variants;
- fail-fast nuPlan closed-loop entry point.

Not yet paper-main-ready without additional integration/data:

- prepared real graph/evidence/service/fleet/profile files must be provided;
- the nuPlan simulation runner must be configured in a real nuPlan environment;
- external baseline planner wrappers/results must be connected;
- the CASA model is currently a multi-head transition-feature network, not yet a full production heterogeneous graph message-passing implementation.

### AbilityBench-AV main GIS/service construction

For paper-mode data construction, the recommended benchmark decomposition is:

```text
AbilityBench-AV-main = map information + passenger information
map information = nuPlan scenes + nuPlan HD map + OSM/OpenSidewalks/city sidewalk GIS + curb/PUDO regulation/evidence
passenger information = calibrated passenger service requests + three-layer capability profiles
```

The two groups are stored separately and composed at dataset-build time.  The map layer is strongly coupled internally through common episode IDs, route corridors, local-map coordinates, snapped entrances, pedestrian topology, curb/PUDO candidates, and evidence provenance.  The passenger layer is strongly coupled internally through service requests that reference explicit profile IDs.  A passenger profile is not baked into a map; instead, the executable transition predicates combine one service request/profile with one map scene to test passenger-complete feasibility.

The GIS fusion builder accepts local prepared records, Overpass/OSM JSON, OpenSidewalks-compatible GeoJSON/JSONL, and city GIS exports.  It requires an explicit georeference when WGS84 GIS layers must be transformed into the nuPlan local map frame:

```bash
python scripts/build_accessibility_graphs.py \
  --scene_dataset_dir outputs/scenes \
  --georeference_json configs/georeference_boston.json \
  --osm_source data/osm/overpass_boston.json \
  --opensidewalks_source data/opensidewalks/boston.geojson \
  --city_gis_dir data/city_gis/boston \
  --curb_inventory_source data/curbs/boston_curbs.geojson \
  --entrance_source data/entrances/boston_entrances.geojson \
  --elevation_source data/dem/boston_elevation_points.jsonl \
  --output_graph_dir outputs/accessibility_graphs \
  --fail_on_synthetic
```

The PUDO evidence generator consumes those graphs plus curb regulation/evidence.  It marks route-corridor curb/curb-ramp nodes as PUDO candidates, snaps them to pedestrian nodes, fuses regulation evidence, and fails closed when no legal-stop regulation matches:

```bash
python scripts/build_pudo_evidence.py \
  --scene_dataset_dir outputs/scenes \
  --accessibility_graph_dir outputs/accessibility_graphs \
  --curb_regulation_jsonl data/curbs/curb_regulations.jsonl \
  --curb_inventory_jsonl data/curbs/curb_inventory.jsonl \
  --output_pudo_evidence_jsonl outputs/pudo_evidence.jsonl \
  --fail_on_missing_core_evidence
```

The service-layer builder can validate existing real/calibrated requests, or sample calibrated OD requests from real entrance nodes.  If no profile file is supplied, it writes the three benchmark profiles: `basic_service_complete`, `mobility_interface_constrained`, and `compound_uncertainty_sensitive`.

```bash
python scripts/build_service_layer.py \
  --accessibility_graph_dir outputs/accessibility_graphs \
  --demand_sources_config configs/demand_calibration.yaml \
  --output_service_requests_jsonl outputs/service_requests.jsonl \
  --output_capability_profiles_jsonl outputs/capability_profiles.jsonl \
  --num_requests_per_episode 3
```
