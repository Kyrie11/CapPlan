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
  local log="$REPORTS/commands/${name}.log"
  local start end elapsed rc
  mkdir -p "$REPORTS/commands"
  start=$(date +%s)
  echo "===== ${name} START $(date -Is) =====" | tee "$log"
  echo "[CAPPLAN_PROGRESS] CAP_NUM_WORKERS=${CAP_NUM_WORKERS:-config-default} command=$*" | tee -a "$log"
  set +e
  "$@" 2>&1 | tee -a "$log"
  rc=${PIPESTATUS[0]}
  set -e
  end=$(date +%s); elapsed=$((end-start))
  echo "===== ${name} END rc=${rc} elapsed_s=${elapsed} $(date -Is) =====" | tee -a "$log"
  return "$rc"
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

fetch_public_force() {
  runlog fetch_recommended_public_sources.force python scripts/fetch_recommended_public_sources.py \
    --config "$CONFIG" --cities boston,pittsburgh,vegas,singapore --strict --force
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


bootstrap_candidates_full() {
  # Pass 1: enumerate all matching nuPlan scenarios and build candidate
  # accessibility/PUDO evidence. Progress is intentionally enabled for full runs.
  # Set CAP_NUM_WORKERS (start with 4 or 8) to let nuPlan DB scenario discovery
  # use more CPU; graph/PUDO stages remain memory-bounded and resumable.
  for split in train val test; do
    echo "[CAPPLAN_PROGRESS] starting full bootstrap split=$split at $(date -Is)"
    runlog "bootstrap_candidates.${split}.all" python scripts/prepare_abilitybench_external.py \
      --config "$CONFIG" --split "$split" --source_policy bootstrap \
      --cities boston+pittsburgh+vegas+singapore --max_scenarios_per_city 0 \
      --stages preflight,extract,graphs,pudo
  done
}

bootstrap_performance_snapshot() {
  runlog "bootstrap_performance_snapshot" python scripts/summarize_bootstrap_performance.py \
    --data_root "$DATA_ROOT" --external_root "$EXT" \
    --output "$REPORTS/bootstrap_performance_snapshot.json"
}

run_city_stage_parallel() {
  # Usage: run_city_stage_parallel <split> <source_policy> <stage> <jobs> <log_prefix> [max_scenarios|config]
  # max_scenarios=config omits the CLI override and therefore uses the split cap
  # from configs/abilitybench_nuplan_real_data0.yaml.  This is the recommended
  # mode for expensive scene/graph/PUDO materialization.
  # Each worker owns one city. Wait for *whichever* city finishes first so a
  # slow Boston job cannot leave a free slot idle while a later city is ready.
  # Bash >=4.3 provides wait -n (the target server satisfies this).
  local split="$1" policy="$2" stage="$3" jobs="$4" prefix="$5" max_mode="${6:-0}"
  local -a cities=(boston pittsburgh vegas singapore)
  local city failed=0 active=0
  [[ "$jobs" =~ ^[1-9][0-9]*$ ]] || { echo "parallel jobs must be a positive integer, got: $jobs" >&2; return 2; }
  echo "[CAPPLAN_PROGRESS] staged split=$split policy=$policy stage=$stage city_jobs=$jobs $(date -Is)"
  for city in "${cities[@]}"; do
    while (( active >= jobs )); do
      if wait -n; then :; else failed=1; fi
      active=$((active - 1))
      if (( failed != 0 )); then
        echo "city worker failed split=$split stage=$stage; waiting for already-running workers" >&2
        while (( active > 0 )); do wait -n || true; active=$((active - 1)); done
        return 1
      fi
    done
    local -a cmd=(python scripts/prepare_abilitybench_external.py
        --config "$CONFIG" --split "$split" --source_policy "$policy"
        --cities "$city" --stages "$stage" --skip_preflight --skip_pudo_concat)
    if [[ "$max_mode" != "config" ]]; then
      cmd+=(--max_scenarios_per_city "$max_mode")
    fi
    ( runlog "${prefix}.${split}.${city}.${stage}" "${cmd[@]}" ) &
    active=$((active + 1))
  done
  while (( active > 0 )); do
    if wait -n; then :; else failed=1; fi
    active=$((active - 1))
  done
  (( failed == 0 )) || { echo "city worker failed split=$split stage=$stage" >&2; return 1; }
}

concat_split_pudo() {
  local split="$1" prefix="$2"
  runlog "${prefix}.${split}.concat" python scripts/concat_jsonl_files.py \
    --inputs \
      "$DATA_ROOT/outputs/prepared/$split/pudo/boston.jsonl" \
      "$DATA_ROOT/outputs/prepared/$split/pudo/pittsburgh.jsonl" \
      "$DATA_ROOT/outputs/prepared/$split/pudo/vegas.jsonl" \
      "$DATA_ROOT/outputs/prepared/$split/pudo/singapore.jsonl" \
    --output "$DATA_ROOT/outputs/prepared/$split/pudo_evidence.jsonl"
}

index_nuplan_full() {
  # Keep a complete, lightweight inventory of every matching scenario identity
  # in the immutable official split.  This preserves full-dataset provenance
  # without hydrating 20-step histories and multi-MB accessibility graphs for
  # every temporally correlated nuPlan snapshot.
  local split
  for split in train val test; do
    runlog "nuplan_scenario_index.${split}.all" python scripts/prepare_abilitybench_external.py \
      --config "$CONFIG" --split "$split" --source_policy bootstrap \
      --cities boston+pittsburgh+vegas+singapore --max_scenarios_per_city 0 \
      --stages index --skip_preflight --skip_pudo_concat
  done
}

bootstrap_candidates_paper_scale_staged() {
  # RECOMMENDED heavy-materialization pass.  Unlike the legacy "full" target,
  # this uses the configured split caps (train=1000/city, val/test=250/city by
  # default) and preserves the full official substrate through index-nuplan-full.
  # Increase the caps only if site/audit/paper-allowlist coverage is insufficient.
  local extract_jobs="${CAP_EXTRACT_CITY_JOBS:-1}"
  local graph_jobs="${CAP_GRAPH_CITY_JOBS:-2}"
  local pudo_jobs="${CAP_PUDO_CITY_JOBS:-4}"
  local split
  echo "[CAPPLAN_PROGRESS] paper-scale candidate materialization CAP_NUM_WORKERS=${CAP_NUM_WORKERS:-config-default} CAP_GRAPH_NUM_WORKERS=${CAP_GRAPH_NUM_WORKERS:-${CAP_NUM_WORKERS:-config-default}} extract_city_jobs=$extract_jobs graph_city_jobs=$graph_jobs pudo_city_jobs=$pudo_jobs"
  for split in train val test; do
    runlog "bootstrap_preflight.${split}.paper_scale" python scripts/prepare_abilitybench_external.py \
      --config "$CONFIG" --split "$split" --source_policy bootstrap \
      --cities boston+pittsburgh+vegas+singapore --stages preflight
    run_city_stage_parallel "$split" bootstrap extract "$extract_jobs" bootstrap_paper_scale config
    run_city_stage_parallel "$split" bootstrap graphs "$graph_jobs" bootstrap_paper_scale config
    run_city_stage_parallel "$split" bootstrap pudo "$pudo_jobs" bootstrap_paper_scale config
    concat_split_pudo "$split" bootstrap_paper_scale
    bootstrap_performance_snapshot
  done
}

bootstrap_candidates_full_staged() {
  # Recommended full-build scheduler from the 100-scene calibration:
  # extraction is storage-heavy, while graphs/PUDO are ~single-core CPU-bound.
  # Therefore serialize extraction by default and parallelize the CPU stages by city.
  local extract_jobs="${CAP_EXTRACT_CITY_JOBS:-1}"
  local graph_jobs="${CAP_GRAPH_CITY_JOBS:-2}"
  local pudo_jobs="${CAP_PUDO_CITY_JOBS:-4}"
  local split
  echo "[CAPPLAN_PROGRESS] staged bootstrap CAP_NUM_WORKERS=${CAP_NUM_WORKERS:-config-default} CAP_GRAPH_NUM_WORKERS=${CAP_GRAPH_NUM_WORKERS:-${CAP_NUM_WORKERS:-config-default}} extract_city_jobs=$extract_jobs graph_city_jobs=$graph_jobs pudo_city_jobs=$pudo_jobs"
  for split in train val test; do
    runlog "bootstrap_preflight.${split}.staged" python scripts/prepare_abilitybench_external.py \
      --config "$CONFIG" --split "$split" --source_policy bootstrap \
      --cities boston+pittsburgh+vegas+singapore --max_scenarios_per_city 0 --stages preflight
    run_city_stage_parallel "$split" bootstrap extract "$extract_jobs" bootstrap_staged
    run_city_stage_parallel "$split" bootstrap graphs "$graph_jobs" bootstrap_staged
    run_city_stage_parallel "$split" bootstrap pudo "$pudo_jobs" bootstrap_staged
    concat_split_pudo "$split" bootstrap_staged
    bootstrap_performance_snapshot
  done
}

rebuild_paper_evidence_full_staged() {
  # Scene extraction is unchanged between bootstrap and paper evidence passes.
  # Resume fingerprints invalidate only graphs/PUDO whose code/config/evidence
  # inputs actually changed, preventing mixed-version or stale-audit reuse.
  local graph_jobs="${CAP_GRAPH_CITY_JOBS:-2}"
  local pudo_jobs="${CAP_PUDO_CITY_JOBS:-4}"
  local split
  echo "[CAPPLAN_PROGRESS] staged paper evidence graph_city_jobs=$graph_jobs pudo_city_jobs=$pudo_jobs"
  for split in train val test; do
    runlog "paper_preflight.${split}.staged" python scripts/prepare_abilitybench_external.py \
      --config "$CONFIG" --split "$split" --source_policy paper \
      --cities boston+pittsburgh+vegas+singapore --max_scenarios_per_city 0 --stages preflight
    run_city_stage_parallel "$split" paper graphs "$graph_jobs" paper_evidence_staged
    run_city_stage_parallel "$split" paper pudo "$pudo_jobs" paper_evidence_staged
    concat_split_pudo "$split" paper_evidence_staged
    bootstrap_performance_snapshot
  done
}

bootstrap_runtime_summary() {
  local input="${CAP_PROFILE_LOG:-$REPORTS/bootstrap_runtime_profile.log}"
  [[ -s "$input" ]] || { echo "Missing profiler log: $input" >&2; return 2; }
  runlog "bootstrap_runtime_profile_summary" python scripts/summarize_bootstrap_runtime_profile.py \
    --input "$input" --output "$REPORTS/bootstrap_runtime_profile_summary.json"
}

bootstrap_candidates_full_parallel() {
  # Optional throughput mode for a large multi-socket server. Cities are fully
  # independent at candidate-build time, so run a bounded number concurrently.
  # Start conservatively because Boston/Singapore GIS layers are large.
  local jobs="${CAP_CITY_JOBS:-2}"
  local -a cities=(boston pittsburgh vegas singapore)
  local split city pid failed batch_count
  [[ "$jobs" =~ ^[1-9][0-9]*$ ]] || { echo "CAP_CITY_JOBS must be a positive integer" >&2; return 2; }
  echo "[CAPPLAN_PROGRESS] parallel city mode CAP_CITY_JOBS=$jobs CAP_NUM_WORKERS=${CAP_NUM_WORKERS:-config-default}"
  for split in train val test; do
    runlog "bootstrap_preflight.${split}.parallel" python scripts/prepare_abilitybench_external.py \
      --config "$CONFIG" --split "$split" --source_policy bootstrap \
      --cities boston+pittsburgh+vegas+singapore --max_scenarios_per_city 0 --stages preflight
    local -a batch_pids=()
    local -a batch_names=()
    batch_count=0
    failed=0
    for city in "${cities[@]}"; do
      ( runlog "bootstrap_candidates.${split}.${city}.all" python scripts/prepare_abilitybench_external.py \
          --config "$CONFIG" --split "$split" --source_policy bootstrap \
          --cities "$city" --max_scenarios_per_city 0 --stages extract,graphs,pudo \
          --skip_preflight --skip_pudo_concat ) &
      pid=$!
      batch_pids+=("$pid")
      batch_names+=("$city")
      batch_count=$((batch_count+1))
      if (( batch_count >= jobs )); then
        for pid in "${batch_pids[@]}"; do wait "$pid" || failed=1; done
        batch_pids=(); batch_names=(); batch_count=0
        (( failed == 0 )) || { echo "Parallel city batch failed for split=$split" >&2; return 1; }
      fi
    done
    for pid in "${batch_pids[@]}"; do wait "$pid" || failed=1; done
    (( failed == 0 )) || { echo "Parallel city batch failed for split=$split" >&2; return 1; }
    runlog "bootstrap_candidates.${split}.concat" python scripts/concat_jsonl_files.py \
      --inputs \
        "$DATA_ROOT/outputs/prepared/$split/pudo/boston.jsonl" \
        "$DATA_ROOT/outputs/prepared/$split/pudo/pittsburgh.jsonl" \
        "$DATA_ROOT/outputs/prepared/$split/pudo/vegas.jsonl" \
        "$DATA_ROOT/outputs/prepared/$split/pudo/singapore.jsonl" \
      --output "$DATA_ROOT/outputs/prepared/$split/pudo_evidence.jsonl"
  done
}


build_site_catalogs() {
  for city in boston pittsburgh vegas singapore; do
    mkdir -p "$EXT/audits/$city"
    runlog "paper_site_catalog.${city}" python scripts/build_pudo_site_catalog.py \
      --input "train=$DATA_ROOT/outputs/prepared/train/pudo/${city}.jsonl" \
      --input "val=$DATA_ROOT/outputs/prepared/val/pudo/${city}.jsonl" \
      --input "test=$DATA_ROOT/outputs/prepared/test/pudo/${city}.jsonl" \
      --georeference_json "$EXT/georeference/${city}.json" --city "$city" \
      --max_candidates_per_episode 4 --dedup_radius_m 5 \
      --output_csv "$EXT/audits/$city/pudo_site_catalog.csv" \
      --report_json "$REPORTS/paper_site_catalog.${city}.json" \
      --split_exclusion_json "$EXT/audits/$city/site_disjoint_exclusions.json"
  done
}

prefill_audit_worklists() {
  for city in boston pittsburgh vegas singapore; do
    runlog "pudo_audit_prefill.${city}" python scripts/prepare_pudo_audit_worklist.py \
      --input_csv "$EXT/audits/$city/pudo_site_catalog.csv" --city "$city" \
      --external_root "$EXT" \
      --output_csv "$EXT/audits/$city/pudo_audit_worklist.csv" \
      --report_json "$REPORTS/pudo_audit_prefill.${city}.json"
  done
}

classify_audits() {
  for city in boston pittsburgh vegas singapore; do
    runlog "pudo_audit_classify.${city}" python scripts/review_pudo_audit_worklist.py \
      --input_csv "$EXT/audits/$city/pudo_audit_worklist.csv" \
      --output_csv "$EXT/audits/$city/pudo_audit_review_status.csv" \
      --review_candidates_csv "$EXT/audits/$city/source_complete_review_candidates.csv" \
      --accepted_csv "$EXT/audits/$city/pudo_audit_source_accepted.csv" \
      --unresolved_csv "$EXT/audits/$city/pudo_audit_unresolved.csv" \
      --report_json "$REPORTS/pudo_audit_classify.${city}.json"
  done
}

triage_audits() {
  for city in boston pittsburgh vegas singapore; do
    runlog "pudo_audit_triage.${city}" python scripts/triage_pudo_audits.py \
      --input_csv "$EXT/audits/$city/pudo_audit_review_status.csv" \
      --output_csv "$EXT/audits/$city/pudo_audit_machine_triage.csv" \
      --machine_pass_csv "$EXT/audits/$city/machine_pass_explicit_authoritative.csv" \
      --machine_reject_csv "$EXT/audits/$city/machine_reject_invalid_or_ambiguous.csv" \
      --visual_review_csv "$EXT/audits/$city/visual_review_required.csv" \
      --new_evidence_csv "$EXT/audits/$city/new_evidence_required.csv" \
      --report_json "$REPORTS/pudo_audit_triage.${city}.json"
  done
}

render_audit_packets() {
  local max_rows="${CAP_AUDIT_RENDER_MAX_ROWS:-0}"
  local radius_m="${CAP_AUDIT_RENDER_RADIUS_M:-120}"
  for city in boston pittsburgh vegas singapore; do
    local csv="$EXT/audits/$city/visual_review_required.csv"
    if [[ ! -s "$csv" ]] || [[ $(wc -l < "$csv") -le 1 ]]; then
      echo "INFO: no visual-review rows for $city; skip packet rendering"
      continue
    fi
    runlog "pudo_audit_render.${city}" python scripts/render_pudo_audit_packets.py \
      --input_csv "$csv" --data_root "$DATA_ROOT" \
      --georeference_json "$EXT/georeference/${city}.json" \
      --output_dir "$EXT/audits/$city/visual_packets" \
      --radius_m "$radius_m" --max_rows "$max_rows" \
      --report_json "$REPORTS/pudo_audit_render.${city}.json"
  done
}

review_source_complete_audits() {
  : "${REVIEWER_ID:?Set REVIEWER_ID only after a reviewer has inspected source_complete_review_candidates.csv}"
  : "${CONFIRM_SOURCE_REVIEW:?Set CONFIRM_SOURCE_REVIEW=YES only after actual human review}"
  [[ "$CONFIRM_SOURCE_REVIEW" == "YES" ]] || { echo "CONFIRM_SOURCE_REVIEW must equal YES" >&2; return 2; }
  for city in boston pittsburgh vegas singapore; do
    local review_csv="$EXT/audits/$city/visual_review_required.csv"
    if [[ ! -s "$review_csv" ]]; then
      review_csv="$EXT/audits/$city/source_complete_review_candidates.csv"
    fi
    if [[ ! -s "$review_csv" ]] || [[ $(wc -l < "$review_csv") -le 1 ]]; then
      echo "INFO: no source-complete review candidates for $city; skip explicit source review"
      continue
    fi
    runlog "pudo_audit_source_review.${city}" python scripts/review_pudo_audit_worklist.py \
      --input_csv "$review_csv" \
      --output_csv "$EXT/audits/$city/pudo_audit_review_status.csv" \
      --review_candidates_csv "$review_csv" \
      --accepted_csv "$EXT/audits/$city/pudo_audit_source_accepted.csv" \
      --unresolved_csv "$EXT/audits/$city/pudo_audit_unresolved.csv" \
      --approve_source_complete --reviewer_id "$REVIEWER_ID" \
      --report_json "$REPORTS/pudo_audit_source_review.${city}.json"
  done
}

import_source_complete_audits() {
  for city in boston pittsburgh vegas singapore; do
    local csv="$EXT/audits/$city/pudo_audit_source_accepted.csv"
    if [[ -s "$csv" ]] && [[ $(wc -l < "$csv") -gt 1 ]]; then
      runlog "manual_audit_layers.source.${city}" python scripts/build_manual_audit_layers.py \
        --input_csv "$csv" --city "$city" --external_root "$EXT" --paper_mode \
        --report_json "$REPORTS/manual_audit_layers.source.${city}.json"
    else
      echo "INFO: no automatically source-complete audited rows for $city; unresolved rows remain in pudo_audit_unresolved.csv"
    fi
  done
}

import_completed_manual_audits() {
  # For facts that cannot be established from an authoritative source, populate
  # audits/<city>/pudo_audit_manual_completed.csv with actual observations.
  # observed_at and auditor_id must be factual; this stage never invents them.
  for city in boston pittsburgh vegas singapore; do
    local csv="$EXT/audits/$city/pudo_audit_manual_completed.csv"
    if [[ -s "$csv" ]] && [[ $(wc -l < "$csv") -gt 1 ]]; then
      runlog "manual_audit_layers.manual.${city}" python scripts/build_manual_audit_layers.py \
        --input_csv "$csv" --city "$city" --external_root "$EXT" --paper_mode \
        --report_json "$REPORTS/manual_audit_layers.manual.${city}.json"
    else
      echo "INFO: no completed manual audit CSV for $city at $csv"
    fi
  done
}

rebuild_paper_evidence_full() {
  # Pass 2: recompute graph/PUDO evidence against the frozen reviewed snapshot.
  for split in train val test; do
    runlog "paper_evidence.${split}.all" python scripts/prepare_abilitybench_external.py \
      --config "$CONFIG" --split "$split" --source_policy paper \
      --cities boston+pittsburgh+vegas+singapore --max_scenarios_per_city 0 \
      --stages preflight,graphs,pudo
  done
}

select_paper_allowlists() {
  # First select episodes purely by paper evidence/graph quality.  Then compute
  # physical-site leakage only among those preliminary episodes, so a bootstrap
  # candidate that can never enter the paper set does not unnecessarily delete
  # a lower-priority training episode.
  for city in boston pittsburgh vegas singapore; do
    mkdir -p "$EXT/audits/$city/paper_allowlists"
    for split in train val test; do
      runlog "paper_select_pre_site.${city}.${split}" python scripts/select_paper_episodes.py \
        --pudo_evidence_jsonl "$DATA_ROOT/outputs/prepared/$split/pudo/${city}.jsonl" \
        --accessibility_graph_dir "$DATA_ROOT/outputs/prepared/$split/accessibility_graphs" \
        --city "$city" --split "$split" \
        --min_paper_eligible_pudos 2 --min_distinct_pudo_sites 2 \
        --min_graph_nodes 100 --min_graph_edges 150 \
        --output_txt "$EXT/audits/$city/paper_allowlists/${split}.pre_site.txt" \
        --report_json "$REPORTS/paper_select_pre_site.${city}.${split}.json"
    done

    runlog "paper_anchor_leakage.${city}" python scripts/build_paper_anchor_leakage.py \
      --pudo_input "train=$DATA_ROOT/outputs/prepared/train/pudo/${city}.jsonl" \
      --pudo_input "val=$DATA_ROOT/outputs/prepared/val/pudo/${city}.jsonl" \
      --pudo_input "test=$DATA_ROOT/outputs/prepared/test/pudo/${city}.jsonl" \
      --graph_input "train=$DATA_ROOT/outputs/prepared/train/accessibility_graphs" \
      --graph_input "val=$DATA_ROOT/outputs/prepared/val/accessibility_graphs" \
      --graph_input "test=$DATA_ROOT/outputs/prepared/test/accessibility_graphs" \
      --allowlist "train=$EXT/audits/$city/paper_allowlists/train.pre_site.txt" \
      --allowlist "val=$EXT/audits/$city/paper_allowlists/val.pre_site.txt" \
      --allowlist "test=$EXT/audits/$city/paper_allowlists/test.pre_site.txt" \
      --city "$city" --pudo_radius_m 5 --entrance_radius_m 5 \
      --output_json "$EXT/audits/$city/paper_anchor_site_disjoint_exclusions.json"

    for split in train val test; do
      runlog "paper_select.${city}.${split}" python scripts/select_paper_episodes.py \
        --pudo_evidence_jsonl "$DATA_ROOT/outputs/prepared/$split/pudo/${city}.jsonl" \
        --accessibility_graph_dir "$DATA_ROOT/outputs/prepared/$split/accessibility_graphs" \
        --city "$city" --split "$split" \
        --site_exclusion_json "$EXT/audits/$city/paper_anchor_site_disjoint_exclusions.json" \
        --min_paper_eligible_pudos 2 --min_distinct_pudo_sites 2 \
        --min_graph_nodes 100 --min_graph_edges 150 \
        --output_txt "$EXT/audits/$city/paper_allowlists/${split}.txt" \
        --report_json "$REPORTS/paper_select.${city}.${split}.json"
    done
  done
}

paper_build_allowlisted() {
  # Different cities have different paper allowlists, so build city datasets
  # independently, then merge back into official train/val/test split names.
  for split in train val test; do
    for city in boston pittsburgh vegas singapore; do
      local allow="$EXT/audits/$city/paper_allowlists/${split}.txt"
      [[ -s "$allow" ]] || { echo "Empty/missing paper allowlist: $allow" >&2; return 2; }
      runlog "paper.${split}.${city}.allowlisted" python scripts/prepare_abilitybench_external.py \
        --config "$CONFIG" --split "$split" --source_policy paper --cities "$city" \
        --max_scenarios_per_city 0 --episode_allowlist "$allow" \
        --stages service,dataset --disable_tqdm
      if [[ -s "$REPORTS/build/$split/service_layer.json" ]]; then
        cp "$REPORTS/build/$split/service_layer.json" "$REPORTS/build/$split/service_layer.${city}.json"
      fi
    done
    runlog "paper.${split}.merge" python scripts/prepare_abilitybench_external.py \
      --config "$CONFIG" --split "$split" --source_policy paper \
      --cities boston+pittsburgh+vegas+singapore --max_scenarios_per_city 0 --stages merge
    runlog "paper.${split}.validate" python scripts/validate_dataset.py \
      --dataset_dir "$DATA_ROOT/outputs/datasets/abilitybench_av_${split}" --strict
    runlog "paper.${split}.audit" python scripts/audit_dataset_quality.py \
      --dataset_dir "$DATA_ROOT/outputs/datasets/abilitybench_av_${split}" \
      --paper_mode --fail_if_not_publication_ready \
      --output "$REPORTS/dataset_quality.paper.${split}.json"
  done
}

qa_snapshot() {
  runlog "four_city_paper_readiness" python scripts/check_four_city_paper_readiness.py \
    --config "$CONFIG" --reports_root "$REPORTS" \
    --output_json "$REPORTS/four_city_paper_readiness.json"
}

qa_strict() {
  runlog "four_city_paper_readiness.strict" python scripts/check_four_city_paper_readiness.py \
    --config "$CONFIG" --reports_root "$REPORTS" --require_allowlists --strict \
    --output_json "$REPORTS/four_city_paper_readiness.strict.json"
}

qa_bundle() {
  runlog "qa_bundle" python scripts/package_capplan_qa_bundle.py \
    --config "$CONFIG" --reports_root "$REPORTS" \
    --output_zip "$REPORTS/capplan_paper_qa_bundle.zip"
}

paper_full() {
  echo "paper-full is now the allowlisted second-pass build; run bootstrap-candidates-full -> site-catalogs -> prefill-audits -> classify-audits -> review/manual audit -> import -> provenance/paper-preflight -> rebuild-paper-evidence-full -> select-paper-allowlists first." >&2
  paper_build_allowlisted
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
  fetch-public-force       # repair/refresh all automatable public layers
  validate-dems
  bootstrap-preflight
  bootstrap-pilot
  bootstrap-candidates-full
  bootstrap-performance-snapshot
  bootstrap-candidates-full-parallel  # legacy whole-city parallel mode
  index-nuplan-full                   # full lightweight immutable scenario identity inventory
  bootstrap-candidates-paper-scale-staged # RECOMMENDED: config-capped heavy materialization
  bootstrap-candidates-full-staged    # legacy exhaustive heavy build; checkpointable but not recommended for paper sampling
  bootstrap-runtime-summary           # summarize profiler, excluding idle tail
  site-catalogs
  prefill-audits
  classify-audits
  triage-audits                       # deterministic reject/missing/visual/explicit-source buckets
  render-audit-packets                # offline topology packets for rows needing semantic review
  review-source-complete-audits
  import-source-complete-audits
  import-completed-manual-audits
  export-audits             # legacy pilot shortlist
  build-audits              # legacy pilot importer
  build-provenance
  paper-preflight
  paper-pilot
  rebuild-paper-evidence-full
  rebuild-paper-evidence-full-staged  # recommended paper evidence scheduler
  select-paper-allowlists
  paper-build-allowlisted
  paper-full                # alias for paper-build-allowlisted
  merge-all
  qa-snapshot
  qa-strict
  qa-bundle
  train-surrogate
  auto-bootstrap   # inspect + osm + fetch + dem + bootstrap preflight + pilot
USAGE
}

case "${1:-}" in
  migrate) migrate ;;
  inspect-nuplan) inspect_nuplan ;;
  prepare-osm) prepare_osm ;;
  fetch-public) fetch_public ;;
  fetch-public-force) fetch_public_force ;;
  validate-dems) validate_dems ;;
  bootstrap-preflight) bootstrap_preflight ;;
  bootstrap-pilot) bootstrap_pilot ;;
  bootstrap-candidates-full) bootstrap_candidates_full ;;
  bootstrap-performance-snapshot) bootstrap_performance_snapshot ;;
  bootstrap-candidates-full-parallel) bootstrap_candidates_full_parallel ;;
  index-nuplan-full) index_nuplan_full ;;
  bootstrap-candidates-paper-scale-staged) bootstrap_candidates_paper_scale_staged ;;
  bootstrap-candidates-full-staged) bootstrap_candidates_full_staged ;;
  bootstrap-runtime-summary) bootstrap_runtime_summary ;;
  site-catalogs) build_site_catalogs ;;
  prefill-audits) prefill_audit_worklists ;;
  classify-audits) classify_audits ;;
  triage-audits) triage_audits ;;
  render-audit-packets) render_audit_packets ;;
  review-source-complete-audits) review_source_complete_audits ;;
  import-source-complete-audits) import_source_complete_audits ;;
  import-completed-manual-audits) import_completed_manual_audits ;;
  export-audits) export_audits ;;
  build-audits) build_audits ;;
  build-provenance) build_provenance ;;
  paper-preflight) paper_preflight ;;
  paper-pilot) paper_pilot ;;
  rebuild-paper-evidence-full) rebuild_paper_evidence_full ;;
  rebuild-paper-evidence-full-staged) rebuild_paper_evidence_full_staged ;;
  select-paper-allowlists) select_paper_allowlists ;;
  paper-build-allowlisted) paper_build_allowlisted ;;
  paper-full) paper_full ;;
  merge-all) merge_all ;;
  qa-snapshot) qa_snapshot ;;
  qa-strict) qa_strict ;;
  qa-bundle) qa_bundle ;;
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
