#!/usr/bin/env bash
set -euo pipefail

# Standalone V3 confirmatory test on the full frozen test split. Run only after
# the preregistered V3-fast gate passes.
CAP_HOME="${CAP_HOME:-/home/senzeyu2/code/CapPlan}"
DATA_ROOT="${DATA_ROOT:-/data0/senzeyu2/dataset/CapPlan/data}"
HYBRID_TEST="${HYBRID_TEST:-$DATA_ROOT/outputs/datasets/abilitybench_av_hybrid_test}"
GPU0="${GPU0:-0}"
GPU1="${GPU1:-1}"
SEED="${SEED:-13}"
CASA_CHECKPOINT="${CASA_CHECKPOINT:-$CAP_HOME/outputs/models/casa_relation_mlp_seed13/checkpoint.pt}"
FRONTIER_CHECKPOINT="${FRONTIER_CHECKPOINT:-$CAP_HOME/outputs/models/v3_frontier_seed${SEED}/full_pairwise/checkpoint.pt}"
V3_ROOT="${V3_ROOT:-$CAP_HOME/outputs/eval/v3_full_seed${SEED}}"
mkdir -p "$V3_ROOT"/{gpu0,gpu1,logs}
cd "$CAP_HOME"
[[ -f "$CASA_CHECKPOINT" && -f "$FRONTIER_CHECKPOINT" ]] || { echo "missing checkpoint" >&2; exit 2; }

CUDA_VISIBLE_DEVICES="$GPU0" python scripts/run_ablations.py \
  --dataset_dir "$HYBRID_TEST" --output_dir "$V3_ROOT/gpu0" \
  --trajectory_mode mock_strict --casa_mode learned \
  --casa_checkpoint "$CASA_CHECKPOINT" --casa_device cuda:0 \
  --algorithm_version V3 --evidence_grounded_runtime \
  --frontier_ranker_checkpoint "$FRONTIER_CHECKPOINT" --frontier_ranker_device cpu \
  --variants full no_frontier_ranker \
  --progress > >(tee "$V3_ROOT/logs/gpu0.log") 2>&1 &
P0=$!

CUDA_VISIBLE_DEVICES="$GPU1" python scripts/run_ablations.py \
  --dataset_dir "$HYBRID_TEST" --output_dir "$V3_ROOT/gpu1" \
  --trajectory_mode mock_strict --casa_mode learned \
  --casa_checkpoint "$CASA_CHECKPOINT" --casa_device cuda:0 \
  --algorithm_version V3 --evidence_grounded_runtime \
  --frontier_ranker_checkpoint "$FRONTIER_CHECKPOINT" --frontier_ranker_device cpu \
  --variants v2_reference_runtime no_evidence_grounding no_conservative_margins \
  --progress > >(tee "$V3_ROOT/logs/gpu1.log") 2>&1 &
P1=$!

wait "$P0"; wait "$P1"
python scripts/summarize_v3_results.py \
  --entry "v3_full=$V3_ROOT/gpu0/full" \
  --entry "v3_no_frontier=$V3_ROOT/gpu0/no_frontier_ranker" \
  --entry "v2_reference=$V3_ROOT/gpu1/v2_reference_runtime" \
  --entry "v3_no_evidence_grounding=$V3_ROOT/gpu1/no_evidence_grounding" \
  --entry "v3_no_conservative_margins=$V3_ROOT/gpu1/no_conservative_margins" \
  --output "$V3_ROOT/v3_full_summary.csv" | tee "$V3_ROOT/logs/summary.log"
python scripts/compare_v3_search.py \
  --reference "$V3_ROOT/gpu0/no_frontier_ranker" --candidate "$V3_ROOT/gpu0/full" \
  --output "$V3_ROOT/v3_vs_no_frontier_paired.json" | tee "$V3_ROOT/logs/v3_vs_no_frontier_paired.log"
python scripts/compare_v3_search.py \
  --reference "$V3_ROOT/gpu1/v2_reference_runtime" --candidate "$V3_ROOT/gpu0/full" \
  --output "$V3_ROOT/v3_vs_v2_paired.json" | tee "$V3_ROOT/logs/v3_vs_v2_paired.log"
echo "[V3_FULL] complete: $V3_ROOT"
