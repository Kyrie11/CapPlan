# CapPlan second-review patch summary

This patch is based only on the current uploaded code, current uploaded results, and the current paper TeX.

## Why this patch was needed

The latest run confirms that the previous passenger-conditioned CASA dataset change took effect: validation samples are now passenger-transition level. However, the dataset remains non-paper-ready because the nuPlan scene layer is still paired with synthetic accessibility/proxy entrance evidence, route-derived PUDOs miss curb geometry, and positive passenger-complete skeletons remain too sparse and profile-imbalanced.

## Code changes

### 1. Prepared accessibility graph support

Files:
- `capplan/data/accessibility_layer.py`
- `scripts/build_dataset.py`

Added `PreparedAccessibilityBuilder` and CLI support:

```bash
--accessibility_source prepared_jsonl
--accessibility_graph_dir /path/to/accessibility_graphs
```

The builder loads either:

```text
<episode_id>.nodes.jsonl + <episode_id>.edges.jsonl
```

or shared:

```text
nodes.jsonl + edges.jsonl
```

This is the intended route away from synthetic accessibility overlays. Missing evidence remains missing and fails through capability uncertainty logic.

### 2. Audited PUDO evidence override support

File:
- `scripts/build_dataset.py`

Added:

```bash
--pudo_evidence_jsonl /path/to/pudo_evidence.jsonl
```

Rows may override fields such as:

```text
curb_height_m, sidewalk_width_m, deployment_clearance_m, legal_stop, side,
lighting, shelter, map_confidence, dynamic_confidence, blockage_risk, source
```

This addresses the current result where every route-derived PUDO has missing `curb_height_m`, and many have missing sidewalk width/deployment clearance.

### 3. Sparse passenger-edge training metrics and weighted edge learning

File:
- `scripts/train_casa.py`

Added automatic positive-class weighting:

```bash
--edge_pos_weight auto
```

Added validation metrics beyond misleading raw accuracy:

```text
edge_balanced_accuracy
edge_precision
edge_recall
edge_f1
edge_true_positive_rate
edge_pred_positive_rate
edge_pos_weight
```

This matters because the current uploaded result has passenger edge positive rate near 1.56%; raw accuracy alone is not meaningful.

### 4. Evaluation/diagnostic failure-phase reporting fix

Files:
- `capplan/evaluation/closed_loop.py`
- `scripts/diagnose_capplan_outputs.py`

Closed-loop episode rows now explicitly store:

```text
failure_phase, failure_resource, failure_source
```

The diagnostic script now falls back to certificate/oracle-certificate fields instead of reporting `None` for all failed rows.

## Local validation

Commands run locally:

```bash
python scripts/build_dataset.py --scene_source synthetic --max_scenarios 8 --num_contracts_per_scene 4 --output_dir /mnt/data/review_now/smoke2 --seed 13 --strict --disable_tqdm
python scripts/validate_dataset.py --dataset_dir /mnt/data/review_now/smoke2 --strict
python scripts/train_casa.py --dataset_dir /mnt/data/review_now/smoke2 --output_dir /mnt/data/review_now/casa2 --epochs 5 --batch_size 64 --device cpu
python scripts/run_closed_loop_eval.py --dataset_dir /mnt/data/review_now/smoke2 --output_dir /mnt/data/review_now/eval2 --trajectory_mode mock_strict --casa_mode learned --casa_checkpoint /mnt/data/review_now/casa2/checkpoint.pt
python scripts/diagnose_capplan_outputs.py --dataset_dir /mnt/data/review_now/smoke2 --eval_dir /mnt/data/review_now/eval2 --output /mnt/data/review_now/diag2.json
python -m pytest -q
```

Observed result:

```text
validation OK
69 tests passed
```

## Remaining limitations

This patch does not fabricate real accessibility data. The next meaningful improvement requires supplying prepared/audited accessibility graphs and/or PUDO evidence. Without those, nuPlan scenes remain vehicle-real but passenger-service evidence is still synthetic/proxy and should not be reported as main paper results.
