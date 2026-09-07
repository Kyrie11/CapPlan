#!/usr/bin/env bash
set -euo pipefail
CAP_HOME="${CAP_HOME:-$(cd "$(dirname "$0")/.." && pwd)}"; DATA_ROOT="${DATA_ROOT:?set DATA_ROOT}"
DATASET_DIR="${DATASET_DIR:-$DATA_ROOT/outputs/datasets/abilitybench_av_hybrid_test}"; SEED="${SEED:-13}"; GPU0="${GPU0:-0}"
EPISODE_LIMIT="${EPISODE_LIMIT:-256}"; EPISODE_SEED="${EPISODE_SEED:-13}"; ROBUSTNESS_WEIGHT="${ROBUSTNESS_WEIGHT:-0.35}"
CASA_CHECKPOINT="${CASA_CHECKPOINT:-$CAP_HOME/outputs/models/casa_relation_mlp_seed13/checkpoint.pt}"; V15_ROOT="${V15_ROOT:-$CAP_HOME/outputs/eval/v15r_fast_seed${SEED}}"
mkdir -p "$V15_ROOT/logs"; cd "$CAP_HOME"
runv(){ local name="$1" mode="$2"; CUDA_VISIBLE_DEVICES="$GPU0" python scripts/run_v15_eval_variant.py --dataset_dir "$DATASET_DIR" --output_dir "$V15_ROOT/$name" --casa_checkpoint "$CASA_CHECKPOINT" --mode "$mode" --robustness_weight "$ROBUSTNESS_WEIGHT" --episode_limit "$EPISODE_LIMIT" --episode_seed "$EPISODE_SEED" --show_progress; }
runv ecrp_full full; runv exact_no_ordering exact; runv summary_count_only count_only; runv legacy_static legacy_static
cmp(){ python scripts/compare_v15_variants.py --reference "$V15_ROOT/$1" --candidate "$V15_ROOT/ecrp_full" --output "$V15_ROOT/$2"; }
cmp exact_no_ordering full_vs_exact.json; cmp legacy_static full_vs_legacy.json; cmp summary_count_only full_vs_count.json
python scripts/compare_diagnostic_exactness.py --reference "$V15_ROOT/exact_no_ordering" --candidate "$V15_ROOT/ecrp_full" --output "$V15_ROOT/full_vs_exact_diagnostic.json"
# Counterbalanced serial timing; timing is a mandatory promotion gate because the exact frontier is already tiny.
for r in 0 1 2 3; do
  OLD="$V15_ROOT"; T="$OLD/timing_repeat_$r"; mkdir -p "$T"
  if (( r % 2 == 0 )); then V15_ROOT="$T" runv exact_no_ordering exact; V15_ROOT="$T" runv ecrp_full full; else V15_ROOT="$T" runv ecrp_full full; V15_ROOT="$T" runv exact_no_ordering exact; fi
  V15_ROOT="$OLD"
done
python scripts/compare_repeated_timing.py --latency-key primary_decision_latency_ms \
  --reference "$V15_ROOT/timing_repeat_0/exact_no_ordering" "$V15_ROOT/timing_repeat_1/exact_no_ordering" "$V15_ROOT/timing_repeat_2/exact_no_ordering" "$V15_ROOT/timing_repeat_3/exact_no_ordering" \
  --candidate "$V15_ROOT/timing_repeat_0/ecrp_full" "$V15_ROOT/timing_repeat_1/ecrp_full" "$V15_ROOT/timing_repeat_2/ecrp_full" "$V15_ROOT/timing_repeat_3/ecrp_full" \
  --output "$V15_ROOT/repeated_primary_exact_vs_ecrp.json"
python scripts/assess_v15_fast.py --full "$V15_ROOT/ecrp_full" --exact "$V15_ROOT/exact_no_ordering" --legacy "$V15_ROOT/legacy_static" --count-only "$V15_ROOT/summary_count_only" \
  --full-exact "$V15_ROOT/full_vs_exact.json" --full-legacy "$V15_ROOT/full_vs_legacy.json" --full-count "$V15_ROOT/full_vs_count.json" --exactness "$V15_ROOT/full_vs_exact_diagnostic.json" --repeat-primary "$V15_ROOT/repeated_primary_exact_vs_ecrp.json" --output "$V15_ROOT/v15r_fast_gate.json"
cat "$V15_ROOT/v15r_fast_gate.json"
