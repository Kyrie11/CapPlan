#!/usr/bin/env bash
set -euo pipefail
CAP_HOME="${CAP_HOME:-/home/senzeyu2/code/CapPlan}"
DATA_ROOT="${DATA_ROOT:-/data0/senzeyu2/dataset/CapPlan/data}"
HYBRID_TEST="${HYBRID_TEST:-$DATA_ROOT/outputs/datasets/abilitybench_av_hybrid_test}"
GPU0="${GPU0:-0}"; SEED="${SEED:-13}"
CASA_CHECKPOINT="${CASA_CHECKPOINT:-$CAP_HOME/outputs/models/casa_relation_mlp_seed13/checkpoint.pt}"
V8_ROOT="${V8_ROOT:-$CAP_HOME/outputs/eval/v8_full_seed${SEED}}"
mkdir -p "$V8_ROOT"/{runs,logs}; cd "$CAP_HOME"
[[ -f "$CASA_CHECKPOINT" ]] || { echo "missing CASA checkpoint: $CASA_CHECKPOINT" >&2; exit 2; }
CUDA_VISIBLE_DEVICES="$GPU0" python scripts/run_ablations.py \
  --dataset_dir "$HYBRID_TEST" --output_dir "$V8_ROOT/runs" \
  --trajectory_mode mock_strict --casa_mode learned --casa_checkpoint "$CASA_CHECKPOINT" --casa_device cuda:0 \
  --algorithm_version V8 --evidence_grounded_runtime --episode_limit 0 --episode_seed 13 \
  --variants full v2_reference_runtime v5_reference_runtime no_typed_viability no_viability_kernel no_lazy_diagnostic_replay no_learned_feasibility_guidance \
  --progress > >(tee "$V8_ROOT/logs/v8_full.log") 2>&1
python scripts/summarize_v8_results.py \
  --entry "v8_full=$V8_ROOT/runs/full" --entry "v2_reference=$V8_ROOT/runs/v2_reference_runtime" \
  --entry "v5_reference=$V8_ROOT/runs/v5_reference_runtime" --entry "v8_structural_only=$V8_ROOT/runs/no_typed_viability" \
  --entry "v8_no_kernel=$V8_ROOT/runs/no_viability_kernel" --entry "v8_no_lazy_proof=$V8_ROOT/runs/no_lazy_diagnostic_replay" \
  --entry "v8_no_static_guidance=$V8_ROOT/runs/no_learned_feasibility_guidance" \
  --output "$V8_ROOT/v8_full_summary.csv" | tee "$V8_ROOT/logs/summary.log"
echo "V8_FULL_ROOT=$V8_ROOT"
