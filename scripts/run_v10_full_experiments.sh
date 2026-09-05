#!/usr/bin/env bash
set -euo pipefail
CAP_HOME="${CAP_HOME:-/home/senzeyu2/code/CapPlan}"
DATA_ROOT="${DATA_ROOT:-/data0/senzeyu2/dataset/CapPlan/data}"
HYBRID_TEST="${HYBRID_TEST:-$DATA_ROOT/outputs/datasets/abilitybench_av_hybrid_test}"
GPU0="${GPU0:-0}"; SEED="${SEED:-13}"
CASA_CHECKPOINT="${CASA_CHECKPOINT:-$CAP_HOME/outputs/models/casa_relation_mlp_seed13/checkpoint.pt}"
V10_ROOT="${V10_ROOT:-$CAP_HOME/outputs/eval/v10_full_seed${SEED}}"
mkdir -p "$V10_ROOT"/{runs,logs}; cd "$CAP_HOME"
[[ -f "$CASA_CHECKPOINT" ]] || { echo "missing CASA checkpoint: $CASA_CHECKPOINT" >&2; exit 2; }
CUDA_VISIBLE_DEVICES="$GPU0" python scripts/run_ablations.py \
  --dataset_dir "$HYBRID_TEST" --output_dir "$V10_ROOT/runs" --trajectory_mode mock_strict \
  --casa_mode learned --casa_checkpoint "$CASA_CHECKPOINT" --casa_device cuda:0 --algorithm_version V10 --evidence_grounded_runtime \
  --variants full v9_reference_runtime v2_reference_runtime v5_reference_runtime no_typed_viability no_semnaive_delta_propagation no_packed_frontier_dominance no_lazy_diagnostic_replay no_learned_feasibility_guidance \
  --progress > >(tee "$V10_ROOT/logs/v10_full.log") 2>&1
python scripts/summarize_v10_results.py \
  --entry "v10_full=$V10_ROOT/runs/full" --entry "v9_reference=$V10_ROOT/runs/v9_reference_runtime" --entry "v2_reference=$V10_ROOT/runs/v2_reference_runtime" --entry "v5_reference=$V10_ROOT/runs/v5_reference_runtime" \
  --entry "v10_structural_only=$V10_ROOT/runs/no_typed_viability" --entry "v10_no_delta=$V10_ROOT/runs/no_semnaive_delta_propagation" --entry "v10_no_packed=$V10_ROOT/runs/no_packed_frontier_dominance" --entry "v10_no_lazy=$V10_ROOT/runs/no_lazy_diagnostic_replay" \
  --output "$V10_ROOT/v10_full_summary.csv" | tee "$V10_ROOT/logs/summary.log"
echo "V10_FULL_ROOT=$V10_ROOT"
