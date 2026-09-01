#!/usr/bin/env bash
set -euo pipefail

: "${CAP_HOME:=/home/senzeyu2/code/CapPlan}"
: "${DATA_ROOT:=/data0/senzeyu2/dataset/CapPlan/data}"
: "${SEED:=13}"
: "${CUDA_DEVICE:=0}"

HYBRID_TEST="${HYBRID_TEST:-$DATA_ROOT/outputs/datasets/abilitybench_av_hybrid_test}"
V1_ROOT="${V1_ROOT:-$CAP_HOME/outputs/eval/v1}"
MODEL_DIR="$V1_ROOT/models/casa_relation_mlp_seed${SEED}"
CKPT="${CHECKPOINT:-$MODEL_DIR/checkpoint.pt}"
LOG_DIR="$V1_ROOT/logs"
mkdir -p "$V1_ROOT/test/seed${SEED}" "$V1_ROOT/ablations/seed${SEED}" "$V1_ROOT/diagnostics/head_isolation_seed${SEED}" "$LOG_DIR"
cd "$CAP_HOME"

if [[ ! -f "$CKPT" ]]; then
  echo "missing checkpoint: $CKPT" >&2
  echo "run scripts/run_v1_train.sh first" >&2
  exit 2
fi

export CUDA_VISIBLE_DEVICES="$CUDA_DEVICE"

# Always evaluate the neural heads for the exact checkpoint used by the planner.
# This is essential for separating representation/head failure from TSBS failure.
python scripts/evaluate_casa_checkpoint.py \
  --dataset_dir "$DATA_ROOT/outputs/datasets/abilitybench_av_hybrid_all" \
  --checkpoint "$CKPT" \
  --split test \
  --device cuda:0 \
  --batch_size 8192 \
  --output "$V1_ROOT/diagnostics/casa_heads_seed${SEED}.json" \
  --progress \
  2>&1 | tee "$LOG_DIR/casa_head_eval_seed${SEED}.log"

echo "[V1] full test seed=$SEED physical_gpu=$CUDA_DEVICE checkpoint=$CKPT"
python scripts/run_closed_loop_eval.py \
  --dataset_dir "$HYBRID_TEST" \
  --output_dir "$V1_ROOT/test/seed${SEED}" \
  --planner capplan \
  --ablation full \
  --trajectory_mode mock_strict \
  --casa_mode learned \
  --casa_checkpoint "$CKPT" \
  --casa_device cuda:0 \
  --progress \
  2>&1 | tee "$LOG_DIR/test_seed${SEED}.log"

echo "[V1] main mechanism ablations"
python scripts/run_ablations.py \
  --dataset_dir "$HYBRID_TEST" \
  --output_dir "$V1_ROOT/ablations/seed${SEED}" \
  --trajectory_mode mock_strict \
  --casa_mode learned \
  --casa_checkpoint "$CKPT" \
  --casa_device cuda:0 \
  --variants \
    full \
    no_capability_compiler \
    no_service_automaton \
    no_casa_net_transitions \
    no_typed_resource_ledger \
    no_conservative_margins \
    no_completion_value_guidance \
    soft_only_capability \
  --progress \
  2>&1 | tee "$LOG_DIR/ablations_seed${SEED}.log"

echo "[V1] learned-head isolation diagnostics"
python scripts/run_ablations.py \
  --dataset_dir "$HYBRID_TEST" \
  --output_dir "$V1_ROOT/diagnostics/head_isolation_seed${SEED}" \
  --trajectory_mode mock_strict \
  --casa_mode learned \
  --casa_checkpoint "$CKPT" \
  --casa_device cuda:0 \
  --variants \
    no_learned_demand \
    no_learned_uncertainty \
    no_learned_availability \
  --progress \
  2>&1 | tee "$LOG_DIR/head_isolation_seed${SEED}.log"

python scripts/summarize_v1_results.py \
  --v1_root "$V1_ROOT" \
  --seed "$SEED" \
  2>&1 | tee "$LOG_DIR/summary_seed${SEED}.log"

echo "[V1] experiments complete under $V1_ROOT"
