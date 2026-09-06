#!/usr/bin/env bash
set -euo pipefail
CAP_HOME="${CAP_HOME:-/home/senzeyu2/code/CapPlan}"
DATA_ROOT="${DATA_ROOT:-/data0/senzeyu2/dataset/CapPlan/data}"
HYBRID_TEST="${HYBRID_TEST:-$DATA_ROOT/outputs/datasets/abilitybench_av_hybrid_test}"
GPU0="${GPU0:-0}"; SEED="${SEED:-13}"
EPISODE_LIMIT="${EPISODE_LIMIT:-256}"; EPISODE_SEED="${EPISODE_SEED:-13}"
CASA_CHECKPOINT="${CASA_CHECKPOINT:-$CAP_HOME/outputs/models/casa_relation_mlp_seed13/checkpoint.pt}"
V12_ROOT="${V12_ROOT:-$CAP_HOME/outputs/eval/v12_fast_seed${SEED}}"
mkdir -p "$V12_ROOT"/{runs,logs,timing_repeat_0,timing_repeat_1,timing_repeat_2}; cd "$CAP_HOME"
[[ -f "$CASA_CHECKPOINT" ]] || { echo "missing CASA checkpoint: $CASA_CHECKPOINT" >&2; exit 2; }

# Confirmatory V12: frozen V11 SN-CPK acceptance + shared exact semantics for
# proof-on-demand replay.  No network retraining and no change to hard authority.
CUDA_VISIBLE_DEVICES="$GPU0" python scripts/run_ablations.py \
  --dataset_dir "$HYBRID_TEST" --output_dir "$V12_ROOT/runs" \
  --trajectory_mode mock_strict --casa_mode learned --casa_checkpoint "$CASA_CHECKPOINT" --casa_device cuda:0 \
  --algorithm_version V12 --evidence_grounded_runtime \
  --episode_limit "$EPISODE_LIMIT" --episode_seed "$EPISODE_SEED" \
  --variants full v11_reference_runtime v2_reference_runtime no_typed_viability no_viability_kernel no_lazy_diagnostic_replay v12_legacy_static_guidance \
  --progress > >(tee "$V12_ROOT/logs/v12_fast.log") 2>&1

python scripts/summarize_v12_results.py \
  --entry "v12_full=$V12_ROOT/runs/full" \
  --entry "v11_reference=$V12_ROOT/runs/v11_reference_runtime" \
  --entry "v2_reference=$V12_ROOT/runs/v2_reference_runtime" \
  --entry "structural_only=$V12_ROOT/runs/no_typed_viability" \
  --entry "no_kernel=$V12_ROOT/runs/no_viability_kernel" \
  --entry "no_lazy=$V12_ROOT/runs/no_lazy_diagnostic_replay" \
  --entry "legacy_guidance=$V12_ROOT/runs/v12_legacy_static_guidance" \
  --output "$V12_ROOT/v12_fast_summary.csv" | tee "$V12_ROOT/logs/summary.log"

for spec in \
  "v11_reference_runtime:v11_vs_v12" \
  "v2_reference_runtime:v2_vs_v12" \
  "no_typed_viability:structural_vs_v12" \
  "no_lazy_diagnostic_replay:no_lazy_vs_v12" \
  "v12_legacy_static_guidance:legacy_vs_v12"; do
  ref="${spec%%:*}"; name="${spec##*:}"
  python scripts/compare_search_efficiency.py \
    --reference "$V12_ROOT/runs/$ref" --candidate "$V12_ROOT/runs/full" \
    --output "$V12_ROOT/${name}_paired.json" > "$V12_ROOT/logs/${name}.log"
done

python scripts/compare_diagnostic_exactness.py \
  --reference "$V12_ROOT/runs/v11_reference_runtime" --candidate "$V12_ROOT/runs/full" \
  --output "$V12_ROOT/v11_vs_v12_diagnostic_exactness.json" \
  | tee "$V12_ROOT/logs/diagnostic_exactness.log"

# Counterbalanced repeated timing. The V10/V11 learned-guidance micro-effect
# changed sign across runs; V12 therefore does not accept one fixed order as a
# timing claim. All blocks are serial on the same GPU and same request subset.
orders=(
  "v2_reference_runtime v11_reference_runtime full v12_legacy_static_guidance"
  "full v12_legacy_static_guidance v11_reference_runtime v2_reference_runtime"
  "v12_legacy_static_guidance v2_reference_runtime full v11_reference_runtime"
)
for i in 0 1 2; do
  out="$V12_ROOT/timing_repeat_$i"
  # shellcheck disable=SC2086
  CUDA_VISIBLE_DEVICES="$GPU0" python scripts/run_ablations.py \
    --dataset_dir "$HYBRID_TEST" --output_dir "$out" \
    --trajectory_mode mock_strict --casa_mode learned --casa_checkpoint "$CASA_CHECKPOINT" --casa_device cuda:0 \
    --algorithm_version V12 --evidence_grounded_runtime \
    --episode_limit "$EPISODE_LIMIT" --episode_seed "$EPISODE_SEED" \
    --variants ${orders[$i]} --progress \
    > >(tee "$V12_ROOT/logs/timing_repeat_${i}.log") 2>&1
done

python scripts/compare_repeated_timing.py \
  --reference "$V12_ROOT/timing_repeat_0/v11_reference_runtime" "$V12_ROOT/timing_repeat_1/v11_reference_runtime" "$V12_ROOT/timing_repeat_2/v11_reference_runtime" \
  --candidate "$V12_ROOT/timing_repeat_0/full" "$V12_ROOT/timing_repeat_1/full" "$V12_ROOT/timing_repeat_2/full" \
  --output "$V12_ROOT/repeated_v11_vs_v12.json" > "$V12_ROOT/logs/repeated_v11_vs_v12.log"
python scripts/compare_repeated_timing.py \
  --reference "$V12_ROOT/timing_repeat_0/v2_reference_runtime" "$V12_ROOT/timing_repeat_1/v2_reference_runtime" "$V12_ROOT/timing_repeat_2/v2_reference_runtime" \
  --candidate "$V12_ROOT/timing_repeat_0/full" "$V12_ROOT/timing_repeat_1/full" "$V12_ROOT/timing_repeat_2/full" \
  --output "$V12_ROOT/repeated_v2_vs_v12.json" > "$V12_ROOT/logs/repeated_v2_vs_v12.log"
python scripts/compare_repeated_timing.py \
  --reference "$V12_ROOT/timing_repeat_0/v12_legacy_static_guidance" "$V12_ROOT/timing_repeat_1/v12_legacy_static_guidance" "$V12_ROOT/timing_repeat_2/v12_legacy_static_guidance" \
  --candidate "$V12_ROOT/timing_repeat_0/full" "$V12_ROOT/timing_repeat_1/full" "$V12_ROOT/timing_repeat_2/full" \
  --output "$V12_ROOT/repeated_legacy_vs_v12.json" > "$V12_ROOT/logs/repeated_legacy_vs_v12.log"

python scripts/assess_v12_fast.py \
  --full "$V12_ROOT/runs/full" --v11 "$V12_ROOT/runs/v11_reference_runtime" --v2 "$V12_ROOT/runs/v2_reference_runtime" \
  --structural "$V12_ROOT/runs/no_typed_viability" --no-kernel "$V12_ROOT/runs/no_viability_kernel" \
  --no-lazy "$V12_ROOT/runs/no_lazy_diagnostic_replay" --legacy "$V12_ROOT/runs/v12_legacy_static_guidance" \
  --v11-v12 "$V12_ROOT/v11_vs_v12_paired.json" --v2-v12 "$V12_ROOT/v2_vs_v12_paired.json" \
  --structural-v12 "$V12_ROOT/structural_vs_v12_paired.json" --no-lazy-v12 "$V12_ROOT/no_lazy_vs_v12_paired.json" \
  --legacy-v12 "$V12_ROOT/legacy_vs_v12_paired.json" --exact-v11-v12 "$V12_ROOT/v11_vs_v12_diagnostic_exactness.json" \
  --repeat-v11-v12 "$V12_ROOT/repeated_v11_vs_v12.json" --repeat-v2-v12 "$V12_ROOT/repeated_v2_vs_v12.json" \
  --repeat-legacy-v12 "$V12_ROOT/repeated_legacy_vs_v12.json" \
  --output "$V12_ROOT/v12_fast_gate.json" | tee "$V12_ROOT/logs/gate.log"

echo "V12_FAST_ROOT=$V12_ROOT"
echo "Only v12_fast_gate.json status == GO permits the 997-episode V12 confirmation. CQ-HPT starts only after that full confirmation."
