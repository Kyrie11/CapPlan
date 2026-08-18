#!/usr/bin/env bash
# Reproducible CapPlan/AbilityBench-AV data0 driver after the 2026-08-18 audit fixes.
# It deliberately stops at evidence gaps instead of fabricating paper truth.
set -euo pipefail

CAP_HOME="${CAP_HOME:-/home/senzeyu2/code/CapPlan}"
DATA_ROOT="${DATA_ROOT:-/data0/senzeyu2/dataset/CapPlan/data}"
CONFIG="${CONFIG:-$CAP_HOME/configs/abilitybench_nuplan_real_data0.yaml}"
EXT="${EXT:-$DATA_ROOT/external}"
REPORTS="${REPORTS:-$EXT/reports}"
CITIES="boston+pittsburgh+vegas+singapore"
AUDIT_CSV_NAME="${AUDIT_CSV_NAME:-pudo_audit_final.csv}"
export CAP_HOME DATA_ROOT CONFIG EXT REPORTS

MODE="${1:-help}"

usage() {
  cat <<EOF
Usage: $0 MODE

Modes:
  preflight              DB/map/georef/bootstrap-source checks
  bootstrap-candidates   full train/val/test extract+graph+PUDO candidate pass
  prefill-audit          cross-split shortlist + conservative Tier-A auto-prefill
  paper-layers           materialize final audit CSVs into normalized Tier-A layers
  paper-preflight        verified fleet + provenance/external paper gate
  paper-full             full train/val/test paper build + per-split audits
  bundle-audit           cross-split leakage/site audit + canonical merge/audit
  test-code              run repository tests in two bounded groups

For paper-layers, each city must have:
  $EXT/audits/<city>/$AUDIT_CSV_NAME
Copy the generated pudo_audit_prefilled.csv to that name and resolve every
remaining auto_residual_fields item. Fully Tier-A official rows may remain
without a human auditor and are accepted only with --allow_automatic_tier_a.
EOF
}

if [[ "$MODE" != "help" && "$MODE" != "-h" && "$MODE" != "--help" ]]; then
  mkdir -p "$REPORTS/commands" "$REPORTS/build" "$REPORTS/model" "$REPORTS/eval"
  cd "$CAP_HOME"
fi

case "$MODE" in
  preflight)
    for split in train val test; do
      python scripts/inspect_nuplan_db_cities.py \
        --config "$CONFIG" --split "$split" --fail_on_unknown \
        --report_json "$REPORTS/nuplan_db_cities.${split}.json" \
        2>&1 | tee "$REPORTS/commands/nuplan_db_cities.${split}.log"
    done
    python scripts/inspect_nuplan_map_crs.py \
      --config "$CONFIG" --cities "$CITIES" --output_dir "$EXT/georeference" \
      2>&1 | tee "$REPORTS/commands/nuplan_map_crs.log"
    python scripts/validate_georeference_alignment.py \
      --config "$CONFIG" --cities boston,pittsburgh,vegas,singapore \
      --min_map_covered_by_aoi 0.95 --min_aoi_covered_by_osm 0.95 --min_map_covered_by_osm 0.95 \
      --write_georeference --report_json "$REPORTS/georeference_spatial_alignment.json" \
      2>&1 | tee "$REPORTS/commands/georeference_spatial_alignment.log"
    python scripts/validate_external_sources.py \
      --config "$CONFIG" --cities boston,pittsburgh,vegas,singapore \
      --source_policy bootstrap --output "$REPORTS/external.bootstrap.json" \
      2>&1 | tee "$REPORTS/commands/external.bootstrap.log"
    ;;

  bootstrap-candidates)
    for split in train val test; do
      python scripts/prepare_abilitybench_external.py \
        --config "$CONFIG" --split "$split" --source_policy bootstrap \
        --cities "$CITIES" --max_scenarios_per_city 0 \
        --stages preflight,extract,graphs,pudo \
        2>&1 | tee "$REPORTS/commands/bootstrap.${split}.candidates.all.log"
    done
    ;;

  prefill-audit)
    for city in boston pittsburgh vegas singapore; do
      mkdir -p "$EXT/audits/$city"
      geo="$EXT/georeference/$city.json"
      python scripts/export_pudo_audit_shortlist.py \
        --pudo_evidence_jsonl "$DATA_ROOT/outputs/prepared/train/pudo/$city.jsonl" \
        --pudo_evidence_jsonl "$DATA_ROOT/outputs/prepared/val/pudo/$city.jsonl" \
        --pudo_evidence_jsonl "$DATA_ROOT/outputs/prepared/test/pudo/$city.jsonl" \
        --georeference_json "$geo" --city "$city" \
        --max_candidates_per_episode 4 --dedup_radius_m 5 \
        --output_csv "$EXT/audits/$city/pudo_audit_shortlist.csv" \
        --report_json "$REPORTS/manual_audit_shortlist.all_splits.$city.json"
      python scripts/auto_prefill_pudo_entrance_audit.py \
        --shortlist_csv "$EXT/audits/$city/pudo_audit_shortlist.csv" \
        --city "$city" --external_root "$EXT" \
        --output_csv "$EXT/audits/$city/pudo_audit_prefilled.csv" \
        --report_json "$REPORTS/manual_audit_autoprefill.$city.json"
    done
    python - <<'PY'
import csv, json, os
from pathlib import Path
root=Path(os.environ.get('EXT','/data0/senzeyu2/dataset/CapPlan/data/external'))
summary={}
for city in ['boston','pittsburgh','vegas','singapore']:
    p=root/'audits'/city/'pudo_audit_prefilled.csv'
    rows=list(csv.DictReader(p.open(encoding='utf-8-sig')))
    residual=[r for r in rows if (r.get('auto_residual_fields') or '').strip()]
    fields={}
    for r in residual:
        for k in (r.get('auto_residual_fields') or '').split(';'):
            if k: fields[k]=fields.get(k,0)+1
    summary[city]={'rows':len(rows),'complete':len(rows)-len(residual),'residual_rows':len(residual),'residual_field_counts':fields}
print(json.dumps(summary,indent=2,sort_keys=True))
(root/'reports'/'manual_audit_residual_summary.json').write_text(json.dumps(summary,indent=2,sort_keys=True)+'\n')
PY
    ;;

  paper-layers)
    for city in boston pittsburgh vegas singapore; do
      in_csv="$EXT/audits/$city/$AUDIT_CSV_NAME"
      test -s "$in_csv" || { echo "Missing final audit CSV: $in_csv" >&2; exit 2; }
      python scripts/build_manual_audit_layers.py \
        --input_csv "$in_csv" --city "$city" --external_root "$EXT" \
        --paper_mode --allow_automatic_tier_a \
        --report_json "$REPORTS/manual_audit_layers.$city.json" \
        2>&1 | tee "$REPORTS/commands/manual_audit_layers.$city.log"
    done
    ;;

  paper-preflight)
    python scripts/validate_fleet_interface.py \
      --fleet_jsonl "$EXT/normalized/fleet/vehicle_interfaces.jsonl" \
      --output "$REPORTS/fleet_interface.paper.json" \
      2>&1 | tee "$REPORTS/commands/fleet_interface.paper.log"
    for city in boston pittsburgh vegas singapore; do
      python scripts/build_provenance_manifest.py \
        --registry "$EXT/provenance_registry.yaml" --city "$city" \
        --output "$EXT/manifests/$city.json" \
        2>&1 | tee "$REPORTS/commands/provenance.$city.log"
    done
    python scripts/validate_external_sources.py \
      --config "$CONFIG" --cities boston,pittsburgh,vegas,singapore \
      --source_policy paper --output "$REPORTS/external.paper.json" \
      2>&1 | tee "$REPORTS/commands/external.paper.log"
    ;;

  paper-full)
    for split in train val test; do
      python scripts/prepare_abilitybench_external.py \
        --config "$CONFIG" --split "$split" --source_policy paper \
        --cities "$CITIES" --max_scenarios_per_city 0 \
        --stages preflight,extract,graphs,pudo,service,dataset,merge \
        2>&1 | tee "$REPORTS/commands/paper.${split}.all.log"
      python scripts/validate_dataset.py \
        --dataset_dir "$DATA_ROOT/outputs/datasets/abilitybench_av_${split}" --strict \
        2>&1 | tee "$REPORTS/commands/validate.paper.${split}.log"
      python scripts/audit_dataset_quality.py \
        --dataset_dir "$DATA_ROOT/outputs/datasets/abilitybench_av_${split}" \
        --paper_mode --fail_if_not_publication_ready \
        --output "$REPORTS/dataset_quality.paper.${split}.json" \
        2>&1 | tee "$REPORTS/commands/audit.paper.${split}.log"
    done
    ;;

  bundle-audit)
    python scripts/audit_dataset_bundle.py \
      --train_dir "$DATA_ROOT/outputs/datasets/abilitybench_av_train" \
      --val_dir "$DATA_ROOT/outputs/datasets/abilitybench_av_val" \
      --test_dir "$DATA_ROOT/outputs/datasets/abilitybench_av_test" \
      --expected_profiles_per_episode 8 \
      --required_cities boston,pittsburgh,vegas,singapore \
      --site_disjoint_test_episodes "$REPORTS/site_disjoint_test_episodes.txt" \
      --output "$REPORTS/dataset_bundle.paper.json" --fail_if_not_ready \
      2>&1 | tee "$REPORTS/commands/dataset_bundle.paper.log"
    python scripts/merge_datasets.py \
      --input_dirs \
        "$DATA_ROOT/outputs/datasets/abilitybench_av_train" \
        "$DATA_ROOT/outputs/datasets/abilitybench_av_val" \
        "$DATA_ROOT/outputs/datasets/abilitybench_av_test" \
      --output_dir "$DATA_ROOT/outputs/datasets/abilitybench_av_all" --strict \
      2>&1 | tee "$REPORTS/commands/merge.abilitybench_av_all.log"
    python scripts/validate_dataset.py \
      --dataset_dir "$DATA_ROOT/outputs/datasets/abilitybench_av_all" --strict \
      2>&1 | tee "$REPORTS/commands/validate.abilitybench_av_all.log"
    python scripts/audit_dataset_quality.py \
      --dataset_dir "$DATA_ROOT/outputs/datasets/abilitybench_av_all" \
      --paper_mode --fail_if_not_publication_ready \
      --output "$REPORTS/dataset_quality.paper.all.json" \
      2>&1 | tee "$REPORTS/commands/audit.abilitybench_av_all.log"
    ;;

  test-code)
    mapfile -t files < <(find tests -maxdepth 1 -name 'test_*.py' | sort)
    pytest -q "${files[@]:0:18}"
    pytest -q "${files[@]:18}"
    ;;

  help|-h|--help) usage ;;
  *) echo "Unknown mode: $1" >&2; usage >&2; exit 2 ;;
esac
