#!/usr/bin/env bash
set -euo pipefail

CAP_HOME="${CAP_HOME:-/home/senzeyu2/code/CapPlan}"
DATA_ROOT="${DATA_ROOT:-/data0/senzeyu2/dataset/CapPlan/data}"
CONFIG="${CONFIG:-$CAP_HOME/configs/abilitybench_nuplan_real_data0.yaml}"
EXT="$DATA_ROOT/external"
REPORTS="$EXT/reports"

mkdir -p "$REPORTS/commands" "$REPORTS/build" "$REPORTS/model" "$REPORTS/eval"
cd "$CAP_HOME"

runlog() {
  local name="$1"; shift
  echo "===== ${name} ====="
  "$@" 2>&1 | tee "$REPORTS/commands/${name}.log"
}

migrate() {
  mkdir -p "$DATA_ROOT"
  rsync -aH --info=progress2 "$CAP_HOME/data/" "$DATA_ROOT/"
}

inspect_nuplan() {
  for split in train val test; do
    runlog "nuplan_db_cities.${split}" python scripts/inspect_nuplan_db_cities.py \
      --config "$CONFIG" --split "$split" --fail_on_unknown \
      --report_json "$REPORTS/nuplan_db_cities.${split}.json"
  done
  runlog "nuplan_map_crs" python scripts/inspect_nuplan_map_crs.py \
    --config "$CONFIG" --cities boston+pittsburgh+vegas+singapore \
    --output_dir "$EXT/georeference"
}

prepare_osm() {
  runlog osm_boston python scripts/prepare_osm_from_pbf.py \
    --input_pbf "$EXT/raw/osm_pbf/massachusetts-latest.osm.pbf" \
    --bbox 42.30,-71.15,42.42,-70.98 \
    --output "$EXT/normalized/osm/boston_sidewalks.geojson" --overwrite
  runlog osm_pittsburgh python scripts/prepare_osm_from_pbf.py \
    --input_pbf "$EXT/raw/osm_pbf/pennsylvania-latest.osm.pbf" \
    --bbox 40.38,-80.04,40.48,-79.88 \
    --output "$EXT/normalized/osm/pittsburgh_sidewalks.geojson" --overwrite
  runlog osm_vegas python scripts/prepare_osm_from_pbf.py \
    --input_pbf "$EXT/raw/osm_pbf/nevada-latest.osm.pbf" \
    --bbox 36.055,-115.23,36.20,-115.10 \
    --output "$EXT/normalized/osm/vegas_sidewalks.geojson" --overwrite
  runlog osm_singapore python scripts/prepare_osm_from_pbf.py \
    --input_pbf "$EXT/raw/osm_pbf/malaysia-singapore-brunei-latest.osm.pbf" \
    --bbox 1.27,103.75,1.33,103.82 \
    --output "$EXT/normalized/osm/singapore_sidewalks.geojson" --overwrite
  runlog georeference_spatial_alignment python scripts/validate_georeference_alignment.py \
    --config "$CONFIG" --cities boston,pittsburgh,vegas,singapore \
    --min_map_covered_by_aoi 0.95 --min_aoi_covered_by_osm 0.95 --min_map_covered_by_osm 0.95 \
    --write_georeference --report_json "$REPORTS/georeference_spatial_alignment.json"
}

fetch_public() {
  runlog fetch_recommended_public_sources python scripts/fetch_recommended_public_sources.py \
    --config "$CONFIG" --cities boston,pittsburgh,vegas,singapore --strict
}

validate_dem_city() {
  local city="$1" res="$2" datum="$3" source="$4"
  local -a rasters=("$EXT/raw/dem/$city"/*.tif)
  if [[ ! -e "${rasters[0]}" ]]; then
    echo "No DEM .tif found for $city under $EXT/raw/dem/$city" >&2
    return 2
  fi
  runlog "dem_validate.${city}" python scripts/validate_dem_tiles.py \
    --config "$CONFIG" --city "$city" --rasters "${rasters[@]}" \
    --expected_resolution_m "$res" --min_coverage 0.99 \
    --report_json "$REPORTS/dem_tiles_${city}.json"
  runlog "dem_sample.${city}" python scripts/sample_raster_dem.py \
    --external_root "$EXT" --city "$city" --rasters "${rasters[@]}" \
    --vertical_datum "$datum" --source_name "$source" --nominal_resolution_m "$res" \
    --tile_validation_report "$REPORTS/dem_tiles_${city}.json" --include_city_gis
}

validate_dems() {
  validate_dem_city boston 1 NAVD88 USGS_3DEP_1m
  validate_dem_city pittsburgh 1 NAVD88 USGS_3DEP_1m
  validate_dem_city vegas 1 NAVD88 USGS_3DEP_1m
  validate_dem_city singapore 30 EGM2008 COPERNICUS_GLO30_DSM
}

bootstrap_preflight() {
  mkdir -p "$EXT/normalized/fleet"
  if [[ ! -s "$EXT/normalized/fleet/vehicle_interfaces.jsonl" ]]; then
    cp "$CAP_HOME/configs/fleet.abilitybench.example.jsonl" "$EXT/normalized/fleet/vehicle_interfaces.jsonl"
    echo "WARNING: installed example fleet for bootstrap only; replace before paper preflight." >&2
  fi
  runlog external.bootstrap python scripts/validate_external_sources.py \
    --config "$CONFIG" --cities boston,pittsburgh,vegas,singapore \
    --source_policy bootstrap --output "$REPORTS/external.bootstrap.json"
}

bootstrap_pilot() {
  runlog bootstrap.val.4city10 python scripts/prepare_abilitybench_external.py \
    --config "$CONFIG" --split val --source_policy bootstrap \
    --cities boston+pittsburgh+vegas+singapore --max_scenarios_per_city 10 \
    --stages map_crs,preflight,extract,graphs,pudo,service,dataset,merge
}

export_audits() {
  for city in boston pittsburgh vegas singapore; do
    mkdir -p "$EXT/audits/$city"
    runlog "manual_audit_shortlist.val.${city}" python scripts/export_pudo_audit_shortlist.py \
      --pudo_evidence_jsonl "$DATA_ROOT/outputs/prepared/val/pudo/${city}.jsonl" \
      --georeference_json "$EXT/georeference/${city}.json" --city "$city" \
      --max_candidates_per_episode 4 --dedup_radius_m 5 \
      --output_csv "$EXT/audits/${city}/pudo_audit_shortlist.csv" \
      --report_json "$REPORTS/manual_audit_shortlist.val.${city}.json"
  done
}

build_audits() {
  for city in boston pittsburgh vegas singapore; do
    runlog "manual_audit_layers.${city}" python scripts/build_manual_audit_layers.py \
      --input_csv "$EXT/audits/${city}/pudo_audit_shortlist.csv" --city "$city" \
      --external_root "$EXT" --report_json "$REPORTS/manual_audit_layers.${city}.json"
  done
}

build_provenance() {
  if [[ ! -s "$EXT/provenance_registry.yaml" ]]; then
    echo "Missing $EXT/provenance_registry.yaml. Copy and REVIEW the data0 paper template first." >&2
    return 2
  fi
  mkdir -p "$EXT/manifests"
  for city in boston pittsburgh vegas singapore; do
    runlog "provenance.${city}" python scripts/build_provenance_manifest.py \
      --registry "$EXT/provenance_registry.yaml" --city "$city" \
      --output "$EXT/manifests/${city}.json"
  done
}

paper_preflight() {
  runlog external.paper python scripts/validate_external_sources.py \
    --config "$CONFIG" --cities boston,pittsburgh,vegas,singapore \
    --source_policy paper --output "$REPORTS/external.paper.json"
}

paper_pilot() {
  runlog paper.val.4city20 python scripts/prepare_abilitybench_external.py \
    --config "$CONFIG" --split val --source_policy paper \
    --cities boston+pittsburgh+vegas+singapore --max_scenarios_per_city 20 \
    --stages preflight,extract,graphs,pudo,service,dataset,merge
}

paper_full() {
  for split in train val test; do
    runlog "paper.${split}.all" python scripts/prepare_abilitybench_external.py \
      --config "$CONFIG" --split "$split" --source_policy paper \
      --cities boston+pittsburgh+vegas+singapore --max_scenarios_per_city 0 \
      --stages preflight,extract,graphs,pudo,service,dataset,merge
  done
}

merge_all() {
  runlog merge.abilitybench_av_all python scripts/merge_datasets.py \
    --input_dirs \
      "$DATA_ROOT/outputs/datasets/abilitybench_av_train" \
      "$DATA_ROOT/outputs/datasets/abilitybench_av_val" \
      "$DATA_ROOT/outputs/datasets/abilitybench_av_test" \
    --output_dir "$DATA_ROOT/outputs/datasets/abilitybench_av_all" --strict
  runlog validate.abilitybench_av_all python scripts/validate_dataset.py \
    --dataset_dir "$DATA_ROOT/outputs/datasets/abilitybench_av_all" --strict
  runlog audit.abilitybench_av_all python scripts/audit_dataset_quality.py \
    --dataset_dir "$DATA_ROOT/outputs/datasets/abilitybench_av_all" \
    --paper_mode --fail_if_not_publication_ready \
    --output "$REPORTS/dataset_quality.paper.all.json"
}

train_surrogate() {
  local out="$DATA_ROOT/outputs/models/casa_relation_surrogate"
  runlog train_casa_relation_surrogate python scripts/train_casa.py \
    --dataset_dir "$DATA_ROOT/outputs/datasets/abilitybench_av_all" \
    --output_dir "$out" --epochs 50 --batch_size 256 --lr 1e-3 \
    --model_type hgt --paper_mode --phase_supervision --predict_typed_demand \
    --predict_uncertainty --predict_availability --value_target skeleton \
    --profile_balanced_sampler --action_balanced_sampler --save_calibration_report
  mkdir -p "$REPORTS/model/casa_relation_surrogate"
  for f in config.json val_metrics.json calibration_report.json train_metrics.jsonl; do
    [[ -e "$out/$f" ]] && cp "$out/$f" "$REPORTS/model/casa_relation_surrogate/$f"
  done
  echo "NOTE: current hgt/rgcn backend is a relation-aware surrogate, not full graph message passing." >&2
}

usage() {
  cat <<USAGE
Usage: $0 <stage>
Stages:
  migrate
  inspect-nuplan
  prepare-osm
  fetch-public
  validate-dems
  bootstrap-preflight
  bootstrap-pilot
  export-audits
  build-audits
  build-provenance
  paper-preflight
  paper-pilot
  paper-full
  merge-all
  train-surrogate
  auto-bootstrap   # inspect + osm + fetch + dem + bootstrap preflight + pilot
USAGE
}

case "${1:-}" in
  migrate) migrate ;;
  inspect-nuplan) inspect_nuplan ;;
  prepare-osm) prepare_osm ;;
  fetch-public) fetch_public ;;
  validate-dems) validate_dems ;;
  bootstrap-preflight) bootstrap_preflight ;;
  bootstrap-pilot) bootstrap_pilot ;;
  export-audits) export_audits ;;
  build-audits) build_audits ;;
  build-provenance) build_provenance ;;
  paper-preflight) paper_preflight ;;
  paper-pilot) paper_pilot ;;
  paper-full) paper_full ;;
  merge-all) merge_all ;;
  train-surrogate) train_surrogate ;;
  auto-bootstrap)
    inspect_nuplan
    prepare_osm
    fetch_public
    validate_dems
    bootstrap_preflight
    bootstrap_pilot
    ;;
  *) usage; exit 2 ;;
esac
