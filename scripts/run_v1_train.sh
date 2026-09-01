#!/usr/bin/env bash
set -euo pipefail

: "${CAP_HOME:=/home/senzeyu2/code/CapPlan}"
: "${DATA_ROOT:=/data0/senzeyu2/dataset/CapPlan/data}"
: "${SEED:=13}"
: "${CUDA_DEVICE:=0}"
: "${EPOCHS:=20}"
: "${BATCH_SIZE:=1024}"
: "${EVAL_BATCH_SIZE:=8192}"

HYBRID_ALL="${HYBRID_ALL:-$DATA_ROOT/outputs/datasets/abilitybench_av_hybrid_all}"
V1_ROOT="${V1_ROOT:-$CAP_HOME/outputs/eval/v1}"
MODEL_DIR="$V1_ROOT/models/casa_relation_mlp_seed${SEED}"
LOG_DIR="$V1_ROOT/logs"
mkdir -p "$MODEL_DIR" "$LOG_DIR" "$V1_ROOT/diagnostics"
cd "$CAP_HOME"

export CUDA_VISIBLE_DEVICES="$CUDA_DEVICE"

echo "[V1] training seed=$SEED physical_gpu=$CUDA_DEVICE dataset=$HYBRID_ALL"
python scripts/train_casa.py \
  --dataset_dir "$HYBRID_ALL" \
  --output_dir "$MODEL_DIR" \
  --epochs "$EPOCHS" \
  --batch_size "$BATCH_SIZE" \
  --eval_batch_size "$EVAL_BATCH_SIZE" \
  --lr 1e-3 \
  --seed "$SEED" \
  --device cuda:0 \
  --model_type relation_mlp \
  --feature_policy paper_safe_v2 \
  --predict_typed_demand \
  --predict_uncertainty \
  --predict_availability \
  --value_target skeleton \
  --action_balanced_sampler \
  --save_calibration_report \
  --edge_pos_weight auto \
  --amp off \
  --no-tf32 \
  --matmul_precision highest \
  --fused_adamw off \
  --progress \
  2>&1 | tee "$LOG_DIR/train_seed${SEED}.log"

# Neural-head quality is evaluated separately from TSBS so a downstream search
# collapse cannot hide whether CASA itself learned the frozen labels.
python scripts/evaluate_casa_checkpoint.py \
  --dataset_dir "$HYBRID_ALL" \
  --checkpoint "$MODEL_DIR/checkpoint.pt" \
  --split test \
  --device cuda:0 \
  --batch_size "$EVAL_BATCH_SIZE" \
  --output "$V1_ROOT/diagnostics/casa_heads_seed${SEED}.json" \
  --progress \
  2>&1 | tee "$LOG_DIR/casa_head_eval_seed${SEED}.log"

echo "[V1] training complete: $MODEL_DIR"
