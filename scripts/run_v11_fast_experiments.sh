#!/usr/bin/env bash
set -euo pipefail
CAP_HOME="${CAP_HOME:-/home/senzeyu2/code/CapPlan}"
DATA_ROOT="${DATA_ROOT:-/data0/senzeyu2/dataset/CapPlan/data}"
HYBRID_TEST="${HYBRID_TEST:-$DATA_ROOT/outputs/datasets/abilitybench_av_hybrid_test}"
GPU0="${GPU0:-0}"; SEED="${SEED:-13}"
EPISODE_LIMIT="${EPISODE_LIMIT:-256}"; EPISODE_SEED="${EPISODE_SEED:-13}"
CASA_CHECKPOINT="${CASA_CHECKPOINT:-$CAP_HOME/outputs/models/casa_relation_mlp_seed13/checkpoint.pt}"
V11_ROOT="${V11_ROOT:-$CAP_HOME/outputs/eval/v11_fast_seed${SEED}}"
mkdir -p "$V11_ROOT"/{runs,timing_recheck,logs}; cd "$CAP_HOME"
[[ -f "$CASA_CHECKPOINT" ]] || { echo "missing CASA checkpoint: $CASA_CHECKPOINT" >&2; exit 2; }

# V11-A (confirmatory): exact V10 SN-CPK + proof-on-demand, but retire the
# transition-static learned ordering that V10-fast showed to be net slower.
# V11-B native quotient composition is exploratory and cannot determine GO/STOP.
CUDA_VISIBLE_DEVICES="$GPU0" python scripts/run_ablations.py \
  --dataset_dir "$HYBRID_TEST" --output_dir "$V11_ROOT/runs" \
  --trajectory_mode mock_strict --casa_mode learned --casa_checkpoint "$CASA_CHECKPOINT" --casa_device cuda:0 \
  --algorithm_version V11 --evidence_grounded_runtime \
  --episode_limit "$EPISODE_LIMIT" --episode_seed "$EPISODE_SEED" \
  --variants full v10_reference_runtime v2_reference_runtime v5_reference_runtime no_typed_viability no_viability_kernel v11_legacy_static_guidance no_lazy_diagnostic_replay v11_native_quotient_experimental v11_native_no_fused \
  --progress > >(tee "$V11_ROOT/logs/v11_fast.log") 2>&1

python scripts/summarize_v11_results.py \
  --entry "v11_full=$V11_ROOT/runs/full" --entry "v10_reference=$V11_ROOT/runs/v10_reference_runtime" \
  --entry "v2_reference=$V11_ROOT/runs/v2_reference_runtime" --entry "v5_reference=$V11_ROOT/runs/v5_reference_runtime" \
  --entry "v11_structural_only=$V11_ROOT/runs/no_typed_viability" --entry "v11_no_kernel=$V11_ROOT/runs/no_viability_kernel" \
  --entry "v11_legacy_guidance=$V11_ROOT/runs/v11_legacy_static_guidance" --entry "v11_no_lazy=$V11_ROOT/runs/no_lazy_diagnostic_replay" \
  --entry "v11_native_experimental=$V11_ROOT/runs/v11_native_quotient_experimental" --entry "v11_native_no_fused=$V11_ROOT/runs/v11_native_no_fused" \
  --output "$V11_ROOT/v11_fast_summary.csv" | tee "$V11_ROOT/logs/summary.log"

for pair in \
  "v10_reference_runtime:v11_vs_v10" \
  "v2_reference_runtime:v11_vs_v2" \
  "v5_reference_runtime:v11_vs_v5" \
  "no_typed_viability:v11_vs_structural" \
  "v11_legacy_static_guidance:v11_vs_legacy" \
  "no_lazy_diagnostic_replay:v11_vs_no_lazy" \
  "full:native_vs_v11" \
  "v11_native_no_fused:native_vs_no_fused"; do
  ref="${pair%%:*}"; name="${pair##*:}"
  cand="full"
  [[ "$name" == native_vs_v11 || "$name" == native_vs_no_fused ]] && cand="v11_native_quotient_experimental"
  python scripts/compare_search_efficiency.py --reference "$V11_ROOT/runs/$ref" --candidate "$V11_ROOT/runs/$cand" --output "$V11_ROOT/${name}_paired.json" | tee "$V11_ROOT/logs/${name}.log"
done

# Opposite-order timing recheck: the V10 gate missed by only ~3 ms, so freeze is
# not allowed to hinge on one fixed variant order or transient warm-up state.
CUDA_VISIBLE_DEVICES="$GPU0" python scripts/run_ablations.py \
  --dataset_dir "$HYBRID_TEST" --output_dir "$V11_ROOT/timing_recheck" \
  --trajectory_mode mock_strict --casa_mode learned --casa_checkpoint "$CASA_CHECKPOINT" --casa_device cuda:0 \
  --algorithm_version V11 --evidence_grounded_runtime \
  --episode_limit "$EPISODE_LIMIT" --episode_seed "$EPISODE_SEED" \
  --variants v2_reference_runtime v10_reference_runtime full --progress \
  > >(tee "$V11_ROOT/logs/timing_recheck.log") 2>&1
python scripts/compare_search_efficiency.py --reference "$V11_ROOT/timing_recheck/v2_reference_runtime" --candidate "$V11_ROOT/timing_recheck/full" --output "$V11_ROOT/recheck_v11_vs_v2_paired.json" >/dev/null
python scripts/compare_search_efficiency.py --reference "$V11_ROOT/timing_recheck/v10_reference_runtime" --candidate "$V11_ROOT/timing_recheck/full" --output "$V11_ROOT/recheck_v11_vs_v10_paired.json" >/dev/null

python scripts/assess_v11_fast.py \
  --full "$V11_ROOT/runs/full" --v10 "$V11_ROOT/runs/v10_reference_runtime" --v2 "$V11_ROOT/runs/v2_reference_runtime" --v5 "$V11_ROOT/runs/v5_reference_runtime" \
  --structural "$V11_ROOT/runs/no_typed_viability" --no-kernel "$V11_ROOT/runs/no_viability_kernel" \
  --legacy-guidance "$V11_ROOT/runs/v11_legacy_static_guidance" --no-lazy "$V11_ROOT/runs/no_lazy_diagnostic_replay" \
  --native "$V11_ROOT/runs/v11_native_quotient_experimental" --native-no-fused "$V11_ROOT/runs/v11_native_no_fused" \
  --v11-v10 "$V11_ROOT/v11_vs_v10_paired.json" --v11-v2 "$V11_ROOT/v11_vs_v2_paired.json" --v11-v5 "$V11_ROOT/v11_vs_v5_paired.json" \
  --v11-structural "$V11_ROOT/v11_vs_structural_paired.json" --v11-legacy "$V11_ROOT/v11_vs_legacy_paired.json" --v11-no-lazy "$V11_ROOT/v11_vs_no_lazy_paired.json" \
  --native-v11 "$V11_ROOT/native_vs_v11_paired.json" --native-no-fused-pair "$V11_ROOT/native_vs_no_fused_paired.json" \
  --recheck-full "$V11_ROOT/timing_recheck/full" --recheck-v2 "$V11_ROOT/timing_recheck/v2_reference_runtime" --recheck-v10 "$V11_ROOT/timing_recheck/v10_reference_runtime" \
  --recheck-v11-v2 "$V11_ROOT/recheck_v11_vs_v2_paired.json" --recheck-v11-v10 "$V11_ROOT/recheck_v11_vs_v10_paired.json" \
  --output "$V11_ROOT/v11_fast_gate.json" | tee "$V11_ROOT/logs/gate.log"
echo "V11_FAST_ROOT=$V11_ROOT"
echo "Only confirmatory v11_fast_gate.json status == GO permits CQ-HPT to become the next main algorithm version."
