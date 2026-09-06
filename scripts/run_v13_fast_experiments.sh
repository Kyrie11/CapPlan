#!/usr/bin/env bash
set -euo pipefail
CAP_HOME="${CAP_HOME:-/home/senzeyu2/code/CapPlan}"
DATA_ROOT="${DATA_ROOT:-/data0/senzeyu2/dataset/CapPlan/data}"
HYBRID_TEST="${HYBRID_TEST:-$DATA_ROOT/outputs/datasets/abilitybench_av_hybrid_test}"
GPU0="${GPU0:-0}"; SEED="${SEED:-13}"
EPISODE_LIMIT="${EPISODE_LIMIT:-256}"; EPISODE_SEED="${EPISODE_SEED:-13}"
CASA_CHECKPOINT="${CASA_CHECKPOINT:-$CAP_HOME/outputs/models/casa_relation_mlp_seed13/checkpoint.pt}"
V13_ROOT="${V13_ROOT:-$CAP_HOME/outputs/eval/v13_fast_seed${SEED}}"
mkdir -p "$V13_ROOT"/{runs,logs,timing_repeat_0,timing_repeat_1,timing_repeat_2}; cd "$CAP_HOME"
[[ -f "$CASA_CHECKPOINT" ]] || { echo "missing CASA checkpoint: $CASA_CHECKPOINT" >&2; exit 2; }

# V13 is the single preregistered contingency after V12 falsified exact state-
# keyed semantic memoization as a useful diagnostic accelerator. The acceptance
# backbone remains V11/V12 SN-CPK; only diagnostic replay may use request-local,
# ledger-agnostic compiled transition programs.
CUDA_VISIBLE_DEVICES="$GPU0" python scripts/run_ablations.py \
  --dataset_dir "$HYBRID_TEST" --output_dir "$V13_ROOT/runs" \
  --trajectory_mode mock_strict --casa_mode learned --casa_checkpoint "$CASA_CHECKPOINT" --casa_device cuda:0 \
  --algorithm_version V13 --evidence_grounded_runtime \
  --episode_limit "$EPISODE_LIMIT" --episode_seed "$EPISODE_SEED" \
  --variants full v11_reference_runtime v12_reference_runtime v2_reference_runtime no_typed_viability no_lazy_diagnostic_replay no_compiled_diagnostic_transition_program v13_legacy_static_guidance \
  --progress > >(tee "$V13_ROOT/logs/v13_fast.log") 2>&1

python scripts/summarize_v13_results.py \
  --entry "v13_full=$V13_ROOT/runs/full" \
  --entry "v11_reference=$V13_ROOT/runs/v11_reference_runtime" \
  --entry "v12_reference=$V13_ROOT/runs/v12_reference_runtime" \
  --entry "v2_reference=$V13_ROOT/runs/v2_reference_runtime" \
  --entry "structural_only=$V13_ROOT/runs/no_typed_viability" \
  --entry "no_lazy=$V13_ROOT/runs/no_lazy_diagnostic_replay" \
  --entry "no_program=$V13_ROOT/runs/no_compiled_diagnostic_transition_program" \
  --entry "legacy_guidance=$V13_ROOT/runs/v13_legacy_static_guidance" \
  --output "$V13_ROOT/v13_fast_summary.csv" | tee "$V13_ROOT/logs/summary.log"

for spec in \
  "v11_reference_runtime:v11_vs_v13" \
  "v12_reference_runtime:v12_vs_v13" \
  "v2_reference_runtime:v2_vs_v13" \
  "no_typed_viability:structural_vs_v13" \
  "no_lazy_diagnostic_replay:no_lazy_vs_v13" \
  "no_compiled_diagnostic_transition_program:no_program_vs_v13" \
  "v13_legacy_static_guidance:legacy_vs_v13"; do
  ref="${spec%%:*}"; name="${spec##*:}"
  python scripts/compare_search_efficiency.py \
    --reference "$V13_ROOT/runs/$ref" --candidate "$V13_ROOT/runs/full" \
    --output "$V13_ROOT/${name}_paired.json" > "$V13_ROOT/logs/${name}.log"
done

python scripts/compare_diagnostic_exactness.py \
  --reference "$V13_ROOT/runs/v11_reference_runtime" --candidate "$V13_ROOT/runs/full" \
  --output "$V13_ROOT/v11_vs_v13_diagnostic_exactness.json" \
  | tee "$V13_ROOT/logs/diagnostic_exactness.log"

# Three counterbalanced serial timing blocks. no-program is the direct causal
# reference; V2 closes the historical <=2x gate; legacy is secondary only.
orders=(
  "v2_reference_runtime no_compiled_diagnostic_transition_program full v13_legacy_static_guidance"
  "full v13_legacy_static_guidance no_compiled_diagnostic_transition_program v2_reference_runtime"
  "v13_legacy_static_guidance v2_reference_runtime full no_compiled_diagnostic_transition_program"
)
for i in 0 1 2; do
  out="$V13_ROOT/timing_repeat_$i"
  # shellcheck disable=SC2086
  CUDA_VISIBLE_DEVICES="$GPU0" python scripts/run_ablations.py \
    --dataset_dir "$HYBRID_TEST" --output_dir "$out" \
    --trajectory_mode mock_strict --casa_mode learned --casa_checkpoint "$CASA_CHECKPOINT" --casa_device cuda:0 \
    --algorithm_version V13 --evidence_grounded_runtime \
    --episode_limit "$EPISODE_LIMIT" --episode_seed "$EPISODE_SEED" \
    --variants ${orders[$i]} --progress \
    > >(tee "$V13_ROOT/logs/timing_repeat_${i}.log") 2>&1
done

python scripts/compare_repeated_timing.py \
  --reference "$V13_ROOT/timing_repeat_0/no_compiled_diagnostic_transition_program" "$V13_ROOT/timing_repeat_1/no_compiled_diagnostic_transition_program" "$V13_ROOT/timing_repeat_2/no_compiled_diagnostic_transition_program" \
  --candidate "$V13_ROOT/timing_repeat_0/full" "$V13_ROOT/timing_repeat_1/full" "$V13_ROOT/timing_repeat_2/full" \
  --output "$V13_ROOT/repeated_no_program_vs_v13.json" > "$V13_ROOT/logs/repeated_no_program_vs_v13.log"
python scripts/compare_repeated_timing.py \
  --reference "$V13_ROOT/timing_repeat_0/v2_reference_runtime" "$V13_ROOT/timing_repeat_1/v2_reference_runtime" "$V13_ROOT/timing_repeat_2/v2_reference_runtime" \
  --candidate "$V13_ROOT/timing_repeat_0/full" "$V13_ROOT/timing_repeat_1/full" "$V13_ROOT/timing_repeat_2/full" \
  --output "$V13_ROOT/repeated_v2_vs_v13.json" > "$V13_ROOT/logs/repeated_v2_vs_v13.log"
python scripts/compare_repeated_timing.py \
  --reference "$V13_ROOT/timing_repeat_0/v13_legacy_static_guidance" "$V13_ROOT/timing_repeat_1/v13_legacy_static_guidance" "$V13_ROOT/timing_repeat_2/v13_legacy_static_guidance" \
  --candidate "$V13_ROOT/timing_repeat_0/full" "$V13_ROOT/timing_repeat_1/full" "$V13_ROOT/timing_repeat_2/full" \
  --output "$V13_ROOT/repeated_legacy_vs_v13.json" > "$V13_ROOT/logs/repeated_legacy_vs_v13.log"

python scripts/assess_v13_fast.py \
  --full "$V13_ROOT/runs/full" --v11 "$V13_ROOT/runs/v11_reference_runtime" --v12 "$V13_ROOT/runs/v12_reference_runtime" \
  --v2 "$V13_ROOT/runs/v2_reference_runtime" --structural "$V13_ROOT/runs/no_typed_viability" \
  --no-lazy "$V13_ROOT/runs/no_lazy_diagnostic_replay" --no-program "$V13_ROOT/runs/no_compiled_diagnostic_transition_program" \
  --legacy "$V13_ROOT/runs/v13_legacy_static_guidance" \
  --v11-v13 "$V13_ROOT/v11_vs_v13_paired.json" --v12-v13 "$V13_ROOT/v12_vs_v13_paired.json" \
  --v2-v13 "$V13_ROOT/v2_vs_v13_paired.json" --structural-v13 "$V13_ROOT/structural_vs_v13_paired.json" \
  --no-lazy-v13 "$V13_ROOT/no_lazy_vs_v13_paired.json" --no-program-v13 "$V13_ROOT/no_program_vs_v13_paired.json" \
  --legacy-v13 "$V13_ROOT/legacy_vs_v13_paired.json" --exact-v11-v13 "$V13_ROOT/v11_vs_v13_diagnostic_exactness.json" \
  --repeat-no-program-v13 "$V13_ROOT/repeated_no_program_vs_v13.json" --repeat-v2-v13 "$V13_ROOT/repeated_v2_vs_v13.json" \
  --repeat-legacy-v13 "$V13_ROOT/repeated_legacy_vs_v13.json" \
  --output "$V13_ROOT/v13_fast_gate.json" | tee "$V13_ROOT/logs/gate.log"

echo "V13_FAST_ROOT=$V13_ROOT"
echo "If V13 STOPs, do not create another exact-diagnostic cache variant. Move the next algorithmic mainline to CQ-HPT under the frozen exact semantic authority."
