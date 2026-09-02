#!/usr/bin/env bash
set -euo pipefail

# Standalone V3 fast attribution experiment.  No real nuPlan closed loop here:
# the goal is fast algorithm convergence on passenger-complete semantics/search.
CAP_HOME="${CAP_HOME:-/home/senzeyu2/code/CapPlan}"
DATA_ROOT="${DATA_ROOT:-/data0/senzeyu2/dataset/CapPlan/data}"
HYBRID_TEST="${HYBRID_TEST:-$DATA_ROOT/outputs/datasets/abilitybench_av_hybrid_test}"
GPU0="${GPU0:-0}"
GPU1="${GPU1:-1}"
SEED="${SEED:-13}"
EPISODE_LIMIT="${EPISODE_LIMIT:-256}"
EPISODE_SEED="${EPISODE_SEED:-13}"
CASA_CHECKPOINT="${CASA_CHECKPOINT:-$CAP_HOME/outputs/models/casa_relation_mlp_seed13/checkpoint.pt}"
FRONTIER_ROOT="${FRONTIER_ROOT:-$CAP_HOME/outputs/models/v3_frontier_seed${SEED}}"
V3_ROOT="${V3_ROOT:-$CAP_HOME/outputs/eval/v3_fast_seed${SEED}}"
mkdir -p "$V3_ROOT"/{primary,structural,bce,logs}
cd "$CAP_HOME"

for f in \
  "$CASA_CHECKPOINT" \
  "$FRONTIER_ROOT/full_pairwise/checkpoint.pt" \
  "$FRONTIER_ROOT/structural_pairwise/checkpoint.pt" \
  "$FRONTIER_ROOT/full_bce/checkpoint.pt"; do
  [[ -f "$f" ]] || { echo "missing checkpoint: $f" >&2; exit 2; }
done

CUDA_VISIBLE_DEVICES="$GPU0" python scripts/run_ablations.py \
  --dataset_dir "$HYBRID_TEST" --output_dir "$V3_ROOT/primary" \
  --trajectory_mode mock_strict \
  --casa_mode learned --casa_checkpoint "$CASA_CHECKPOINT" --casa_device cuda:0 \
  --algorithm_version V3 --evidence_grounded_runtime \
  --frontier_ranker_checkpoint "$FRONTIER_ROOT/full_pairwise/checkpoint.pt" \
  --frontier_ranker_device cpu \
  --episode_limit "$EPISODE_LIMIT" --episode_seed "$EPISODE_SEED" \
  --variants full no_frontier_ranker v2_reference_runtime \
  --progress > >(tee "$V3_ROOT/logs/gpu0_primary.log") 2>&1 &
P0=$!

(
  CUDA_VISIBLE_DEVICES="$GPU1" python scripts/run_ablations.py \
    --dataset_dir "$HYBRID_TEST" --output_dir "$V3_ROOT/structural" \
    --trajectory_mode mock_strict \
    --casa_mode learned --casa_checkpoint "$CASA_CHECKPOINT" --casa_device cuda:0 \
    --algorithm_version V3 --evidence_grounded_runtime \
    --frontier_ranker_checkpoint "$FRONTIER_ROOT/structural_pairwise/checkpoint.pt" \
    --frontier_ranker_device cpu \
    --episode_limit "$EPISODE_LIMIT" --episode_seed "$EPISODE_SEED" \
    --variants full --progress
  CUDA_VISIBLE_DEVICES="$GPU1" python scripts/run_ablations.py \
    --dataset_dir "$HYBRID_TEST" --output_dir "$V3_ROOT/bce" \
    --trajectory_mode mock_strict \
    --casa_mode learned --casa_checkpoint "$CASA_CHECKPOINT" --casa_device cuda:0 \
    --algorithm_version V3 --evidence_grounded_runtime \
    --frontier_ranker_checkpoint "$FRONTIER_ROOT/full_bce/checkpoint.pt" \
    --frontier_ranker_device cpu \
    --episode_limit "$EPISODE_LIMIT" --episode_seed "$EPISODE_SEED" \
    --variants full --progress
) > >(tee "$V3_ROOT/logs/gpu1_controls.log") 2>&1 &
P1=$!

wait "$P0"; wait "$P1"

python scripts/summarize_v3_results.py \
  --entry "v3_full_pairwise=$V3_ROOT/primary/full" \
  --entry "v3_no_frontier=$V3_ROOT/primary/no_frontier_ranker" \
  --entry "v2_reference=$V3_ROOT/primary/v2_reference_runtime" \
  --entry "v3_structural_pairwise=$V3_ROOT/structural/full" \
  --entry "v3_full_bce=$V3_ROOT/bce/full" \
  --output "$V3_ROOT/v3_fast_summary.csv" | tee "$V3_ROOT/logs/summary.log"

python scripts/compare_v3_search.py \
  --reference "$V3_ROOT/primary/no_frontier_ranker" \
  --candidate "$V3_ROOT/primary/full" \
  --output "$V3_ROOT/v3_vs_no_frontier_paired.json" \
  | tee "$V3_ROOT/logs/v3_vs_no_frontier_paired.log"
python scripts/compare_v3_search.py \
  --reference "$V3_ROOT/primary/v2_reference_runtime" \
  --candidate "$V3_ROOT/primary/full" \
  --output "$V3_ROOT/v3_vs_v2_paired.json" \
  | tee "$V3_ROOT/logs/v3_vs_v2_paired.log"

echo "[V3_FAST] complete: $V3_ROOT"
