#!/usr/bin/env bash
set -euo pipefail
CAP_HOME="${CAP_HOME:-/home/senzeyu2/code/CapPlan}"
DATA_ROOT="${DATA_ROOT:-/data0/senzeyu2/dataset/CapPlan/data}"
HYBRID_TEST="${HYBRID_TEST:-$DATA_ROOT/outputs/datasets/abilitybench_av_hybrid_test}"
GPU0="${GPU0:-0}"; GPU1="${GPU1:-1}"; SEED="${SEED:-13}"
EPISODE_LIMIT="${EPISODE_LIMIT:-256}"; EPISODE_SEED="${EPISODE_SEED:-13}"
CASA_CHECKPOINT="${CASA_CHECKPOINT:-$CAP_HOME/outputs/models/casa_relation_mlp_seed13/checkpoint.pt}"
V7_ROOT="${V7_ROOT:-$CAP_HOME/outputs/eval/v7_fast_seed${SEED}}"
mkdir -p "$V7_ROOT"/{gpu0,gpu1,logs}
cd "$CAP_HOME"
[[ -f "$CASA_CHECKPOINT" ]] || { echo "missing CASA checkpoint: $CASA_CHECKPOINT" >&2; exit 2; }

# Latency is a V7 preregistered gate.  Unlike V6-fast, do NOT run a competing
# worker while the latency-critical V7/V6/V5/V2 controls execute.  This makes the
# paired wall-clock comparison interpretable rather than GPU/CPU-contention bound.
CUDA_VISIBLE_DEVICES="$GPU0" python scripts/run_ablations.py \
  --dataset_dir "$HYBRID_TEST" --output_dir "$V7_ROOT/gpu0" \
  --trajectory_mode mock_strict --casa_mode learned --casa_checkpoint "$CASA_CHECKPOINT" --casa_device cuda:0 \
  --algorithm_version V7 --evidence_grounded_runtime \
  --episode_limit "$EPISODE_LIMIT" --episode_seed "$EPISODE_SEED" \
  --variants full v6_reference_runtime v5_reference_runtime v2_reference_runtime no_typed_viability no_viability_kernel \
  --progress > >(tee "$V7_ROOT/logs/gpu0.log") 2>&1

# Diagnosis-only controls run only after the latency-critical group has finished.
CUDA_VISIBLE_DEVICES="$GPU1" python scripts/run_ablations.py \
  --dataset_dir "$HYBRID_TEST" --output_dir "$V7_ROOT/gpu1" \
  --trajectory_mode mock_strict --casa_mode learned --casa_checkpoint "$CASA_CHECKPOINT" --casa_device cuda:0 \
  --algorithm_version V7 --evidence_grounded_runtime \
  --episode_limit "$EPISODE_LIMIT" --episode_seed "$EPISODE_SEED" \
  --variants no_rejection_kernel no_learned_feasibility_guidance \
  --progress > >(tee "$V7_ROOT/logs/gpu1.log") 2>&1

python scripts/summarize_v7_results.py \
  --entry "v7_full=$V7_ROOT/gpu0/full" \
  --entry "v6_reference=$V7_ROOT/gpu0/v6_reference_runtime" \
  --entry "v5_reference=$V7_ROOT/gpu0/v5_reference_runtime" \
  --entry "v2_reference=$V7_ROOT/gpu0/v2_reference_runtime" \
  --entry "v7_structural_only=$V7_ROOT/gpu0/no_typed_viability" \
  --entry "v7_no_kernel=$V7_ROOT/gpu0/no_viability_kernel" \
  --entry "v7_no_rejection=$V7_ROOT/gpu1/no_rejection_kernel" \
  --entry "v7_no_static_guidance=$V7_ROOT/gpu1/no_learned_feasibility_guidance" \
  --output "$V7_ROOT/v7_fast_summary.csv" | tee "$V7_ROOT/logs/summary.log"

python scripts/compare_search_efficiency.py --reference "$V7_ROOT/gpu0/v2_reference_runtime" --candidate "$V7_ROOT/gpu0/full" --output "$V7_ROOT/v7_vs_v2_paired.json" | tee "$V7_ROOT/logs/v7_vs_v2.log"
python scripts/compare_search_efficiency.py --reference "$V7_ROOT/gpu0/v5_reference_runtime" --candidate "$V7_ROOT/gpu0/full" --output "$V7_ROOT/v7_vs_v5_paired.json" | tee "$V7_ROOT/logs/v7_vs_v5.log"
python scripts/compare_search_efficiency.py --reference "$V7_ROOT/gpu0/v6_reference_runtime" --candidate "$V7_ROOT/gpu0/full" --output "$V7_ROOT/v7_vs_v6_paired.json" | tee "$V7_ROOT/logs/v7_vs_v6.log"
python scripts/compare_search_efficiency.py --reference "$V7_ROOT/gpu0/no_typed_viability" --candidate "$V7_ROOT/gpu0/full" --output "$V7_ROOT/v7_vs_structural_only_paired.json" | tee "$V7_ROOT/logs/v7_vs_structural_only.log"
python scripts/compare_search_efficiency.py --reference "$V7_ROOT/gpu1/no_rejection_kernel" --candidate "$V7_ROOT/gpu0/full" --output "$V7_ROOT/v7_vs_no_rejection_paired.json" | tee "$V7_ROOT/logs/v7_vs_no_rejection.log"
python scripts/compare_search_efficiency.py --reference "$V7_ROOT/gpu1/no_learned_feasibility_guidance" --candidate "$V7_ROOT/gpu0/full" --output "$V7_ROOT/v7_vs_no_static_paired.json" | tee "$V7_ROOT/logs/v7_vs_no_static.log"

python scripts/assess_v7_fast.py \
  --full "$V7_ROOT/gpu0/full" --v2 "$V7_ROOT/gpu0/v2_reference_runtime" --v5 "$V7_ROOT/gpu0/v5_reference_runtime" --v6 "$V7_ROOT/gpu0/v6_reference_runtime" \
  --structural "$V7_ROOT/gpu0/no_typed_viability" --no-rejection "$V7_ROOT/gpu1/no_rejection_kernel" \
  --v7-v2 "$V7_ROOT/v7_vs_v2_paired.json" --v7-v5 "$V7_ROOT/v7_vs_v5_paired.json" --v7-v6 "$V7_ROOT/v7_vs_v6_paired.json" \
  --v7-structural "$V7_ROOT/v7_vs_structural_only_paired.json" --v7-no-rejection "$V7_ROOT/v7_vs_no_rejection_paired.json" \
  --output "$V7_ROOT/v7_fast_gate.json" | tee "$V7_ROOT/logs/gate.log"

echo "[V7_FAST] complete: $V7_ROOT"
