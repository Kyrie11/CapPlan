#!/usr/bin/env bash
set -euo pipefail
CAP_HOME="${CAP_HOME:-/home/senzeyu2/code/CapPlan}"
DATA_ROOT="${DATA_ROOT:-/data0/senzeyu2/dataset/CapPlan/data}"
HYBRID_TEST="${HYBRID_TEST:-$DATA_ROOT/outputs/datasets/abilitybench_av_hybrid_test}"
GPU0="${GPU0:-0}"; SEED="${SEED:-13}"
EPISODE_LIMIT="${EPISODE_LIMIT:-256}"; EPISODE_SEED="${EPISODE_SEED:-13}"
CASA_CHECKPOINT="${CASA_CHECKPOINT:-$CAP_HOME/outputs/models/casa_relation_mlp_seed13/checkpoint.pt}"
V9_ROOT="${V9_ROOT:-$CAP_HOME/outputs/eval/v9_fast_seed${SEED}}"
mkdir -p "$V9_ROOT"/{runs,logs}; cd "$CAP_HOME"
[[ -f "$CASA_CHECKPOINT" ]] || { echo "missing CASA checkpoint: $CASA_CHECKPOINT" >&2; exit 2; }

# Keep latency-critical controls serial. Full V9 differs from the exact V8
# reference only by capability projection + frontier representation indexing.
CUDA_VISIBLE_DEVICES="$GPU0" python scripts/run_ablations.py \
  --dataset_dir "$HYBRID_TEST" --output_dir "$V9_ROOT/runs" \
  --trajectory_mode mock_strict --casa_mode learned --casa_checkpoint "$CASA_CHECKPOINT" --casa_device cuda:0 \
  --algorithm_version V9 --evidence_grounded_runtime \
  --episode_limit "$EPISODE_LIMIT" --episode_seed "$EPISODE_SEED" \
  --variants full v8_reference_runtime v2_reference_runtime v5_reference_runtime no_typed_viability no_viability_kernel no_capability_projection no_frontier_signature_index no_lazy_diagnostic_replay no_learned_feasibility_guidance \
  --progress > >(tee "$V9_ROOT/logs/v9_fast.log") 2>&1

python scripts/summarize_v9_results.py \
  --entry "v9_full=$V9_ROOT/runs/full" \
  --entry "v8_reference=$V9_ROOT/runs/v8_reference_runtime" \
  --entry "v2_reference=$V9_ROOT/runs/v2_reference_runtime" \
  --entry "v5_reference=$V9_ROOT/runs/v5_reference_runtime" \
  --entry "v9_structural_only=$V9_ROOT/runs/no_typed_viability" \
  --entry "v9_no_kernel=$V9_ROOT/runs/no_viability_kernel" \
  --entry "v9_no_projection=$V9_ROOT/runs/no_capability_projection" \
  --entry "v9_no_index=$V9_ROOT/runs/no_frontier_signature_index" \
  --entry "v9_no_lazy=$V9_ROOT/runs/no_lazy_diagnostic_replay" \
  --entry "v9_no_static_guidance=$V9_ROOT/runs/no_learned_feasibility_guidance" \
  --output "$V9_ROOT/v9_fast_summary.csv" | tee "$V9_ROOT/logs/summary.log"

for pair in "v8_reference_runtime:v9_vs_v8" "v2_reference_runtime:v9_vs_v2" "v5_reference_runtime:v9_vs_v5" "no_typed_viability:v9_vs_structural" "no_capability_projection:v9_vs_no_projection" "no_frontier_signature_index:v9_vs_no_index" "no_lazy_diagnostic_replay:v9_vs_no_lazy"; do
  ref="${pair%%:*}"; name="${pair##*:}"
  python scripts/compare_search_efficiency.py --reference "$V9_ROOT/runs/$ref" --candidate "$V9_ROOT/runs/full" --output "$V9_ROOT/${name}_paired.json" | tee "$V9_ROOT/logs/${name}.log"
done

python scripts/assess_v9_fast.py \
  --full "$V9_ROOT/runs/full" --v8 "$V9_ROOT/runs/v8_reference_runtime" --v2 "$V9_ROOT/runs/v2_reference_runtime" --v5 "$V9_ROOT/runs/v5_reference_runtime" \
  --structural "$V9_ROOT/runs/no_typed_viability" --no-kernel "$V9_ROOT/runs/no_viability_kernel" --no-projection "$V9_ROOT/runs/no_capability_projection" \
  --no-index "$V9_ROOT/runs/no_frontier_signature_index" --no-lazy "$V9_ROOT/runs/no_lazy_diagnostic_replay" \
  --v9-v8 "$V9_ROOT/v9_vs_v8_paired.json" --v9-v2 "$V9_ROOT/v9_vs_v2_paired.json" --v9-v5 "$V9_ROOT/v9_vs_v5_paired.json" \
  --v9-structural "$V9_ROOT/v9_vs_structural_paired.json" --v9-no-projection "$V9_ROOT/v9_vs_no_projection_paired.json" \
  --v9-no-index "$V9_ROOT/v9_vs_no_index_paired.json" --v9-no-lazy "$V9_ROOT/v9_vs_no_lazy_paired.json" \
  --output "$V9_ROOT/v9_fast_gate.json" | tee "$V9_ROOT/logs/gate.log"

echo "V9_FAST_ROOT=$V9_ROOT"
echo "Run V9 full only when v9_fast_gate.json status == GO"
