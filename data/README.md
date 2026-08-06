# CapPlan data root

All nuPlan, external GIS, manual audit, provenance, intermediate and dataset outputs are rooted here.

Create the directory tree:

```bash
python scripts/create_data_layout.py
```

Core gates:

```bash
python scripts/validate_external_sources.py \
  --config configs/abilitybench_nuplan_real.yaml \
  --source_policy bootstrap

python scripts/validate_external_sources.py \
  --config configs/abilitybench_nuplan_real.yaml \
  --source_policy paper
```

Do not put HTML error pages or zero-byte placeholders under `normalized/`.
Do not rename ordinary OSM GeoJSON as OpenSidewalks. Only place a dataset under
`normalized/opensidewalks/` after schema validation.

See `docs/CapPlan数据集准备与复现实验指南.md` for the complete four-city workflow.
