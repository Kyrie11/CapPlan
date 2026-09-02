#!/usr/bin/env bash
set -euo pipefail

# Standalone V3 training entry point.  It does not invoke any other run_v*.sh.
CAP_HOME="${CAP_HOME:-/home/senzeyu2/code/CapPlan}"
DATA_ROOT="${DATA_ROOT:-/data0/senzeyu2/dataset/CapPlan/data}"
HYBRID_ALL="${HYBRID_ALL:-$DATA_ROOT/outputs/datasets/abilitybench_av_hybrid_all}"
GPU0="${GPU0:-0}"
GPU1="${GPU1:-1}"
SEED="${SEED:-13}"
TRAIN_EPISODE_LIMIT="${TRAIN_EPISODE_LIMIT:-1500}"
VAL_EPISODE_LIMIT="${VAL_EPISODE_LIMIT:-500}"
MODEL_ROOT="${MODEL_ROOT:-$CAP_HOME/outputs/models/v3_frontier_seed${SEED}}"
LOG_ROOT="${LOG_ROOT:-$CAP_HOME/outputs/logs/v3_frontier_seed${SEED}}"
mkdir -p "$MODEL_ROOT" "$LOG_ROOT"
cd "$CAP_HOME"

common=(
  --dataset_dir "$HYBRID_ALL"
  --train_episode_limit "$TRAIN_EPISODE_LIMIT"
  --val_episode_limit "$VAL_EPISODE_LIMIT"
  --max_negatives_per_frontier 8
  --epochs 12 --batch_size 4096 --hidden_dim 128 --lr 1e-3
  --seed "$SEED"
)

CUDA_VISIBLE_DEVICES="$GPU0" python scripts/train_frontier_ranker.py \
  "${common[@]}" --device cuda:0 --feature_mode full --objective pairwise \
  --output_dir "$MODEL_ROOT/full_pairwise" \
  > >(tee "$LOG_ROOT/full_pairwise.log") 2>&1 &
P0=$!

CUDA_VISIBLE_DEVICES="$GPU1" python scripts/train_frontier_ranker.py \
  "${common[@]}" --device cuda:0 --feature_mode structural --objective pairwise \
  --output_dir "$MODEL_ROOT/structural_pairwise" \
  > >(tee "$LOG_ROOT/structural_pairwise.log") 2>&1 &
P1=$!

wait "$P0"
wait "$P1"

# Same full state-dependent features but a global BCE objective.  This isolates
# the contribution of frontier-relative pairwise supervision from representation.
CUDA_VISIBLE_DEVICES="$GPU1" python scripts/train_frontier_ranker.py \
  "${common[@]}" --device cuda:0 --feature_mode full --objective bce \
  --output_dir "$MODEL_ROOT/full_bce" \
  > >(tee "$LOG_ROOT/full_bce.log") 2>&1

echo "[V3_TRAIN] complete: $MODEL_ROOT"
