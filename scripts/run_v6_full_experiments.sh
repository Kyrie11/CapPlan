#!/usr/bin/env bash
set -euo pipefail

# Full 997-episode frozen-test confirmation. Run only if v6_fast_gate.json says GO.
CAP_HOME="${CAP_HOME:-/home/senzeyu2/code/CapPlan}"
DATA_ROOT="${DATA_ROOT:-/data0/senzeyu2/dataset/CapPlan/data}"
HYBRID_TEST="${HYBRID_TEST:-$DATA_ROOT/outputs/datasets/abilitybench_av_hybrid_test}"
GPU0="${GPU0:-0}"; GPU1="${GPU1:-1}"; SEED="${SEED:-13}"
CASA_CHECKPOINT="${CASA_CHECKPOINT:-$CAP_HOME/outputs/models/casa_relation_mlp_seed13/checkpoint.pt}"
V6_ROOT="${V6_ROOT:-$CAP_HOME/outputs/eval/v6_full_seed${SEED}}"
mkdir -p "$V6_ROOT"/{gpu0,gpu1,logs}
cd "$CAP_HOME"
[[ -f "$CASA_CHECKPOINT" ]] || { echo "missing CASA checkpoint: $CASA_CHECKPOINT" >&2; exit 2; }

# GPU0: paper mechanism + exact representation controls.
CUDA_VISIBLE_DEVICES="$GPU0" python scripts/run_ablations.py \
  --dataset_dir "$HYBRID_TEST" --output_dir "$V6_ROOT/gpu0" \
  --trajectory_mode mock_strict --casa_mode learned --casa_checkpoint "$CASA_CHECKPOINT" --casa_device cuda:0 \
  --algorithm_version V6 --evidence_grounded_runtime \
  --variants full v5_reference_runtime v2_reference_runtime no_typed_viability \
  --progress > >(tee "$V6_ROOT/logs/gpu0.log") 2>&1 &
P0=$!

# GPU1: explanation/order/safety controls.  These do not depend on GPU0 outputs.
CUDA_VISIBLE_DEVICES="$GPU1" python scripts/run_ablations.py \
  --dataset_dir "$HYBRID_TEST" --output_dir "$V6_ROOT/gpu1" \
  --trajectory_mode mock_strict --casa_mode learned --casa_checkpoint "$CASA_CHECKPOINT" --casa_device cuda:0 \
  --algorithm_version V6 --evidence_grounded_runtime \
  --variants no_viability_kernel no_viability_proof_envelope no_learned_feasibility_guidance no_evidence_grounding no_conservative_margins \
  --progress > >(tee "$V6_ROOT/logs/gpu1.log") 2>&1 &
P1=$!
wait "$P0"; wait "$P1"

python scripts/summarize_v6_results.py \
  --entry "v6_full=$V6_ROOT/gpu0/full" \
  --entry "v5_reference=$V6_ROOT/gpu0/v5_reference_runtime" \
  --entry "v2_reference=$V6_ROOT/gpu0/v2_reference_runtime" \
  --entry "v6_structural_only=$V6_ROOT/gpu0/no_typed_viability" \
  --entry "v6_no_kernel=$V6_ROOT/gpu1/no_viability_kernel" \
  --entry "v6_no_proof_envelope=$V6_ROOT/gpu1/no_viability_proof_envelope" \
  --entry "v6_no_static_guidance=$V6_ROOT/gpu1/no_learned_feasibility_guidance" \
  --entry "v6_no_evidence_grounding=$V6_ROOT/gpu1/no_evidence_grounding" \
  --entry "v6_no_conservative_margins=$V6_ROOT/gpu1/no_conservative_margins" \
  --output "$V6_ROOT/v6_full_summary.csv" | tee "$V6_ROOT/logs/summary.log"

python scripts/compare_search_efficiency.py --reference "$V6_ROOT/gpu0/v2_reference_runtime" --candidate "$V6_ROOT/gpu0/full" --output "$V6_ROOT/v6_vs_v2_paired.json" | tee "$V6_ROOT/logs/v6_vs_v2.log"
python scripts/compare_search_efficiency.py --reference "$V6_ROOT/gpu0/v5_reference_runtime" --candidate "$V6_ROOT/gpu0/full" --output "$V6_ROOT/v6_vs_v5_paired.json" | tee "$V6_ROOT/logs/v6_vs_v5.log"
python scripts/compare_search_efficiency.py --reference "$V6_ROOT/gpu0/no_typed_viability" --candidate "$V6_ROOT/gpu0/full" --output "$V6_ROOT/v6_vs_structural_only_paired.json" | tee "$V6_ROOT/logs/v6_vs_structural_only.log"
python scripts/compare_search_efficiency.py --reference "$V6_ROOT/gpu1/no_viability_proof_envelope" --candidate "$V6_ROOT/gpu0/full" --output "$V6_ROOT/v6_vs_no_proof_paired.json" | tee "$V6_ROOT/logs/v6_vs_no_proof.log"
python scripts/compare_search_efficiency.py --reference "$V6_ROOT/gpu1/no_learned_feasibility_guidance" --candidate "$V6_ROOT/gpu0/full" --output "$V6_ROOT/v6_vs_no_static_paired.json" | tee "$V6_ROOT/logs/v6_vs_no_static.log"

echo "[V6_FULL] complete: $V6_ROOT"
