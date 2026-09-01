# GIS fusion / PUDO / service layer implementation summary

This patch converts the previous prepared-JSONL-only paper-mode scaffolding into a deterministic file-first construction pipeline for `AbilityBench-AV-main = nuPlan scenes + nuPlan HD map + OSM/OpenSidewalks/city sidewalk GIS + curb/PUDO regulation/evidence + calibrated passenger service requests + three-layer capability profiles`.

## New/updated implementation

1. `capplan/data/gis_fusion.py`
   - Adds explicit WGS84 <-> nuPlan local map-frame conversion with either local ENU georeference (`origin_lat`, `origin_lon`, `origin_heading_deg`) or optional `pyproj` CRS transforms.
   - Reads OSM/Overpass JSON, OpenSidewalks/GeoJSON/JSONL, city GIS, curb inventory, entrance, and elevation exports.
   - Crops per scenario using route-corridor bbox from `scenes.jsonl` / `episodes.jsonl`.
   - Converts pedestrian features into accessibility nodes/edges.
   - Snaps entrances and curb/curb-ramp points to pedestrian topology.
   - Cleans duplicate/self-loop edges and marks route-corridor PUDO connector candidates.

2. `scripts/build_accessibility_graphs.py`
   - Keeps backward-compatible prepared JSONL validation mode.
   - Adds true GIS fusion mode from OSM/OpenSidewalks/city GIS layers.
   - Writes per-episode `<episode>.nodes.jsonl`, `<episode>.edges.jsonl`, `<episode>.jsonl`, plus source/quality reports.

3. `scripts/build_pudo_evidence.py`
   - Moves from validator-only behavior to candidate generation from accessibility graphs and route corridors.
   - Fuses curb regulation and curb inventory evidence.
   - Uses legal-stop fail-closed semantics when no regulation evidence matches.
   - Computes adjacent pedestrian node, route side, nearest sidewalk width, deployment clearance, curb height, blockage risk, confidence, and provenance where evidence exists.

4. `scripts/build_service_layer.py`
   - Keeps validation for materialized service requests.
   - Adds calibrated OD request generation from real entrance/transit-stop nodes.
   - Generates three functional passenger-capability profiles when a profile file is not supplied:
     - `basic_service_complete`
     - `mobility_interface_constrained`
     - `compound_uncertainty_sensitive`

5. `README.md`
   - Documents the map-information/passenger-information decomposition and the new GIS/PUDO/service commands.

6. Tests
   - Added `tests/test_gis_fusion_builders.py` covering GIS graph fusion, PUDO generation, and service/profile generation.

## Verification run

```bash
python -m py_compile capplan/data/gis_fusion.py scripts/build_accessibility_graphs.py scripts/build_pudo_evidence.py scripts/build_service_layer.py scripts/build_dataset.py scripts/train_casa.py
python -m pytest -q tests/test_gis_fusion_builders.py tests/test_readme_commands.py tests/test_nuplan_pudo_evidence.py tests/test_pudo_spatial_binding.py tests/test_dataset_labels.py
# 12 passed
python scripts/build_dataset.py --scene_source synthetic --max_scenarios 2 --accessibility_source synthetic_local --service_layer_source synthetic_smoke --num_contracts_per_scene 2 --output_dir /mnt/data/capplan_smoke_final --seed 13 --disable_tqdm
# Validation ok=True errors=0 warnings=0
```
