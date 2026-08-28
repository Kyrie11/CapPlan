#!/usr/bin/env bash
set -euo pipefail

CAP_HOME="${CAP_HOME:-/home/senzeyu2/code/CapPlan}"
DATA_ROOT="${DATA_ROOT:-/data0/senzeyu2/dataset/CapPlan/data}"
CONFIG="${CONFIG:-$CAP_HOME/configs/abilitybench_nuplan_real_data0.yaml}"
EXT="$DATA_ROOT/external"
REPORTS="$EXT/reports"
# Historical compatibility marker retained for regression tests/documentation:
# PIPELINE_VERSION="abilitybench_data0_realism_v4_reviewfix3_20260825"
PIPELINE_VERSION="abilitybench_data0_realism_v4_reviewfix5_hotfix1_20260828"

mkdir -p "$REPORTS/commands" "$REPORTS/build" "$REPORTS/model" "$REPORTS/eval"
cd "$CAP_HOME"

pipeline_version() {
  local script_path
  script_path=$(readlink -f "$0" 2>/dev/null || printf '%s' "$0")
  echo "CAPPLAN_PIPELINE_VERSION=$PIPELINE_VERSION"
  echo "CAPPLAN_SCRIPT_PATH=$script_path"
  echo "CAPPLAN_CAP_HOME=$CAP_HOME"
  echo "CAPPLAN_CONFIG=$CONFIG"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$0" | awk '{print "CAPPLAN_SCRIPT_SHA256=" $1}'
  fi
  grep -q 'hybrid-realism-rebuild)' "$0" && echo "CAPPLAN_REALISM_STAGE_DISPATCH=present" || echo "CAPPLAN_REALISM_STAGE_DISPATCH=MISSING"
  grep -q 'hybrid-realism-resume-post-base)' "$0" && echo "CAPPLAN_REALISM_RESUME_DISPATCH=present" || echo "CAPPLAN_REALISM_RESUME_DISPATCH=MISSING"
  grep -q 'hybrid-realism-resume-post-pudo)' "$0" && echo "CAPPLAN_REALISM_POST_PUDO_RESUME_DISPATCH=present" || echo "CAPPLAN_REALISM_POST_PUDO_RESUME_DISPATCH=MISSING"
  grep -q 'hybrid-realism-resume-reviewfix3)' "$0" && echo "CAPPLAN_REVIEWFIX3_RESUME_DISPATCH=present" || echo "CAPPLAN_REVIEWFIX3_RESUME_DISPATCH=MISSING"
  grep -q 'hybrid-dataset-resume-reviewfix5)' "$0" && echo "CAPPLAN_REVIEWFIX5_DATASET_RESUME_DISPATCH=present" || echo "CAPPLAN_REVIEWFIX5_DATASET_RESUME_DISPATCH=MISSING"
  grep -q 'abilitybench_hybrid_site_consistency_v2_20260825' "$CAP_HOME/scripts/audit_hybrid_site_consistency.py" 2>/dev/null \
    && echo "CAPPLAN_SITE_AUDIT_V2=present" || echo "CAPPLAN_SITE_AUDIT_V2=MISSING"
  grep -q 'abilitybench_hybrid_accessibility_v3_20260825' "$CAP_HOME/scripts/build_hybrid_accessibility_overlay.py" 2>/dev/null \
    && echo "CAPPLAN_HYBRID_GRAPH_V3=present" || echo "CAPPLAN_HYBRID_GRAPH_V3=MISSING"
  grep -q 'abilitybench_hybrid_pudo_v6_20260828' "$CAP_HOME/scripts/build_hybrid_pudo_evidence.py" 2>/dev/null \
    && echo "CAPPLAN_HYBRID_PUDO_V6=present" || echo "CAPPLAN_HYBRID_PUDO_V6=MISSING"
  grep -q 'abilitybench_hybrid_dataset_audit_v4_20260825' "$CAP_HOME/scripts/audit_hybrid_benchmark.py" 2>/dev/null \
    && echo "CAPPLAN_HYBRID_AUDIT_V4=present" || echo "CAPPLAN_HYBRID_AUDIT_V4=MISSING"
  grep -q 'capplan_hybrid_review_bundle_v5_hotfix1_20260828' "$CAP_HOME/scripts/build_hybrid_review_bundle.py" 2>/dev/null \
    && echo "CAPPLAN_REVIEW_BUNDLE_V5=present" || echo "CAPPLAN_REVIEW_BUNDLE_V5=MISSING"
}

reviewfix5_runtime_guard() {
  # Dataset-only reviewfix5 must fail before an expensive city build if the
  # running checkout contains only part of the Oracle/performance fix.
  python - "$CAP_HOME" <<'PYGUARD5'
from pathlib import Path
import hashlib, sys
root = Path(sys.argv[1]).resolve()
checks = {
    "capplan/data/label_oracle.py": [
        "origin_states = sorted({",
        "dominates(existing, candidate, self.registry)",
    ],
    "capplan/data/accessibility_layer.py": [
        "def shortest_path_tree(",
        "def materialize_prepared_accessibility_graph(",
    ],
    "capplan/planning/transition_generator.py": [
        "route_aware_pudo_selection: bool = True",
        "def _select_pudo_candidates(",
        "drop-off; the typed ledger still carries pickup-specific ride",
    ],
    "scripts/build_dataset.py": [
        "materialize_prepared_accessibility_graph(",
        "write_legacy_combined=False",
    ],
    "scripts/build_hybrid_pudo_evidence.py": [
        'VERSION = "abilitybench_hybrid_pudo_v6_20260828"',
        '"nearest_agent_distance_dynamic_blockage_risk"',
    ],
    "scripts/diagnose_capplan_outputs.py": [
        "'--fast_graph_scan'",
    ],
    "scripts/merge_datasets.py": [
        "def _link_or_copy(",
    ],
    "scripts/build_hybrid_review_bundle.py": [
        'VERSION = "capplan_hybrid_review_bundle_v5_hotfix1_20260828"',
        'EXPECTED_PIPELINE_VERSION = "abilitybench_data0_realism_v4_reviewfix5_hotfix1_20260828"',
    ],
}
errors=[]
for rel, markers in checks.items():
    p=root/rel
    if not p.exists():
        errors.append(f"missing:{rel}"); continue
    text=p.read_text(encoding="utf-8", errors="replace")
    for marker in markers:
        if marker not in text:
            errors.append(f"marker_missing:{rel}:{marker}")
    print(f"CAPPLAN_REVIEWFIX5_FILE_SHA256[{rel}]={hashlib.sha256(p.read_bytes()).hexdigest()}")
if errors:
    print("CAPPLAN_REVIEWFIX5_RUNTIME_GUARD=FAIL", file=sys.stderr)
    for e in errors: print(e, file=sys.stderr)
    raise SystemExit(2)
print("CAPPLAN_REVIEWFIX5_RUNTIME_GUARD=PASS")
PYGUARD5
  local helper
  for helper in write_reviewfix5_dataset_hashes write_reviewfix5_dataset_run_context reviewfix5_helper_selfcheck reviewfix5_reused_graph_preflight; do
    if ! declare -F "$helper" >/dev/null 2>&1; then
      echo "CAPPLAN_REVIEWFIX5_RUNTIME_GUARD=FAIL helper_missing=$helper" >&2
      return 2
    fi
  done
}

write_reviewfix5_dataset_run_context() {
  local out="${1:-$REPORTS/commands/hybrid_run_context.reviewfix5_dataset.json}"
  python - "$CAP_HOME" "$CONFIG" "$PIPELINE_VERSION" "$out" <<'PYCTX5'
from pathlib import Path
import datetime as dt, hashlib, json, os, sys, time
root=Path(sys.argv[1]).resolve(); config=Path(sys.argv[2]).resolve(); version=sys.argv[3]; out=Path(sys.argv[4]).resolve()
critical=[
  "scripts/build_abilitybench_data0_20260817.sh",
  "scripts/build_hybrid_pudo_evidence.py",
  "scripts/audit_hybrid_site_consistency.py",
  "scripts/build_hybrid_ready_allowlist.py",
  "scripts/build_service_layer.py",
  "capplan/data/label_oracle.py",
  "capplan/data/accessibility_layer.py",
  "capplan/data/pudo_interface_layer.py",
  "capplan/planning/transition_generator.py",
  "capplan/semantics/capability_compiler.py",
  "capplan/semantics/service_automaton.py",
  "capplan/semantics/typed_resource_algebra.py",
  "scripts/build_dataset.py",
  "scripts/audit_dataset_quality.py",
  "scripts/diagnose_capplan_outputs.py",
  "scripts/prepare_abilitybench_external.py",
  "scripts/audit_hybrid_benchmark.py",
  "scripts/merge_datasets.py",
  "scripts/build_hybrid_review_bundle.py",
]
sha={}
for rel in critical:
    p=root/rel
    sha[rel]=hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
missing=[rel for rel,val in sha.items() if val is None]
if missing:
    raise SystemExit("reviewfix5 run-context critical files missing: " + ", ".join(missing))
now_ns=time.time_ns()
run_id=(
    f"reviewfix5_dataset_{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    f"_{now_ns}_{sha[critical[0]][:12]}"
)
upstream=out.parent/"hybrid_run_context.reviewfix3.json"
upstream_payload={}
if upstream.exists():
    try: upstream_payload=json.loads(upstream.read_text(encoding="utf-8"))
    except Exception: upstream_payload={}
payload={
  "run_id":run_id, "pipeline_version":version, "cap_home":str(root), "config":str(config),
  "start_time_ns":now_ns, "start_time_utc":dt.datetime.fromtimestamp(now_ns/1e9, tz=dt.timezone.utc).isoformat(),
  "critical_file_sha256":sha, "hybrid_seed":os.environ.get("CAP_HYBRID_SEED"),
  "reused_upstream_context":str(upstream) if upstream.exists() else None,
  "reused_upstream_run_id":upstream_payload.get("run_id") if upstream_payload else None,
  "reused_artifacts":["hybrid_graph_v3"],
}
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(payload, indent=2, sort_keys=True)+"\n", encoding="utf-8")
print(f"CAPPLAN_HYBRID_RUN_ID={run_id}")
print(f"CAPPLAN_HYBRID_RUN_CONTEXT={out}")
PYCTX5
}

write_reviewfix5_dataset_hashes() {
  local out="${1:-$REPORTS/commands/reviewfix5_dataset_fix.sha256}"
  mkdir -p "$(dirname "$out")"
  sha256sum \
    scripts/build_abilitybench_data0_20260817.sh \
    scripts/build_hybrid_pudo_evidence.py \
    scripts/audit_hybrid_site_consistency.py \
    scripts/build_hybrid_ready_allowlist.py \
    scripts/build_service_layer.py \
    capplan/data/label_oracle.py \
    capplan/data/accessibility_layer.py \
    capplan/data/pudo_interface_layer.py \
    capplan/planning/transition_generator.py \
    capplan/semantics/capability_compiler.py \
    capplan/semantics/service_automaton.py \
    capplan/semantics/typed_resource_algebra.py \
    scripts/build_dataset.py \
    scripts/audit_dataset_quality.py \
    scripts/diagnose_capplan_outputs.py \
    scripts/prepare_abilitybench_external.py \
    scripts/audit_hybrid_benchmark.py \
    scripts/merge_datasets.py \
    scripts/build_hybrid_review_bundle.py \
    | tee "$out"
}

reviewfix5_helper_selfcheck() {
  local helper tmpdir
  for helper in write_reviewfix5_dataset_hashes write_reviewfix5_dataset_run_context; do
    if ! declare -F "$helper" >/dev/null 2>&1; then
      echo "CAPPLAN_REVIEWFIX5_HELPER_DEFINITIONS=FAIL helper_missing=$helper" >&2
      return 2
    fi
  done
  command -v sha256sum >/dev/null 2>&1 || {
    echo "CAPPLAN_REVIEWFIX5_HELPER_DEFINITIONS=FAIL missing_command=sha256sum" >&2
    return 2
  }
  echo "CAPPLAN_REVIEWFIX5_HELPER_DEFINITIONS=present"

  # Execute both helpers against a temporary directory. This catches exactly the
  # class of bug where a Bash helper name exists textually inside a Python heredoc
  # but was never defined by Bash, and also validates the embedded Python syntax.
  tmpdir=$(mktemp -d "${TMPDIR:-/tmp}/capplan-reviewfix5-preflight.XXXXXX")
  if ! write_reviewfix5_dataset_run_context "$tmpdir/context.json" >/dev/null; then
    rm -rf "$tmpdir"
    echo "CAPPLAN_REVIEWFIX5_HELPER_SMOKE=FAIL helper=run_context" >&2
    return 2
  fi
  if ! write_reviewfix5_dataset_hashes "$tmpdir/hashes.sha256" >/dev/null; then
    rm -rf "$tmpdir"
    echo "CAPPLAN_REVIEWFIX5_HELPER_SMOKE=FAIL helper=hashes" >&2
    return 2
  fi
  python - "$tmpdir/context.json" "$PIPELINE_VERSION" <<'PYSELF5'
import json, sys
from pathlib import Path
p=Path(sys.argv[1]); expected=sys.argv[2]
obj=json.loads(p.read_text(encoding="utf-8"))
if obj.get("pipeline_version") != expected:
    raise SystemExit(f"pipeline version mismatch in helper smoke: {obj.get('pipeline_version')} != {expected}")
if not obj.get("run_id") or not isinstance(obj.get("critical_file_sha256"), dict):
    raise SystemExit("invalid reviewfix5 helper smoke context")
PYSELF5
  rm -rf "$tmpdir"
  echo "CAPPLAN_REVIEWFIX5_HELPER_SMOKE=PASS"
}

reviewfix5_reused_graph_preflight() {
  # This resume deliberately reuses the expensive reviewfix3 graph-v3 reports.
  # Prove their lineage before starting PUDO/dataset work so a missing/stale
  # upstream context cannot turn into a many-hour build followed by bundle FAIL.
  python - "$REPORTS" <<'PYGRAPH5'
from pathlib import Path
import json, math, sys
root=Path(sys.argv[1]).resolve()
ctx=root/"commands/hybrid_run_context.reviewfix3.json"
errors=[]
if not ctx.is_file():
    errors.append(f"missing_upstream_context:{ctx}")
    payload={}; start_ns=None
else:
    try:
        payload=json.loads(ctx.read_text(encoding="utf-8"))
    except Exception as e:
        payload={}; errors.append(f"invalid_upstream_context_json:{type(e).__name__}:{e}")
    try:
        start_ns=int(payload.get("start_time_ns")) if payload.get("start_time_ns") else None
    except Exception:
        start_ns=None
    if not payload.get("run_id"):
        errors.append("upstream_context_missing_run_id")
    if start_ns is None:
        errors.append("upstream_context_missing_start_time_ns")
for split in ("train","val","test"):
    for city in ("boston","pittsburgh","vegas","singapore"):
        p=root/f"hybrid_graph.{split}.{city}.json"
        if not p.is_file():
            errors.append(f"missing_graph_report:{p.name}"); continue
        try:
            obj=json.loads(p.read_text(encoding="utf-8"))
        except Exception as e:
            errors.append(f"invalid_graph_report:{p.name}:{type(e).__name__}:{e}"); continue
        if obj.get("version") != "abilitybench_hybrid_accessibility_v3_20260825":
            errors.append(f"wrong_graph_version:{p.name}:{obj.get('version')}")
        if str(obj.get("status") or "").upper() != "PASS":
            errors.append(f"graph_status_not_pass:{p.name}:{obj.get('status')}")
        if start_ns is not None and p.stat().st_mtime_ns <= start_ns:
            errors.append(f"graph_report_stale_vs_upstream_context:{p.name}")
        try:
            slope=(obj.get("numeric_field_ranges") or {}).get("slope") or {}
            vmax=slope.get("max")
            if vmax is not None and math.isfinite(float(vmax)) and float(vmax) > 1.0 + 1e-9:
                errors.append(f"implausible_graph_slope:{p.name}:{vmax}")
        except Exception:
            errors.append(f"invalid_graph_slope_summary:{p.name}")
if errors:
    print("CAPPLAN_REVIEWFIX5_REUSED_GRAPH_PREFLIGHT=FAIL", file=sys.stderr)
    for e in errors[:100]: print(e, file=sys.stderr)
    raise SystemExit(2)
print(f"CAPPLAN_REVIEWFIX5_REUSED_GRAPH_RUN_ID={payload.get('run_id')}")
print("CAPPLAN_REVIEWFIX5_REUSED_GRAPH_REPORTS=12/12")
print("CAPPLAN_REVIEWFIX5_REUSED_GRAPH_PREFLIGHT=PASS")
PYGRAPH5
}

reviewfix3_runtime_guard() {
  # Fail before touching data if the server checkout is not the code version the
  # resume command was designed for.  This prevents another expensive run from
  # silently using an older audit/overlay script from a different checkout.
  python - "$CAP_HOME" <<'PYGUARD'
from pathlib import Path
import hashlib, re, sys
root = Path(sys.argv[1]).resolve()
checks = {
    "scripts/build_hybrid_pudo_evidence.py": [
        'VERSION = "abilitybench_hybrid_pudo_v5_20260825"',
        'STATIC_TRANSFER_FIELDS = PHYSICAL_FIELDS',
        'SIDE_SEMANTICS = "episode_route_relative_service_approach_relation"',
    ],
    "scripts/audit_hybrid_site_consistency.py": [
        'VERSION = "abilitybench_hybrid_site_consistency_v2_20260825"',
        'RELATIONAL_FIELDS = ("side",)',
    ],
    "scripts/build_hybrid_accessibility_overlay.py": [
        'VERSION = "abilitybench_hybrid_accessibility_v3_20260825"',
        'MAX_DEM_GRADE = 1.0',
    ],
    "scripts/audit_hybrid_benchmark.py": [
        'VERSION = "abilitybench_hybrid_dataset_audit_v4_20260825"',
    ],
    "scripts/build_hybrid_review_bundle.py": [
        'VERSION = "capplan_hybrid_review_bundle_v4_20260825"',
    ],
}
errors=[]
for rel, markers in checks.items():
    p=root/rel
    if not p.exists():
        errors.append(f"missing:{rel}"); continue
    text=p.read_text(encoding="utf-8", errors="replace")
    for marker in markers:
        if marker not in text:
            errors.append(f"marker_missing:{rel}:{marker}")
    print(f"CAPPLAN_RUNTIME_FILE_SHA256[{rel}]={hashlib.sha256(p.read_bytes()).hexdigest()}")
if errors:
    print("CAPPLAN_REVIEWFIX3_RUNTIME_GUARD=FAIL", file=sys.stderr)
    for e in errors: print(e, file=sys.stderr)
    raise SystemExit(2)
print("CAPPLAN_REVIEWFIX3_RUNTIME_GUARD=PASS")
PYGUARD
}

write_reviewfix3_run_context() {
  local out="$REPORTS/commands/hybrid_run_context.reviewfix3.json"
  python - "$CAP_HOME" "$CONFIG" "$PIPELINE_VERSION" "$out" <<'PYCTX'
from pathlib import Path
import datetime as dt, hashlib, json, os, sys, time
root=Path(sys.argv[1]).resolve(); config=Path(sys.argv[2]).resolve(); version=sys.argv[3]; out=Path(sys.argv[4])
critical=[
  "scripts/build_abilitybench_data0_20260817.sh",
  "scripts/build_hybrid_pudo_evidence.py",
  "scripts/audit_hybrid_site_consistency.py",
  "scripts/build_hybrid_accessibility_overlay.py",
  "scripts/audit_hybrid_benchmark.py",
  "scripts/build_hybrid_review_bundle.py",
]
sha={}
for rel in critical:
    p=root/rel
    sha[rel]=hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
now_ns=time.time_ns()
run_id=f"reviewfix3_{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_{sha[critical[0]][:12]}"
payload={
  "run_id":run_id, "pipeline_version":version, "cap_home":str(root), "config":str(config),
  "start_time_ns":now_ns, "start_time_utc":dt.datetime.fromtimestamp(now_ns/1e9, tz=dt.timezone.utc).isoformat(),
  "critical_file_sha256":sha, "hybrid_seed":os.environ.get("CAP_HYBRID_SEED"),
}
out.parent.mkdir(parents=True, exist_ok=True); out.write_text(json.dumps(payload, indent=2, sort_keys=True)+"\n")
print(f"CAPPLAN_HYBRID_RUN_ID={run_id}")
print(f"CAPPLAN_HYBRID_RUN_CONTEXT={out}")
PYCTX
}

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


site_disjoint_eval() {
  local city
  mkdir -p "$REPORTS/site_disjoint"
  for city in boston pittsburgh vegas singapore; do
    runlog "site_disjoint_eval.${city}" python scripts/build_site_disjoint_eval_allowlists.py \
      --catalog_csv "$EXT/audits/$city/pudo_site_catalog.csv" --city "$city" \
      --output_dir "$REPORTS/site_disjoint" --min_catalog_sites_per_episode "${CAP_SITE_DISJOINT_MIN_SITES:-2}" \
      --report_json "$REPORTS/site_disjoint/${city}.json"
  done
  for split in train val test; do
    : > "$REPORTS/site_disjoint/all.${split}.site_disjoint.txt"
    for city in boston pittsburgh vegas singapore; do
      cat "$REPORTS/site_disjoint/${city}.${split}.site_disjoint.txt" >> "$REPORTS/site_disjoint/all.${split}.site_disjoint.txt"
    done
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
  audit_status
}

refresh_audit_public_sources() {
  # Audit-specific refresh is intentionally non-strict: partial official-source
  # recovery is useful and all failures are persisted to recommended_public_sources.json.
  runlog fetch_recommended_public_sources.audit python scripts/fetch_recommended_public_sources.py \
    --config "$CONFIG" --cities boston,pittsburgh,vegas,singapore
}

audit_status() {
  runlog pudo_audit_status python scripts/summarize_pudo_audit_status.py \
    --external_root "$EXT" --reports_root "$REPORTS" \
    --output "$REPORTS/pudo_audit_status.json"
  runlog pudo_evidence_gap_manifest python scripts/export_pudo_evidence_gap_manifest.py \
    --external_root "$EXT" \
    --output_csv "$REPORTS/pudo_evidence_gap_manifest.csv" \
    --report_json "$REPORTS/pudo_evidence_gap_manifest.json"
}

recover_audit_evidence() {
  runlog pudo_audit_evidence_recovery python scripts/recover_pudo_audit_sources.py \
    --external_root "$EXT" --cities boston,pittsburgh,vegas,singapore \
    --report_json "$REPORTS/pudo_audit_evidence_recovery.json"
  prefill_audit_worklists
  classify_audits
  triage_audits
}

render_audit_packets() {
  local max_rows="${CAP_AUDIT_RENDER_MAX_ROWS:-0}"
  local radius_m="${CAP_AUDIT_RENDER_RADIUS_M:-120}"
  local scope="${CAP_AUDIT_RENDER_SCOPE:-auto}"
  [[ "$scope" =~ ^(auto|visual|new_evidence)$ ]] || { echo "CAP_AUDIT_RENDER_SCOPE must be auto|visual|new_evidence" >&2; return 2; }
  for city in boston pittsburgh vegas singapore; do
    local visual_csv="$EXT/audits/$city/visual_review_required.csv"
    local evidence_csv="$EXT/audits/$city/new_evidence_required.csv"
    local csv="" bucket="" outdir=""
    if [[ "$scope" != "new_evidence" ]] && [[ -s "$visual_csv" ]] && [[ $(wc -l < "$visual_csv") -gt 1 ]]; then
      csv="$visual_csv"; bucket="visual"; outdir="$REPORTS/audit_packets/$city/visual"
    elif [[ "$scope" != "visual" ]] && [[ -s "$evidence_csv" ]] && [[ $(wc -l < "$evidence_csv") -gt 1 ]]; then
      csv="$evidence_csv"; bucket="evidence_gap"; outdir="$REPORTS/audit_packets/$city/evidence_gap"
      echo "INFO: no visual-review rows for $city; rendering NEW_EVIDENCE_REQUIRED diagnostic packets instead"
    else
      echo "INFO: no rows for audit packet scope=$scope city=$city; skip packet rendering"
      continue
    fi
    runlog "pudo_audit_render.${city}.${bucket}" python scripts/render_pudo_audit_packets.py \
      --input_csv "$csv" --data_root "$DATA_ROOT" \
      --georeference_json "$EXT/georeference/${city}.json" \
      --output_dir "$outdir" \
      --radius_m "$radius_m" --max_rows "$max_rows" \
      --report_json "$REPORTS/pudo_audit_render.${city}.${bucket}.json"
  done
}

audit_review_bundle() {
  runlog pudo_audit_review_bundle python scripts/build_audit_review_bundle.py \
    --external_root "$EXT" --reports_root "$REPORTS" \
    --max_rows "${CAP_AUDIT_BUNDLE_MAX_ROWS:-100}" \
    --max_images "${CAP_AUDIT_BUNDLE_MAX_IMAGES:-24}" \
    --output_zip "$REPORTS/capplan_audit_review_bundle.zip"
}

hybrid_review_bundle() {
  reviewfix5_runtime_guard
  # The review bundle is also the final freeze-readiness gate.  Even on an
  # incomplete/failed run the Python packager writes a diagnostic ZIP first,
  # then exits non-zero so stale historical artifacts cannot masquerade as PASS.
  runlog hybrid_review_bundle python scripts/build_hybrid_review_bundle.py \
    --reports_root "$REPORTS" \
    --output_zip "$REPORTS/capplan_hybrid_review_bundle.zip" \
    --require_complete
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
      local unresolved="$EXT/audits/$city/new_evidence_required.csv"
      if [[ -s "$unresolved" ]] && [[ $(wc -l < "$unresolved") -gt 1 ]]; then
        echo "INFO: no source-complete review candidates for $city; unresolved rows still require source/evidence recovery (see new_evidence_required.csv). Do NOT stamp them as human-reviewed."
      else
        echo "INFO: no source-complete review candidates for $city; skip explicit source review"
      fi
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


hybrid_pudo_evidence_only() {
  # Cheap/idempotent PUDO overlay refresh.  This is deliberately separate from
  # the very large accessibility-graph overlay so an interrupted hybrid build
  # can pick up PUDO-policy fixes without repeating hours of graph work.
  local split city allowlist base_pudo hybrid_city audit_csv
  for split in train val test; do
    mkdir -p "$DATA_ROOT/outputs/prepared/$split/pudo_hybrid"
    for city in boston pittsburgh vegas singapore; do
      allowlist="$REPORTS/hybrid_episode_ids.${split}.${city}.txt"
      python - "$DATA_ROOT/outputs/prepared/$split/scene_contexts/$city/episodes.jsonl" "$allowlist" <<'PYIDS'
import json, sys
from pathlib import Path
src, dst = map(Path, sys.argv[1:])
if not src.exists(): raise SystemExit(f"missing {src}")
ids=[]
for line in src.open():
    if line.strip():
        r=json.loads(line); eid=r.get("episode_id")
        if eid: ids.append(str(eid))
dst.parent.mkdir(parents=True, exist_ok=True)
dst.write_text("\n".join(ids)+( "\n" if ids else ""))
print(f"wrote {dst}: {len(ids)} episode ids")
PYIDS
      base_pudo="$DATA_ROOT/outputs/prepared/$split/pudo/${city}.jsonl"
      hybrid_city="$DATA_ROOT/outputs/prepared/$split/pudo_hybrid/${city}.jsonl"
      audit_csv="$EXT/audits/$city/pudo_audit_worklist.csv"
      runlog "hybrid_pudo.${split}.${city}" python scripts/build_hybrid_pudo_evidence.py \
        --input_pudo_jsonl "$base_pudo" --output_pudo_jsonl "$hybrid_city" \
        --city "$city" --split "$split" \
        --audit_worklist_csv "$audit_csv" \
        --site_evidence_peer_jsonl "train=$DATA_ROOT/outputs/prepared/train/pudo/${city}.jsonl" \
        --site_evidence_peer_jsonl "val=$DATA_ROOT/outputs/prepared/val/pudo/${city}.jsonl" \
        --site_evidence_peer_jsonl "test=$DATA_ROOT/outputs/prepared/test/pudo/${city}.jsonl" \
        --seed "${CAP_HYBRID_SEED:-20260822}" \
        --min_positive_per_episode "${CAP_HYBRID_MIN_PUDOS:-2}" \
        --report_json "$REPORTS/hybrid_pudo.${split}.${city}.json"
    done
    runlog "hybrid_pudo.${split}.concat" python scripts/concat_jsonl_files.py \
      --inputs \
        "$DATA_ROOT/outputs/prepared/$split/pudo_hybrid/boston.jsonl" \
        "$DATA_ROOT/outputs/prepared/$split/pudo_hybrid/pittsburgh.jsonl" \
        "$DATA_ROOT/outputs/prepared/$split/pudo_hybrid/vegas.jsonl" \
        "$DATA_ROOT/outputs/prepared/$split/pudo_hybrid/singapore.jsonl" \
      --output "$DATA_ROOT/outputs/prepared/$split/pudo_hybrid_evidence.jsonl"
  done
  hybrid_site_consistency_only
}

hybrid_site_consistency_only() {
  for split in train val test; do
    [[ -s "$DATA_ROOT/outputs/prepared/$split/pudo_hybrid_evidence.jsonl" ]] || {
      echo "Missing fresh hybrid PUDO evidence for $split; run hybrid-pudo-refresh first" >&2
      return 2
    }
  done
  runlog "hybrid_site_consistency" python scripts/audit_hybrid_site_consistency.py \
    --input "train=$DATA_ROOT/outputs/prepared/train/pudo_hybrid_evidence.jsonl" \
    --input "val=$DATA_ROOT/outputs/prepared/val/pudo_hybrid_evidence.jsonl" \
    --input "test=$DATA_ROOT/outputs/prepared/test/pudo_hybrid_evidence.jsonl" \
    --output "$REPORTS/hybrid_site_consistency.json" --fail_on_error
}

hybrid_graph_evidence_only() {
  # Expensive graph overlay.  Real nodes/topology and observed attributes are
  # preserved; only missing typed-resource attributes receive deterministic,
  # provenance-tagged benchmark values.
  local split city base_graph hybrid_graph allowlist
  for split in train val test; do
    base_graph="$DATA_ROOT/outputs/prepared/$split/accessibility_graphs"
    hybrid_graph="$DATA_ROOT/outputs/prepared/$split/accessibility_graphs_hybrid"
    # The current hybrid overlay is a complete materialization of the current
    # capped episode inventory.  Remove stale episode files from older overlay
    # versions/selections before repopulating all four cities.  Otherwise a
    # previous scene can remain discoverable by build_service_layer.py even
    # though it is no longer in the current inventory.
    rm -rf "$hybrid_graph"
    mkdir -p "$hybrid_graph"
    for city in boston pittsburgh vegas singapore; do
      allowlist="$REPORTS/hybrid_episode_ids.${split}.${city}.txt"
      [[ -s "$allowlist" ]] || { echo "Missing hybrid episode inventory: $allowlist" >&2; return 2; }
      runlog "hybrid_graph.${split}.${city}" python scripts/build_hybrid_accessibility_overlay.py \
        --input_graph_dir "$base_graph" --output_graph_dir "$hybrid_graph" \
        --city "$city" --split "$split" --episode_allowlist "$allowlist" \
        --seed "${CAP_HYBRID_SEED:-20260822}" \
        --report_json "$REPORTS/hybrid_graph.${split}.${city}.json"
    done
  done
}

hybrid_evidence() {
  # Geometry-anchored benchmark truth without pretending simulated values are
  # exact city measurements.  PUDO and graph stages are split so PUDO policy
  # updates can be replayed cheaply on an already-built heavy corpus.
  hybrid_pudo_evidence_only
  hybrid_graph_evidence_only
}

hybrid_ready_allowlists() {
  # Final hybrid membership is evidence-driven.  Do not fabricate new PUDO
  # geometry merely to satisfy the >=N anchor gate: select only episodes whose
  # already-built hybrid PUDO layer has enough complete/legal/unblocked anchors.
  local split city src out rejected
  for split in train val test; do
    mkdir -p "$DATA_ROOT/outputs/prepared/$split/hybrid_ready_episode_ids"
    for city in boston pittsburgh vegas singapore; do
      src="$DATA_ROOT/outputs/prepared/$split/pudo_hybrid/$city.jsonl"
      out="$DATA_ROOT/outputs/prepared/$split/hybrid_ready_episode_ids/$city.txt"
      rejected="$DATA_ROOT/outputs/prepared/$split/hybrid_ready_episode_ids/$city.rejected.txt"
      [[ -s "$src" ]] || { echo "Missing hybrid PUDO evidence: $src" >&2; return 2; }
      runlog "hybrid_ready.${split}.${city}" python scripts/build_hybrid_ready_allowlist.py \
        --input_pudo_jsonl "$src" \
        --output_allowlist "$out" \
        --output_rejected "$rejected" \
        --min_hybrid_eligible_pudos "${CAP_HYBRID_MIN_PUDOS:-2}" \
        --city "$city" --split "$split" \
        --report_json "$REPORTS/hybrid_ready.${split}.${city}.json"
    done
  done
}

hybrid_dataset_build_only() {
  # Build service/labels/datasets from already-materialized hybrid graph+PUDO.
  # Kept separate so from-zero/full flows do not regenerate the cheap PUDO
  # overlay twice after hybrid_evidence has already produced it.
  hybrid_ready_allowlists
  local split
  for split in train val test; do
    runlog "hybrid_build.${split}" python scripts/prepare_abilitybench_external.py \
      --config "$CONFIG" --split "$split" --source_policy hybrid \
      --cities boston+pittsburgh+vegas+singapore \
      --stages preflight,service,dataset,merge
  done
}

hybrid_build() {
  # Resume path when the large hybrid graph overlay is already valid: refresh
  # only the cheap PUDO layer and then rebuild service/labels/datasets.
  hybrid_pudo_evidence_only
  hybrid_dataset_build_only
}

reviewfix5_preflight() {
  reviewfix5_runtime_guard
  reviewfix5_helper_selfcheck
  pipeline_version
  python scripts/diagnose_capplan_outputs.py --help | grep -q -- '--fast_graph_scan'
  echo "CAPPLAN_REVIEWFIX5_DIAGNOSE_FAST_GRAPH_SCAN=present"
}

hybrid_dataset_resume_reviewfix5() {
  # Minimal continuation for the reviewfix4 failure: graph v3, PUDO v5 and site
  # consistency v2 are already valid and expensive/semantically upstream.  Make
  # a fresh dataset-run identity, regenerate readiness + service/dataset/oracle
  # labels/audits/merge only, and let the review bundle distinguish these new
  # artifacts from the reused reviewfix3 lineage.
  reviewfix5_runtime_guard
  reviewfix5_helper_selfcheck
  reviewfix5_reused_graph_preflight
  pipeline_version | tee "$REPORTS/commands/pipeline_identity.reviewfix5_dataset.txt"
  write_reviewfix5_dataset_run_context | tee "$REPORTS/commands/hybrid_run_context.reviewfix5_dataset.log"
  write_reviewfix5_dataset_hashes
  # PUDO is cheap to refresh and v6 closes the missing dynamic-blockage
  # provenance gap.  The expensive hybrid accessibility graph v3 is reused.
  hybrid_pudo_evidence_only
  hybrid_dataset_build_only
}

hybrid_reality_refresh() {
  # Minimal rebuild after hybrid-prior/service semantics changes. Reuse immutable
  # nuPlan extraction + real accessibility topology + base PUDO candidates, but
  # regenerate the hybrid graph overlay (static correlated priors), PUDO overlay,
  # service requests, labels, city datasets and merged train/val/test outputs.
  hybrid_graph_evidence_only
  hybrid_build
}

hybrid_realism_resume_reviewfix2() {
  # Recommended continuation for the uploaded reviewfix1 run.  Expensive
  # site-catalog/source-prefill work is already fresh.  Rebuild only the cheap
  # hybrid PUDO v4 overlay so its reports use corrected route-side semantics,
  # then run the corrected site audit before spending time on hybrid graph and
  # final dataset materialization.  The manual identity intentionally does not
  # replace the original pipeline freshness anchor because all reused upstream
  # artifacts were produced within that same run lineage.
  pipeline_version | tee "$REPORTS/commands/manual_pipeline_identity.reviewfix2_resume.txt"
  hybrid_pudo_evidence_only
  hybrid_graph_evidence_only
  hybrid_dataset_build_only
}

reviewfix3_preflight() {
  reviewfix3_runtime_guard
  pipeline_version
}

hybrid_realism_resume_reviewfix3() {
  # Correct continuation after the reviewfix2 deployment mismatch.  The stage
  # first proves that the running checkout contains the intended semantics, then
  # creates a fresh pipeline identity/run context.  It refreshes cheap PUDO v5,
  # regenerates hybrid graph v3 (including DEM-outlier sanitation), and rebuilds
  # final labels/datasets/audits.  Base graph/PUDO and external downloads are reused.
  reviewfix3_runtime_guard
  pipeline_version | tee "$REPORTS/commands/pipeline_identity.reviewfix3_resume.txt"
  write_reviewfix3_run_context | tee "$REPORTS/commands/hybrid_run_context.reviewfix3.log"
  hybrid_pudo_evidence_only
  hybrid_graph_evidence_only
  hybrid_dataset_build_only
}

hybrid_realism_resume_post_pudo() {
  # Recovery path for the reviewfix1 run that successfully materialized all
  # v4 hybrid PUDO files and then stopped only because route-relative ``side``
  # was incorrectly audited as an immutable physical-site fact.  Do not move
  # the review-bundle freshness anchor: the reused v4 PUDO artifacts were
  # legitimately generated after the original realism-v4 resume identity.
  pipeline_version | tee "$REPORTS/commands/manual_pipeline_identity.reviewfix2_post_pudo.txt"
  hybrid_site_consistency_only
  hybrid_graph_evidence_only
  hybrid_dataset_build_only
}

hybrid_realism_resume_post_base() {
  # Resume after corrected realism-v4 base graph/PUDO materialization has already
  # completed.  This is the right recovery path for the 2026-08-24 run whose
  # reports reached site catalog / evidence recovery but never produced fresh
  # hybrid v2/v4 overlays or final semantic audits.
  pipeline_version | tee "$REPORTS/commands/pipeline_identity.realism_v4_resume.txt"
  build_site_catalogs
  recover_audit_evidence
  site_disjoint_eval
  hybrid_evidence
  hybrid_dataset_build_only
}

hybrid_realism_rebuild() {
  # Recommended rebuild for the corrected realism-v4 pipeline.  Scene extraction,
  # full nuPlan identity indexes and downloaded external sources are reused.
  # Base accessibility graphs must be regenerated because DEM samples are now
  # evidence (<=5 m endpoint elevation -> derived grade) instead of accidental
  # generic POI graph nodes; PUDO/site/audit/hybrid/service labels therefore
  # follow the corrected graph semantics.
  local graph_jobs="${CAP_GRAPH_CITY_JOBS:-2}"
  local pudo_jobs="${CAP_PUDO_CITY_JOBS:-4}"
  local split
  # Record the exact checkout/script identity at the start of the expensive run.
  # This makes wrong-checkout failures visible in the reports bundle.
  pipeline_version | tee "$REPORTS/commands/pipeline_identity.realism_v4.txt"
  for split in train val test; do
    runlog "realism_v4_preflight.${split}" python scripts/prepare_abilitybench_external.py \
      --config "$CONFIG" --split "$split" --source_policy bootstrap \
      --cities boston+pittsburgh+vegas+singapore --stages preflight
    run_city_stage_parallel "$split" bootstrap graphs "$graph_jobs" realism_v4_base config
    run_city_stage_parallel "$split" bootstrap pudo "$pudo_jobs" realism_v4_base config
    concat_split_pudo "$split" realism_v4_base
  done
  build_site_catalogs
  recover_audit_evidence
  site_disjoint_eval
  hybrid_evidence
  hybrid_dataset_build_only
}

hybrid_full_build() {
  # End-to-end benchmark construction from the immutable nuPlan substrate.
  # The full official DB inventory is indexed, while heavy graph/PUDO
  # materialization intentionally follows the paper-scale split caps from the
  # config (train=1000/city and val/test=250/city unless overridden there).
  inspect_nuplan
  index_nuplan_full
  bootstrap_preflight
  bootstrap_candidates_paper_scale_staged
  build_site_catalogs
  refresh_audit_public_sources
  recover_audit_evidence
  site_disjoint_eval
  hybrid_evidence
  hybrid_dataset_build_only
}

hybrid_from_existing() {
  # Recommended continuation from the user's current 6000-scene heavy corpus.
  refresh_audit_public_sources
  recover_audit_evidence
  site_disjoint_eval
  hybrid_evidence
  hybrid_dataset_build_only
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
  version                              # print exact pipeline/check-out identity before long runs
  reviewfix3-preflight                 # hard-check critical script semantic versions/hashes before data writes
  reviewfix5-preflight                 # code/helper smoke check; no benchmark data writes
  reviewfix5-reused-graph-preflight    # verify reviewfix3 graph-v3 lineage before dataset-only resume
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
  site-disjoint-eval                  # secondary strict eval allowlists; never purge the main train split
  prefill-audits
  classify-audits
  triage-audits                       # deterministic reject/missing/visual/explicit-source buckets + status
  audit-status                        # combined report + compact evidence-gap manifest; PASS != evidence complete
  refresh-audit-public-sources        # refresh all four cities: Boston/Pittsburgh/Clark County/LTA official/public layers
  recover-audit-evidence              # semantic source recovery + prefill/classify/triage rerun
  hybrid-pudo-refresh                   # cheap PUDO-only hybrid overlay refresh; reuses existing graph overlay
  hybrid-evidence                      # explicit observed/derived/simulated benchmark truth overlays
  hybrid-ready-allowlists              # select evidence-valid hybrid episodes; no geometry synthesis
  hybrid-build                         # build benchmark datasets under outputs/datasets/abilitybench_av_hybrid_*
  hybrid-dataset-resume-reviewfix5     # RECOMMENDED here: reuse graph v3, refresh PUDO v6, and rebuild final datasets/audits
  hybrid-from-existing                 # recommended continuation: source refresh -> recovery -> overlays -> build
  hybrid-full-build                    # complete from-zero four-city train/val/test hybrid benchmark pipeline
  hybrid-reality-refresh               # Rebuild hybrid priors/service/labels only when base graph semantics are already current
  hybrid-realism-rebuild               # full realism-v4: rebuild base graph/PUDO + downstream hybrid; reuse scene extraction/downloads
  hybrid-realism-resume-post-base      # reuse fresh v5 base graph/PUDO and rebuild audit/hybrid/dataset
  hybrid-realism-resume-post-pudo      # ultra-minimal: reuse existing v4 hybrid PUDO, rerun corrected site audit, then graph/dataset
  hybrid-realism-resume-reviewfix2     # legacy reviewfix2 resume
  hybrid-realism-resume-reviewfix3     # RECOMMENDED: runtime-guarded PUDO v5 + graph v3 + final dataset/audits
  render-audit-packets                # visual rows, or evidence-gap diagnostic packets when visual bucket is empty
  audit-review-bundle                 # small PUDO audit ZIP under reports/ (no NPZ/full dataset)
  hybrid-review-bundle                # compact hybrid rebuild/dataset reports ZIP for remote review
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
  version|pipeline-version) pipeline_version ;;
  reviewfix3-preflight) reviewfix3_preflight ;;
  reviewfix5-preflight) reviewfix5_preflight ;;
  reviewfix5-reused-graph-preflight) reviewfix5_reused_graph_preflight ;;
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
  site-disjoint-eval) site_disjoint_eval ;;
  prefill-audits) prefill_audit_worklists ;;
  classify-audits) classify_audits ;;
  triage-audits) triage_audits ;;
  audit-status) audit_status ;;
  refresh-audit-public-sources) refresh_audit_public_sources ;;
  recover-audit-evidence) recover_audit_evidence ;;
  hybrid-pudo-refresh) hybrid_pudo_evidence_only ;;
  hybrid-evidence) hybrid_evidence ;;
  hybrid-ready-allowlists) hybrid_ready_allowlists ;;
  hybrid-build) hybrid_build ;;
  hybrid-dataset-resume-reviewfix5) hybrid_dataset_resume_reviewfix5 ;;
  hybrid-from-existing) hybrid_from_existing ;;
  hybrid-full-build) hybrid_full_build ;;
  hybrid-reality-refresh) hybrid_reality_refresh ;;
  hybrid-realism-rebuild) hybrid_realism_rebuild ;;
  hybrid-realism-resume-post-base) hybrid_realism_resume_post_base ;;
  hybrid-realism-resume-post-pudo) hybrid_realism_resume_post_pudo ;;
  hybrid-realism-resume-reviewfix2) hybrid_realism_resume_reviewfix2 ;;
  hybrid-realism-resume-reviewfix3) hybrid_realism_resume_reviewfix3 ;;
  render-audit-packets) render_audit_packets ;;
  audit-review-bundle) audit_review_bundle ;;
  hybrid-review-bundle) hybrid_review_bundle ;;
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
