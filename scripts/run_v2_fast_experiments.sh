#!/usr/bin/env bash
set -euo pipefail
: "${CAP_HOME:=/home/senzeyu2/code/CapPlan}"
: "${DATA_ROOT:=/data0/senzeyu2/dataset/CapPlan/data}"
: "${SEED:=13}"
: "${GPU0:=0}"
: "${GPU1:=1}"
: "${EPISODE_LIMIT:=256}"
: "${EPISODE_SEED:=13}"
: "${CHECKPOINT:=$CAP_HOME/outputs/models/casa_relation_mlp_seed13/checkpoint.pt}"
: "${V2_ROOT:=$CAP_HOME/outputs/eval/v2_fast_seed${SEED}}"
HYBRID_TEST="${HYBRID_TEST:-$DATA_ROOT/outputs/datasets/abilitybench_av_hybrid_test}"
mkdir -p "$V2_ROOT/gpu0" "$V2_ROOT/gpu1" "$V2_ROOT/logs"
cd "$CAP_HOME"
[[ -f "$CHECKPOINT" ]] || { echo "missing checkpoint: $CHECKPOINT" >&2; exit 2; }

# Development-only offline passenger/service evaluation. No nuPlan closed loop here.
CUDA_VISIBLE_DEVICES="$GPU0" python scripts/run_ablations.py \
  --dataset_dir "$HYBRID_TEST" \
  --output_dir "$V2_ROOT/gpu0" \
  --trajectory_mode mock_strict \
  --casa_mode learned --casa_checkpoint "$CHECKPOINT" --casa_device cuda:0 \
  --algorithm_version V2 --evidence_grounded_runtime \
  --episode_limit "$EPISODE_LIMIT" --episode_seed "$EPISODE_SEED" \
  --variants full no_evidence_grounding no_learned_feasibility_guidance \
  --progress \
  > >(tee "$V2_ROOT/logs/gpu0.log") 2>&1 &
P0=$!

CUDA_VISIBLE_DEVICES="$GPU1" python scripts/run_ablations.py \
  --dataset_dir "$HYBRID_TEST" \
  --output_dir "$V2_ROOT/gpu1" \
  --trajectory_mode mock_strict \
  --casa_mode learned --casa_checkpoint "$CHECKPOINT" --casa_device cuda:0 \
  --algorithm_version V2 --evidence_grounded_runtime \
  --episode_limit "$EPISODE_LIMIT" --episode_seed "$EPISODE_SEED" \
  --variants no_completion_value_guidance no_conservative_margins \
  --progress \
  > >(tee "$V2_ROOT/logs/gpu1.log") 2>&1 &
P1=$!

wait "$P0"; wait "$P1"
python scripts/summarize_v2_results.py \
  --roots "$V2_ROOT/gpu0" "$V2_ROOT/gpu1" \
  --output "$V2_ROOT/v2_fast_summary.csv" \
  | tee "$V2_ROOT/logs/summary.log"
echo "[V2_FAST] complete: $V2_ROOT"
