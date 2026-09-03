#!/usr/bin/env bash
set -euo pipefail

# V5 fast preregistered attribution. No new training: use the same frozen CASA
# seed13 checkpoint so V2/V4/V5 differences isolate the backward viability mechanism.
CAP_HOME="${CAP_HOME:-/home/senzeyu2/code/CapPlan}"
DATA_ROOT="${DATA_ROOT:-/data0/senzeyu2/dataset/CapPlan/data}"
HYBRID_TEST="${HYBRID_TEST:-$DATA_ROOT/outputs/datasets/abilitybench_av_hybrid_test}"
GPU0="${GPU0:-0}"
GPU1="${GPU1:-1}"
SEED="${SEED:-13}"
EPISODE_LIMIT="${EPISODE_LIMIT:-256}"
EPISODE_SEED="${EPISODE_SEED:-13}"
CASA_CHECKPOINT="${CASA_CHECKPOINT:-$CAP_HOME/outputs/models/casa_relation_mlp_seed13/checkpoint.pt}"
V5_ROOT="${V5_ROOT:-$CAP_HOME/outputs/eval/v5_fast_seed${SEED}}"
mkdir -p "$V5_ROOT"/{gpu0,gpu1,v4_reference,logs}
cd "$CAP_HOME"
[[ -f "$CASA_CHECKPOINT" ]] || { echo "missing CASA checkpoint: $CASA_CHECKPOINT" >&2; exit 2; }

# GPU0: primary V5 attribution and exact V2 reference.
CUDA_VISIBLE_DEVICES="$GPU0" python scripts/run_ablations.py \
  --dataset_dir "$HYBRID_TEST" --output_dir "$V5_ROOT/gpu0" \
  --trajectory_mode mock_strict \
  --casa_mode learned --casa_checkpoint "$CASA_CHECKPOINT" --casa_device cuda:0 \
  --algorithm_version V5 --evidence_grounded_runtime \
  --episode_limit "$EPISODE_LIMIT" --episode_seed "$EPISODE_SEED" \
  --variants full no_viability_kernel no_typed_viability v2_reference_runtime \
  --progress > >(tee "$V5_ROOT/logs/gpu0.log") 2>&1 &
P0=$!

# GPU1: proof-certificate and learned-ordering controls, followed by an exact V4
# reference on the same deterministic subset.
(
  CUDA_VISIBLE_DEVICES="$GPU1" python scripts/run_ablations.py \
    --dataset_dir "$HYBRID_TEST" --output_dir "$V5_ROOT/gpu1" \
    --trajectory_mode mock_strict \
    --casa_mode learned --casa_checkpoint "$CASA_CHECKPOINT" --casa_device cuda:0 \
    --algorithm_version V5 --evidence_grounded_runtime \
    --episode_limit "$EPISODE_LIMIT" --episode_seed "$EPISODE_SEED" \
    --variants generic_viability_certificates no_learned_feasibility_guidance \
    --progress > >(tee "$V5_ROOT/logs/gpu1.log") 2>&1

  CUDA_VISIBLE_DEVICES="$GPU1" python scripts/run_ablations.py \
    --dataset_dir "$HYBRID_TEST" --output_dir "$V5_ROOT/v4_reference" \
    --trajectory_mode mock_strict \
    --casa_mode learned --casa_checkpoint "$CASA_CHECKPOINT" --casa_device cuda:0 \
    --algorithm_version V4 --evidence_grounded_runtime \
    --episode_limit "$EPISODE_LIMIT" --episode_seed "$EPISODE_SEED" \
    --variants full --progress > >(tee "$V5_ROOT/logs/v4_reference.log") 2>&1
) &
P1=$!

wait "$P0"; wait "$P1"

python scripts/summarize_v5_results.py \
  --entry "v5_full=$V5_ROOT/gpu0/full" \
  --entry "v5_no_kernel=$V5_ROOT/gpu0/no_viability_kernel" \
  --entry "v5_structural_only=$V5_ROOT/gpu0/no_typed_viability" \
  --entry "v2_reference=$V5_ROOT/gpu0/v2_reference_runtime" \
  --entry "v5_generic_certificate=$V5_ROOT/gpu1/generic_viability_certificates" \
  --entry "v5_no_static_guidance=$V5_ROOT/gpu1/no_learned_feasibility_guidance" \
  --entry "v4_reference=$V5_ROOT/v4_reference/full" \
  --output "$V5_ROOT/v5_fast_summary.csv" | tee "$V5_ROOT/logs/summary.log"

python scripts/compare_search_efficiency.py --reference "$V5_ROOT/gpu0/v2_reference_runtime" --candidate "$V5_ROOT/gpu0/full" --output "$V5_ROOT/v5_vs_v2_paired.json" | tee "$V5_ROOT/logs/v5_vs_v2.log"
python scripts/compare_search_efficiency.py --reference "$V5_ROOT/gpu0/no_viability_kernel" --candidate "$V5_ROOT/gpu0/full" --output "$V5_ROOT/v5_vs_no_kernel_paired.json" | tee "$V5_ROOT/logs/v5_vs_no_kernel.log"
python scripts/compare_search_efficiency.py --reference "$V5_ROOT/gpu0/no_typed_viability" --candidate "$V5_ROOT/gpu0/full" --output "$V5_ROOT/v5_vs_structural_only_paired.json" | tee "$V5_ROOT/logs/v5_vs_structural_only.log"
python scripts/compare_search_efficiency.py --reference "$V5_ROOT/gpu1/generic_viability_certificates" --candidate "$V5_ROOT/gpu0/full" --output "$V5_ROOT/v5_vs_generic_certificate_paired.json" | tee "$V5_ROOT/logs/v5_vs_generic_certificate.log"
python scripts/compare_search_efficiency.py --reference "$V5_ROOT/v4_reference/full" --candidate "$V5_ROOT/gpu0/full" --output "$V5_ROOT/v5_vs_v4_paired.json" | tee "$V5_ROOT/logs/v5_vs_v4.log"
python scripts/compare_search_efficiency.py --reference "$V5_ROOT/gpu1/no_learned_feasibility_guidance" --candidate "$V5_ROOT/gpu0/full" --output "$V5_ROOT/v5_vs_no_static_paired.json" | tee "$V5_ROOT/logs/v5_vs_no_static.log"

python scripts/assess_v5_fast.py \
  --full "$V5_ROOT/gpu0/full" \
  --v2 "$V5_ROOT/gpu0/v2_reference_runtime" \
  --structural "$V5_ROOT/gpu0/no_typed_viability" \
  --generic "$V5_ROOT/gpu1/generic_viability_certificates" \
  --v4 "$V5_ROOT/v4_reference/full" \
  --v5-v2 "$V5_ROOT/v5_vs_v2_paired.json" \
  --v5-structural "$V5_ROOT/v5_vs_structural_only_paired.json" \
  --v5-generic "$V5_ROOT/v5_vs_generic_certificate_paired.json" \
  --v5-v4 "$V5_ROOT/v5_vs_v4_paired.json" \
  --output "$V5_ROOT/v5_fast_gate.json" | tee "$V5_ROOT/logs/gate.log"

echo "[V5_FAST] complete: $V5_ROOT"
