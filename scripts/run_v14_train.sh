#!/usr/bin/env bash
set -euo pipefail
CAP_HOME="${CAP_HOME:-$(cd "$(dirname "$0")/.." && pwd)}"
DATA_ROOT="${DATA_ROOT:?set DATA_ROOT}"
DATASET_DIR="${DATASET_DIR:-$DATA_ROOT/outputs/datasets/abilitybench_av_hybrid_all}"
HYBRID_TEST="${HYBRID_TEST:-$DATA_ROOT/outputs/datasets/abilitybench_av_hybrid_test}"
SEED="${SEED:-13}"
GPU0="${GPU0:-0}"; GPU1="${GPU1:-1}"
CASA_CHECKPOINT="${CASA_CHECKPOINT:-$CAP_HOME/outputs/models/casa_relation_mlp_seed13/checkpoint.pt}"
MODEL_ROOT="${MODEL_ROOT:-$CAP_HOME/outputs/models/v14_cqhpt_seed${SEED}}"
TEACHER_ROOT="${TEACHER_ROOT:-$CAP_HOME/outputs/teachers/v14_cqhpt_seed${SEED}}"
TRAIN_EPISODE_LIMIT="${TRAIN_EPISODE_LIMIT:-1500}"
VAL_EPISODE_LIMIT="${VAL_EPISODE_LIMIT:-500}"
TRAIN_MAX_FRONTIERS="${TRAIN_MAX_FRONTIERS:-30000}"
VAL_MAX_FRONTIERS="${VAL_MAX_FRONTIERS:-10000}"
mkdir -p "$MODEL_ROOT" "$TEACHER_ROOT" "$CAP_HOME/outputs/logs"
cd "$CAP_HOME"

if [[ ! -f "$DATASET_DIR/cqhpt_evidence_cache.jsonl" ]]; then
  python scripts/prepare_cqhpt_evidence_cache.py \
    --dataset_dir "$DATASET_DIR" --splits train val test --seed "$SEED" \
    2>&1 | tee "$CAP_HOME/outputs/logs/v14_prepare_evidence_cache.log"
fi

# Evaluation uses the canonical frozen test split. Prepare its compact raw-evidence
# sidecar now so fast/full evaluation never samples train/val episodes and never
# reparses full accessibility graphs at runtime.
if [[ ! -f "$HYBRID_TEST/cqhpt_evidence_cache.jsonl" ]]; then
  python scripts/prepare_cqhpt_evidence_cache.py \
    --dataset_dir "$HYBRID_TEST" --splits test --seed "$SEED" \
    2>&1 | tee "$CAP_HOME/outputs/logs/v14_prepare_test_evidence_cache.log"
fi

CUDA_VISIBLE_DEVICES="$GPU0" python scripts/export_cqhpt_teacher.py \
  --dataset_dir "$DATASET_DIR" --split train --output "$TEACHER_ROOT/train.jsonl.gz" \
  --casa_checkpoint "$CASA_CHECKPOINT" --episode_limit "$TRAIN_EPISODE_LIMIT" --seed "$SEED" \
  --max_frontiers "$TRAIN_MAX_FRONTIERS" --device auto \
  2>&1 | tee "$CAP_HOME/outputs/logs/v14_teacher_train.log"
CUDA_VISIBLE_DEVICES="$GPU0" python scripts/export_cqhpt_teacher.py \
  --dataset_dir "$DATASET_DIR" --split val --output "$TEACHER_ROOT/val.jsonl.gz" \
  --casa_checkpoint "$CASA_CHECKPOINT" --episode_limit "$VAL_EPISODE_LIMIT" --seed "$SEED" \
  --max_frontiers "$VAL_MAX_FRONTIERS" --device auto \
  2>&1 | tee "$CAP_HOME/outputs/logs/v14_teacher_val.log"

train_one() {
  local mode="$1" gpu="$2"
  CUDA_VISIBLE_DEVICES="$gpu" python scripts/train_cqhpt.py \
    --train_teacher "$TEACHER_ROOT/train.jsonl.gz" --val_teacher "$TEACHER_ROOT/val.jsonl.gz" \
    --output_dir "$MODEL_ROOT/$mode" --mode "$mode" --seed "$SEED" --device auto \
    2>&1 | tee "$CAP_HOME/outputs/logs/v14_train_${mode}.log"
}
train_one full "$GPU0" & P0=$!
train_one late_fusion "$GPU1" & P1=$!
wait "$P0"; wait "$P1"
train_one neutral_query "$GPU0" & P0=$!
train_one query_only "$GPU1" & P1=$!
wait "$P0"; wait "$P1"

echo "V14_TRAIN=PASS"
echo "MODEL_ROOT=$MODEL_ROOT"
