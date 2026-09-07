#!/usr/bin/env bash
set -euo pipefail
CAP_HOME="${CAP_HOME:-$(cd "$(dirname "$0")/.." && pwd)}"; DATA_ROOT="${DATA_ROOT:?set DATA_ROOT}"; GPU0="${GPU0:-0}"; SEED="${SEED:-13}"
DATASET_DIR="${DATASET_DIR:-$DATA_ROOT/outputs/datasets/abilitybench_av_hybrid_test}"; CASA_CHECKPOINT="${CASA_CHECKPOINT:-$CAP_HOME/outputs/models/casa_relation_mlp_seed13/checkpoint.pt}"; V16_ROOT="${V16_ROOT:-$CAP_HOME/outputs/eval/v16_full_seed${SEED}}"
mkdir -p "$V16_ROOT/logs"; cd "$CAP_HOME"
runv(){ local name="$1" mode="$2"; CUDA_VISIBLE_DEVICES="$GPU0" python scripts/run_v16_eval_variant.py --dataset_dir "$DATASET_DIR" --output_dir "$V16_ROOT/$name" --casa_checkpoint "$CASA_CHECKPOINT" --mode "$mode" --episode_seed "$SEED" --show_progress | tee "$V16_ROOT/logs/${name}.log"; }
runv parametric_full parametric
runv exact_no_reuse exact
runv full_contract_key_cache full_contract_cache
python scripts/compare_v16_variants.py --reference "$V16_ROOT/exact_no_reuse" --candidate "$V16_ROOT/parametric_full" --output "$V16_ROOT/full_vs_exact.json"
python scripts/compare_v16_variants.py --reference "$V16_ROOT/full_contract_key_cache" --candidate "$V16_ROOT/parametric_full" --output "$V16_ROOT/full_vs_full_contract_key.json"
python scripts/compare_diagnostic_exactness.py --reference "$V16_ROOT/exact_no_reuse" --candidate "$V16_ROOT/parametric_full" --output "$V16_ROOT/full_vs_exact_diagnostic.json"
# Publication confirmatory timing uses the same four counterbalanced serial repeats as fast.
for r in 0 1 2 3; do
  OLD="$V16_ROOT"; T="$OLD/timing_repeat_$r"; mkdir -p "$T"
  if (( r % 2 == 0 )); then V16_ROOT="$T" runv exact_no_reuse exact; V16_ROOT="$T" runv parametric_full parametric; else V16_ROOT="$T" runv parametric_full parametric; V16_ROOT="$T" runv exact_no_reuse exact; fi
  V16_ROOT="$OLD"
done
python scripts/compare_v16_repeated_timing.py \
  --reference "$V16_ROOT/timing_repeat_0/exact_no_reuse" "$V16_ROOT/timing_repeat_1/exact_no_reuse" "$V16_ROOT/timing_repeat_2/exact_no_reuse" "$V16_ROOT/timing_repeat_3/exact_no_reuse" \
  --candidate "$V16_ROOT/timing_repeat_0/parametric_full" "$V16_ROOT/timing_repeat_1/parametric_full" "$V16_ROOT/timing_repeat_2/parametric_full" "$V16_ROOT/timing_repeat_3/parametric_full" \
  --output "$V16_ROOT/repeated_primary_exact_vs_parametric.json"
python scripts/assess_v16_fast.py --full "$V16_ROOT/parametric_full" --exact "$V16_ROOT/exact_no_reuse" --full-key "$V16_ROOT/full_contract_key_cache" --full-exact "$V16_ROOT/full_vs_exact.json" --full-full-key "$V16_ROOT/full_vs_full_contract_key.json" --exactness "$V16_ROOT/full_vs_exact_diagnostic.json" --repeat-primary "$V16_ROOT/repeated_primary_exact_vs_parametric.json" --output "$V16_ROOT/v16_full_gate.json"
cat "$V16_ROOT/v16_full_gate.json"
