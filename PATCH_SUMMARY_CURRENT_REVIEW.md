# CapPlan current-review patch summary

This patch focuses on correctness/truthfulness rather than improving headline metrics.

## Changed files

- `capplan/models/casa_features.py`
  - Adds passenger capability token features via `encode_capability_tokens`.
  - Adds `encode_transition_with_capability` so learned CASA can condition on compiled passenger contracts.

- `capplan/models/casa_dataset.py`
  - Changes CASA training samples from one row per transition to one row per `(transition, passenger contract)`.
  - Uses `passenger_edge_labels.y_e_p` as the edge target when available, instead of transition-only `z_e`.
  - Uses skeleton membership as a stronger value target.

- `capplan/models/predictors.py`
  - Makes learned inference use the same capability-conditioned feature vector as training.

- `capplan/data/accessibility_layer.py`
  - Marks synthetic direct PUDO connector edges as curb-context edges.
  - Aggregates curb-ramp evidence on any path edge touching PUDO/curb nodes, avoiding false positive defaults when curb evidence is absent.

- `scripts/diagnose_capplan_outputs.py`
  - Adds a post-build/post-eval diagnostic report for dataset scale, graph scale, passenger label balance, skeleton positives, certificate phases/resources, and evaluation summary.

## Smoke validation run locally

```bash
python scripts/build_dataset.py --scene_source synthetic --max_scenarios 6 --num_contracts_per_scene 4 --output_dir /mnt/data/work/smoke_patched --seed 13 --strict --disable_tqdm
python scripts/validate_dataset.py --dataset_dir /mnt/data/work/smoke_patched --strict
python scripts/train_casa.py --dataset_dir /mnt/data/work/smoke_patched --output_dir /mnt/data/work/casa_patched --epochs 2 --batch_size 16 --device cpu
python scripts/run_closed_loop_eval.py --dataset_dir /mnt/data/work/smoke_patched --output_dir /mnt/data/work/eval_patched --trajectory_mode mock_strict --casa_mode learned --casa_checkpoint /mnt/data/work/casa_patched/checkpoint.pt
python -m pytest tests/test_casa_training_smoke.py tests/test_accessibility_graph_routing.py -q
```

Observed smoke result: validation OK; selected tests `6 passed`.

## Remaining major limitations

The current nuPlan command still uses proxy entrances, synthetic accessibility, route-derived PUDOs, and `mock_strict` vehicle evaluation. Those are not sufficient for paper-scale main results. Use the diagnostic script after rebuilding the nuPlan dataset to confirm graph scale, source provenance, and positive passenger-complete label rates.
