#!/usr/bin/env bash
set -euo pipefail
CAP_HOME="${CAP_HOME:-/home/senzeyu2/code/CapPlan}"; DATA_ROOT="${DATA_ROOT:-/data0/senzeyu2/dataset/CapPlan/data}"
HYBRID_TEST="${HYBRID_TEST:-$DATA_ROOT/outputs/datasets/abilitybench_av_hybrid_test}"; GPU0="${GPU0:-0}"; SEED="${SEED:-13}"
CASA_CHECKPOINT="${CASA_CHECKPOINT:-$CAP_HOME/outputs/models/casa_relation_mlp_seed13/checkpoint.pt}"
V11_ROOT="${V11_ROOT:-$CAP_HOME/outputs/eval/v11_full_seed${SEED}}"; mkdir -p "$V11_ROOT"/{runs,logs}; cd "$CAP_HOME"
CUDA_VISIBLE_DEVICES="$GPU0" python scripts/run_ablations.py \
  --dataset_dir "$HYBRID_TEST" --output_dir "$V11_ROOT/runs" --trajectory_mode mock_strict \
  --casa_mode learned --casa_checkpoint "$CASA_CHECKPOINT" --casa_device cuda:0 --algorithm_version V11 --evidence_grounded_runtime \
  --variants full v10_reference_runtime v2_reference_runtime v5_reference_runtime no_typed_viability v11_legacy_static_guidance no_lazy_diagnostic_replay v11_native_quotient_experimental \
  --progress > >(tee "$V11_ROOT/logs/v11_full.log") 2>&1
echo "V11_FULL_ROOT=$V11_ROOT"
