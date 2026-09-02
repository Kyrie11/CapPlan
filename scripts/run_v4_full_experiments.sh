#!/usr/bin/env bash
set -euo pipefail

# Full frozen-test confirmation. Run only after V4-fast passes the preregistered
# V4-vs-V2 paired expansion gate.
CAP_HOME="${CAP_HOME:-/home/senzeyu2/code/CapPlan}"
DATA_ROOT="${DATA_ROOT:-/data0/senzeyu2/dataset/CapPlan/data}"
HYBRID_TEST="${HYBRID_TEST:-$DATA_ROOT/outputs/datasets/abilitybench_av_hybrid_test}"
GPU0="${GPU0:-0}"
GPU1="${GPU1:-1}"
SEED="${SEED:-13}"
CASA_CHECKPOINT="${CASA_CHECKPOINT:-$CAP_HOME/outputs/models/casa_relation_mlp_seed13/checkpoint.pt}"
V4_ROOT="${V4_ROOT:-$CAP_HOME/outputs/eval/v4_full_seed${SEED}}"
mkdir -p "$V4_ROOT"/{gpu0,gpu1,logs}
cd "$CAP_HOME"
[[ -f "$CASA_CHECKPOINT" ]] || { echo "missing CASA checkpoint: $CASA_CHECKPOINT" >&2; exit 2; }

CUDA_VISIBLE_DEVICES="$GPU0" python scripts/run_ablations.py \
  --dataset_dir "$HYBRID_TEST" --output_dir "$V4_ROOT/gpu0" \
  --trajectory_mode mock_strict --casa_mode learned \
  --casa_checkpoint "$CASA_CHECKPOINT" --casa_device cuda:0 \
  --algorithm_version V4 --evidence_grounded_runtime \
  --variants full no_continuation_envelope no_continuation_pruning no_continuation_priority \
  --progress > >(tee "$V4_ROOT/logs/gpu0.log") 2>&1 &
P0=$!

CUDA_VISIBLE_DEVICES="$GPU1" python scripts/run_ablations.py \
  --dataset_dir "$HYBRID_TEST" --output_dir "$V4_ROOT/gpu1" \
  --trajectory_mode mock_strict --casa_mode learned \
  --casa_checkpoint "$CASA_CHECKPOINT" --casa_device cuda:0 \
  --algorithm_version V4 --evidence_grounded_runtime \
  --variants v2_reference_runtime no_learned_feasibility_guidance no_evidence_grounding no_conservative_margins \
  --progress > >(tee "$V4_ROOT/logs/gpu1.log") 2>&1 &
P1=$!

wait "$P0"; wait "$P1"

python scripts/summarize_v4_results.py \
  --entry "v4_full=$V4_ROOT/gpu0/full" \
  --entry "v4_no_cce=$V4_ROOT/gpu0/no_continuation_envelope" \
  --entry "v4_priority_only=$V4_ROOT/gpu0/no_continuation_pruning" \
  --entry "v4_pruning_only=$V4_ROOT/gpu0/no_continuation_priority" \
  --entry "v2_reference=$V4_ROOT/gpu1/v2_reference_runtime" \
  --entry "v4_no_static_guidance=$V4_ROOT/gpu1/no_learned_feasibility_guidance" \
  --entry "v4_no_evidence_grounding=$V4_ROOT/gpu1/no_evidence_grounding" \
  --entry "v4_no_conservative_margins=$V4_ROOT/gpu1/no_conservative_margins" \
  --output "$V4_ROOT/v4_full_summary.csv" | tee "$V4_ROOT/logs/summary.log"

python scripts/compare_search_efficiency.py \
  --reference "$V4_ROOT/gpu1/v2_reference_runtime" --candidate "$V4_ROOT/gpu0/full" \
  --output "$V4_ROOT/v4_vs_v2_paired.json" | tee "$V4_ROOT/logs/v4_vs_v2_paired.log"
python scripts/compare_search_efficiency.py \
  --reference "$V4_ROOT/gpu0/no_continuation_envelope" --candidate "$V4_ROOT/gpu0/full" \
  --output "$V4_ROOT/v4_vs_no_cce_paired.json" | tee "$V4_ROOT/logs/v4_vs_no_cce_paired.log"

echo "[V4_FULL] complete: $V4_ROOT"
