#!/usr/bin/env bash
set -euo pipefail

# Full 997-episode frozen-test confirmation. Run only if v5_fast_gate.json says GO.
CAP_HOME="${CAP_HOME:-/home/senzeyu2/code/CapPlan}"
DATA_ROOT="${DATA_ROOT:-/data0/senzeyu2/dataset/CapPlan/data}"
HYBRID_TEST="${HYBRID_TEST:-$DATA_ROOT/outputs/datasets/abilitybench_av_hybrid_test}"
GPU0="${GPU0:-0}"
GPU1="${GPU1:-1}"
SEED="${SEED:-13}"
CASA_CHECKPOINT="${CASA_CHECKPOINT:-$CAP_HOME/outputs/models/casa_relation_mlp_seed13/checkpoint.pt}"
V5_ROOT="${V5_ROOT:-$CAP_HOME/outputs/eval/v5_full_seed${SEED}}"
mkdir -p "$V5_ROOT"/{gpu0,gpu1,v4_reference,logs}
cd "$CAP_HOME"
[[ -f "$CASA_CHECKPOINT" ]] || { echo "missing CASA checkpoint: $CASA_CHECKPOINT" >&2; exit 2; }

CUDA_VISIBLE_DEVICES="$GPU0" python scripts/run_ablations.py \
  --dataset_dir "$HYBRID_TEST" --output_dir "$V5_ROOT/gpu0" \
  --trajectory_mode mock_strict --casa_mode learned \
  --casa_checkpoint "$CASA_CHECKPOINT" --casa_device cuda:0 \
  --algorithm_version V5 --evidence_grounded_runtime \
  --variants full no_viability_kernel no_typed_viability v2_reference_runtime \
  --progress > >(tee "$V5_ROOT/logs/gpu0.log") 2>&1 &
P0=$!

(
  CUDA_VISIBLE_DEVICES="$GPU1" python scripts/run_ablations.py \
    --dataset_dir "$HYBRID_TEST" --output_dir "$V5_ROOT/gpu1" \
    --trajectory_mode mock_strict --casa_mode learned \
    --casa_checkpoint "$CASA_CHECKPOINT" --casa_device cuda:0 \
    --algorithm_version V5 --evidence_grounded_runtime \
    --variants generic_viability_certificates no_learned_feasibility_guidance no_evidence_grounding no_conservative_margins \
    --progress > >(tee "$V5_ROOT/logs/gpu1.log") 2>&1
  CUDA_VISIBLE_DEVICES="$GPU1" python scripts/run_ablations.py \
    --dataset_dir "$HYBRID_TEST" --output_dir "$V5_ROOT/v4_reference" \
    --trajectory_mode mock_strict --casa_mode learned \
    --casa_checkpoint "$CASA_CHECKPOINT" --casa_device cuda:0 \
    --algorithm_version V4 --evidence_grounded_runtime --variants full \
    --progress > >(tee "$V5_ROOT/logs/v4_reference.log") 2>&1
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
  --entry "v5_no_evidence_grounding=$V5_ROOT/gpu1/no_evidence_grounding" \
  --entry "v5_no_conservative_margins=$V5_ROOT/gpu1/no_conservative_margins" \
  --entry "v4_reference=$V5_ROOT/v4_reference/full" \
  --output "$V5_ROOT/v5_full_summary.csv" | tee "$V5_ROOT/logs/summary.log"

python scripts/compare_search_efficiency.py --reference "$V5_ROOT/gpu0/v2_reference_runtime" --candidate "$V5_ROOT/gpu0/full" --output "$V5_ROOT/v5_vs_v2_paired.json" | tee "$V5_ROOT/logs/v5_vs_v2.log"
python scripts/compare_search_efficiency.py --reference "$V5_ROOT/gpu0/no_typed_viability" --candidate "$V5_ROOT/gpu0/full" --output "$V5_ROOT/v5_vs_structural_only_paired.json" | tee "$V5_ROOT/logs/v5_vs_structural_only.log"
python scripts/compare_search_efficiency.py --reference "$V5_ROOT/gpu1/generic_viability_certificates" --candidate "$V5_ROOT/gpu0/full" --output "$V5_ROOT/v5_vs_generic_certificate_paired.json" | tee "$V5_ROOT/logs/v5_vs_generic_certificate.log"
python scripts/compare_search_efficiency.py --reference "$V5_ROOT/v4_reference/full" --candidate "$V5_ROOT/gpu0/full" --output "$V5_ROOT/v5_vs_v4_paired.json" | tee "$V5_ROOT/logs/v5_vs_v4.log"

echo "[V5_FULL] complete: $V5_ROOT"
