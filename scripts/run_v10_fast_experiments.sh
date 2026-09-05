#!/usr/bin/env bash
set -euo pipefail
CAP_HOME="${CAP_HOME:-/home/senzeyu2/code/CapPlan}"
DATA_ROOT="${DATA_ROOT:-/data0/senzeyu2/dataset/CapPlan/data}"
HYBRID_TEST="${HYBRID_TEST:-$DATA_ROOT/outputs/datasets/abilitybench_av_hybrid_test}"
GPU0="${GPU0:-0}"; SEED="${SEED:-13}"
EPISODE_LIMIT="${EPISODE_LIMIT:-256}"; EPISODE_SEED="${EPISODE_SEED:-13}"
CASA_CHECKPOINT="${CASA_CHECKPOINT:-$CAP_HOME/outputs/models/casa_relation_mlp_seed13/checkpoint.pt}"
V10_ROOT="${V10_ROOT:-$CAP_HOME/outputs/eval/v10_fast_seed${SEED}}"
mkdir -p "$V10_ROOT"/{runs,logs}; cd "$CAP_HOME"
[[ -f "$CASA_CHECKPOINT" ]] || { echo "missing CASA checkpoint: $CASA_CHECKPOINT" >&2; exit 2; }

# Timing-critical controls are intentionally serial.  V10 changes only exact
# backward-kernel construction; the frozen CASA checkpoint is reused.
CUDA_VISIBLE_DEVICES="$GPU0" python scripts/run_ablations.py \
  --dataset_dir "$HYBRID_TEST" --output_dir "$V10_ROOT/runs" \
  --trajectory_mode mock_strict --casa_mode learned --casa_checkpoint "$CASA_CHECKPOINT" --casa_device cuda:0 \
  --algorithm_version V10 --evidence_grounded_runtime \
  --episode_limit "$EPISODE_LIMIT" --episode_seed "$EPISODE_SEED" \
  --variants full v9_reference_runtime v2_reference_runtime v5_reference_runtime no_typed_viability no_viability_kernel no_semnaive_delta_propagation no_packed_frontier_dominance no_lazy_diagnostic_replay no_learned_feasibility_guidance \
  --progress > >(tee "$V10_ROOT/logs/v10_fast.log") 2>&1

python scripts/summarize_v10_results.py \
  --entry "v10_full=$V10_ROOT/runs/full" --entry "v9_reference=$V10_ROOT/runs/v9_reference_runtime" \
  --entry "v2_reference=$V10_ROOT/runs/v2_reference_runtime" --entry "v5_reference=$V10_ROOT/runs/v5_reference_runtime" \
  --entry "v10_structural_only=$V10_ROOT/runs/no_typed_viability" --entry "v10_no_kernel=$V10_ROOT/runs/no_viability_kernel" \
  --entry "v10_no_delta=$V10_ROOT/runs/no_semnaive_delta_propagation" --entry "v10_no_packed=$V10_ROOT/runs/no_packed_frontier_dominance" \
  --entry "v10_no_lazy=$V10_ROOT/runs/no_lazy_diagnostic_replay" --entry "v10_no_static_guidance=$V10_ROOT/runs/no_learned_feasibility_guidance" \
  --output "$V10_ROOT/v10_fast_summary.csv" | tee "$V10_ROOT/logs/summary.log"

for pair in "v9_reference_runtime:v10_vs_v9" "v2_reference_runtime:v10_vs_v2" "v5_reference_runtime:v10_vs_v5" "no_typed_viability:v10_vs_structural" "no_semnaive_delta_propagation:v10_vs_no_delta" "no_packed_frontier_dominance:v10_vs_no_packed" "no_lazy_diagnostic_replay:v10_vs_no_lazy"; do
  ref="${pair%%:*}"; name="${pair##*:}"
  python scripts/compare_search_efficiency.py --reference "$V10_ROOT/runs/$ref" --candidate "$V10_ROOT/runs/full" --output "$V10_ROOT/${name}_paired.json" | tee "$V10_ROOT/logs/${name}.log"
done

python scripts/assess_v10_fast.py \
  --full "$V10_ROOT/runs/full" --v9 "$V10_ROOT/runs/v9_reference_runtime" --v2 "$V10_ROOT/runs/v2_reference_runtime" --v5 "$V10_ROOT/runs/v5_reference_runtime" \
  --structural "$V10_ROOT/runs/no_typed_viability" --no-kernel "$V10_ROOT/runs/no_viability_kernel" --no-delta "$V10_ROOT/runs/no_semnaive_delta_propagation" --no-packed "$V10_ROOT/runs/no_packed_frontier_dominance" --no-lazy "$V10_ROOT/runs/no_lazy_diagnostic_replay" \
  --v10-v9 "$V10_ROOT/v10_vs_v9_paired.json" --v10-v2 "$V10_ROOT/v10_vs_v2_paired.json" --v10-v5 "$V10_ROOT/v10_vs_v5_paired.json" --v10-structural "$V10_ROOT/v10_vs_structural_paired.json" \
  --v10-no-delta "$V10_ROOT/v10_vs_no_delta_paired.json" --v10-no-packed "$V10_ROOT/v10_vs_no_packed_paired.json" --v10-no-lazy "$V10_ROOT/v10_vs_no_lazy_paired.json" \
  --output "$V10_ROOT/v10_fast_gate.json" | tee "$V10_ROOT/logs/gate.log"
echo "V10_FAST_ROOT=$V10_ROOT"
echo "Run V10 full only when v10_fast_gate.json status == GO"
