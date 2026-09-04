#!/usr/bin/env bash
set -euo pipefail
CAP_HOME="${CAP_HOME:-/home/senzeyu2/code/CapPlan}"
DATA_ROOT="${DATA_ROOT:-/data0/senzeyu2/dataset/CapPlan/data}"
HYBRID_TEST="${HYBRID_TEST:-$DATA_ROOT/outputs/datasets/abilitybench_av_hybrid_test}"
GPU0="${GPU0:-0}"; SEED="${SEED:-13}"
EPISODE_LIMIT="${EPISODE_LIMIT:-256}"; EPISODE_SEED="${EPISODE_SEED:-13}"
CASA_CHECKPOINT="${CASA_CHECKPOINT:-$CAP_HOME/outputs/models/casa_relation_mlp_seed13/checkpoint.pt}"
V8_ROOT="${V8_ROOT:-$CAP_HOME/outputs/eval/v8_fast_seed${SEED}}"
mkdir -p "$V8_ROOT"/{runs,logs}; cd "$CAP_HOME"
[[ -f "$CASA_CHECKPOINT" ]] || { echo "missing CASA checkpoint: $CASA_CHECKPOINT" >&2; exit 2; }

# All latency-critical controls are serial in one process.  Do not launch a
# competing worker while this group runs.
CUDA_VISIBLE_DEVICES="$GPU0" python scripts/run_ablations.py \
  --dataset_dir "$HYBRID_TEST" --output_dir "$V8_ROOT/runs" \
  --trajectory_mode mock_strict --casa_mode learned --casa_checkpoint "$CASA_CHECKPOINT" --casa_device cuda:0 \
  --algorithm_version V8 --evidence_grounded_runtime \
  --episode_limit "$EPISODE_LIMIT" --episode_seed "$EPISODE_SEED" \
  --variants full v2_reference_runtime v5_reference_runtime no_typed_viability no_viability_kernel no_lazy_diagnostic_replay no_learned_feasibility_guidance \
  --progress > >(tee "$V8_ROOT/logs/v8_fast.log") 2>&1

python scripts/summarize_v8_results.py \
  --entry "v8_full=$V8_ROOT/runs/full" \
  --entry "v2_reference=$V8_ROOT/runs/v2_reference_runtime" \
  --entry "v5_reference=$V8_ROOT/runs/v5_reference_runtime" \
  --entry "v8_structural_only=$V8_ROOT/runs/no_typed_viability" \
  --entry "v8_no_kernel=$V8_ROOT/runs/no_viability_kernel" \
  --entry "v8_no_lazy_proof=$V8_ROOT/runs/no_lazy_diagnostic_replay" \
  --entry "v8_no_static_guidance=$V8_ROOT/runs/no_learned_feasibility_guidance" \
  --output "$V8_ROOT/v8_fast_summary.csv" | tee "$V8_ROOT/logs/summary.log"

python scripts/compare_search_efficiency.py --reference "$V8_ROOT/runs/v2_reference_runtime" --candidate "$V8_ROOT/runs/full" --output "$V8_ROOT/v8_vs_v2_paired.json" | tee "$V8_ROOT/logs/v8_vs_v2.log"
python scripts/compare_search_efficiency.py --reference "$V8_ROOT/runs/v5_reference_runtime" --candidate "$V8_ROOT/runs/full" --output "$V8_ROOT/v8_vs_v5_paired.json" | tee "$V8_ROOT/logs/v8_vs_v5.log"
python scripts/compare_search_efficiency.py --reference "$V8_ROOT/runs/no_typed_viability" --candidate "$V8_ROOT/runs/full" --output "$V8_ROOT/v8_vs_structural_paired.json" | tee "$V8_ROOT/logs/v8_vs_structural.log"
python scripts/compare_search_efficiency.py --reference "$V8_ROOT/runs/no_lazy_diagnostic_replay" --candidate "$V8_ROOT/runs/full" --output "$V8_ROOT/v8_vs_no_lazy_paired.json" | tee "$V8_ROOT/logs/v8_vs_no_lazy.log"

python scripts/assess_v8_fast.py \
  --full "$V8_ROOT/runs/full" --v2 "$V8_ROOT/runs/v2_reference_runtime" --v5 "$V8_ROOT/runs/v5_reference_runtime" \
  --structural "$V8_ROOT/runs/no_typed_viability" --no-kernel "$V8_ROOT/runs/no_viability_kernel" --no-lazy "$V8_ROOT/runs/no_lazy_diagnostic_replay" \
  --v8-v2 "$V8_ROOT/v8_vs_v2_paired.json" --v8-v5 "$V8_ROOT/v8_vs_v5_paired.json" \
  --v8-structural "$V8_ROOT/v8_vs_structural_paired.json" --v8-no-lazy "$V8_ROOT/v8_vs_no_lazy_paired.json" \
  --output "$V8_ROOT/v8_fast_gate.json" | tee "$V8_ROOT/logs/gate.log"

echo "V8_FAST_ROOT=$V8_ROOT"
echo "Run full only when: python - <<'PY' ... v8_fast_gate.json status == GO"
