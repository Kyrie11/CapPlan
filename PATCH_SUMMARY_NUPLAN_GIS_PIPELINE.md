# nuPlan + GIS + passenger pipeline review patch

This patch addresses the remaining dataset-construction gap for a real AbilityBench-AV-main small-sanity pipeline.

## Main changes

1. Added `scripts/extract_nuplan_scenes.py`.
   - Extracts `scenes.jsonl`, `episodes.jsonl`, map names, route corridors, and agent histories from real nuPlan DB sets before GIS fusion.
   - This removes the chicken-and-egg problem where GIS fusion needed scene route corridors but full dataset build needed prebuilt GIS graphs.

2. Extended `capplan/data/nuplan_adapter.py` and `scripts/build_dataset.py`.
   - Added optional `--nuplan_map_names`, `--nuplan_scenario_types`, and `--nuplan_log_names` filters.
   - Added global fleet support through `episode_id="*"` in `fleet_jsonl`; final build materializes the vehicle interface for each episode.
   - Paper mode now fails instead of silently falling back to generated vehicle profiles when no fleet row exists.

3. Enhanced `scripts/diagnose_capplan_outputs.py`.
   - Added optional checks for external accessibility graph dirs, external service requests, and external PUDO evidence.
   - Reports graph connectivity, component size, dangling edge references, node-kind distribution, edge-type distribution, width/slope missingness, PUDO legal-stop rate, service profile coverage, and readiness status.

4. Added example configs:
   - `configs/georeference.boston.example.json`
   - `configs/georeference.pittsburgh.example.json`
   - `configs/demand.small_sanity.example.yaml`
   - `configs/fleet.abilitybench.example.jsonl`

5. Added `pyproj>=3.6` to `requirements.txt` for exact WGS84/projected CRS transforms.

## Verification run in this environment

```bash
python -m py_compile scripts/extract_nuplan_scenes.py scripts/build_accessibility_graphs.py scripts/build_pudo_evidence.py scripts/build_service_layer.py scripts/build_dataset.py scripts/diagnose_capplan_outputs.py capplan/data/nuplan_adapter.py capplan/data/gis_fusion.py capplan/data/passenger_service_layer.py
python scripts/build_dataset.py --scene_source synthetic --max_scenarios 2 --accessibility_source synthetic_local --service_layer_source synthetic_smoke --num_contracts_per_scene 2 --output_dir /mnt/data/capplan_smoke_check --seed 13 --disable_tqdm
python scripts/diagnose_capplan_outputs.py --dataset_dir /mnt/data/capplan_smoke_check --output /mnt/data/capplan_smoke_diag.json
python -m pytest tests/test_gis_fusion_builders.py tests/test_nuplan_adapter_modes.py -vv -s
```

The smoke build wrote 2 episodes / 4 contracts / 110 transitions with zero schema validation errors. The full pytest suite was not rerun to completion here because it exceeded the interactive timeout, but targeted GIS/nuPlan adapter tests passed.
