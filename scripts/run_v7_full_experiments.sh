#!/usr/bin/env bash
set -euo pipefail
CAP_HOME="${CAP_HOME:-/home/senzeyu2/code/CapPlan}"
DATA_ROOT="${DATA_ROOT:-/data0/senzeyu2/dataset/CapPlan/data}"
HYBRID_TEST="${HYBRID_TEST:-$DATA_ROOT/outputs/datasets/abilitybench_av_hybrid_test}"
GPU0="${GPU0:-0}"; GPU1="${GPU1:-1}"; SEED="${SEED:-13}"
CASA_CHECKPOINT="${CASA_CHECKPOINT:-$CAP_HOME/outputs/models/casa_relation_mlp_seed13/checkpoint.pt}"
V7_ROOT="${V7_ROOT:-$CAP_HOME/outputs/eval/v7_full_seed${SEED}}"
mkdir -p "$V7_ROOT"/{gpu0,gpu1,logs}
cd "$CAP_HOME"
[[ -f "$CASA_CHECKPOINT" ]] || { echo "missing CASA checkpoint: $CASA_CHECKPOINT" >&2; exit 2; }

CUDA_VISIBLE_DEVICES="$GPU0" python scripts/run_ablations.py \
  --dataset_dir "$HYBRID_TEST" --output_dir "$V7_ROOT/gpu0" \
  --trajectory_mode mock_strict --casa_mode learned --casa_checkpoint "$CASA_CHECKPOINT" --casa_device cuda:0 \
  --algorithm_version V7 --evidence_grounded_runtime \
  --variants full v6_reference_runtime v5_reference_runtime v2_reference_runtime no_typed_viability no_viability_kernel \
  --progress > >(tee "$V7_ROOT/logs/gpu0.log") 2>&1 &
P0=$!
CUDA_VISIBLE_DEVICES="$GPU1" python scripts/run_ablations.py \
  --dataset_dir "$HYBRID_TEST" --output_dir "$V7_ROOT/gpu1" \
  --trajectory_mode mock_strict --casa_mode learned --casa_checkpoint "$CASA_CHECKPOINT" --casa_device cuda:0 \
  --algorithm_version V7 --evidence_grounded_runtime \
  --variants no_rejection_kernel no_learned_feasibility_guidance no_evidence_grounding no_conservative_margins \
  --progress > >(tee "$V7_ROOT/logs/gpu1.log") 2>&1 &
P1=$!
wait "$P0"; wait "$P1"

python scripts/summarize_v7_results.py \
  --entry "v7_full=$V7_ROOT/gpu0/full" --entry "v6_reference=$V7_ROOT/gpu0/v6_reference_runtime" \
  --entry "v5_reference=$V7_ROOT/gpu0/v5_reference_runtime" --entry "v2_reference=$V7_ROOT/gpu0/v2_reference_runtime" \
  --entry "v7_structural_only=$V7_ROOT/gpu0/no_typed_viability" --entry "v7_no_kernel=$V7_ROOT/gpu0/no_viability_kernel" \
  --entry "v7_no_rejection=$V7_ROOT/gpu1/no_rejection_kernel" --entry "v7_no_static=$V7_ROOT/gpu1/no_learned_feasibility_guidance" \
  --entry "v7_no_evidence=$V7_ROOT/gpu1/no_evidence_grounding" --entry "v7_no_margin=$V7_ROOT/gpu1/no_conservative_margins" \
  --output "$V7_ROOT/v7_full_summary.csv" | tee "$V7_ROOT/logs/summary.log"

echo "[V7_FULL] complete: $V7_ROOT"
