#!/usr/bin/env bash
set -euo pipefail
CAP_HOME="${CAP_HOME:-$(cd "$(dirname "$0")/.." && pwd)}"
DATA_ROOT="${DATA_ROOT:?set DATA_ROOT}"
HYBRID_TEST="${HYBRID_TEST:-$DATA_ROOT/outputs/datasets/abilitybench_av_hybrid_test}"
DATASET_DIR="${DATASET_DIR:-$HYBRID_TEST}"
SEED="${SEED:-13}"; GPU0="${GPU0:-0}"; EPISODE_SEED="${EPISODE_SEED:-13}"
CASA_CHECKPOINT="${CASA_CHECKPOINT:-$CAP_HOME/outputs/models/casa_relation_mlp_seed13/checkpoint.pt}"
MODEL_ROOT="${MODEL_ROOT:-$CAP_HOME/outputs/models/v14_cqhpt_seed${SEED}}"
V14_ROOT="${V14_ROOT:-$CAP_HOME/outputs/eval/v14_full_seed${SEED}}"
mkdir -p "$V14_ROOT/logs"; cd "$CAP_HOME"
for mode in full late_fusion neutral_query query_only; do
  [[ -f "$MODEL_ROOT/$mode/checkpoint.pt" ]] || { echo "missing $MODEL_ROOT/$mode/checkpoint.pt" >&2; exit 2; }
done
[[ -f "$DATASET_DIR/cqhpt_evidence_cache.jsonl" ]] || { echo "missing canonical-test CQ-HPT evidence cache" >&2; exit 2; }

run_cq(){ local root="$1" name="$2" mode="$3"; CUDA_VISIBLE_DEVICES="$GPU0" python scripts/run_v14_eval_variant.py --dataset_dir "$DATASET_DIR" --output_dir "$root/$name" --casa_checkpoint "$CASA_CHECKPOINT" --cqhpt_checkpoint "$MODEL_ROOT/$mode/checkpoint.pt" --episode_seed "$EPISODE_SEED" --show_progress; }
run_exact(){ local root="$1" name="$2"; CUDA_VISIBLE_DEVICES="$GPU0" python scripts/run_v14_eval_variant.py --dataset_dir "$DATASET_DIR" --output_dir "$root/$name" --casa_checkpoint "$CASA_CHECKPOINT" --episode_seed "$EPISODE_SEED" --show_progress; }
run_legacy(){ local root="$1" name="$2"; CUDA_VISIBLE_DEVICES="$GPU0" python scripts/run_v14_eval_variant.py --dataset_dir "$DATASET_DIR" --output_dir "$root/$name" --casa_checkpoint "$CASA_CHECKPOINT" --legacy_static_guidance --episode_seed "$EPISODE_SEED" --show_progress; }

# Confirm the exact same causal suite on all 997 frozen test episodes.
run_cq "$V14_ROOT" full_cqhpt full
run_cq "$V14_ROOT" late_fusion late_fusion
run_cq "$V14_ROOT" neutral_query neutral_query
run_cq "$V14_ROOT" query_only query_only
run_exact "$V14_ROOT" exact_no_learning
run_legacy "$V14_ROOT" legacy_static

cmp(){ python scripts/compare_v14_variants.py --reference "$V14_ROOT/$1" --candidate "$V14_ROOT/full_cqhpt" --output "$V14_ROOT/$2"; }
cmp exact_no_learning full_vs_exact.json
cmp legacy_static full_vs_legacy.json
cmp late_fusion full_vs_late.json
cmp neutral_query full_vs_neutral.json
cmp query_only full_vs_query.json
python scripts/compare_diagnostic_exactness.py --reference "$V14_ROOT/exact_no_learning" --candidate "$V14_ROOT/full_cqhpt" --output "$V14_ROOT/full_vs_exact_diagnostic.json"

# Two counterbalanced full-test repeats are sufficient for confirmatory primary
# latency after the four-repeat fast mechanism gate has already passed.
for r in 0 1; do
  rr="$V14_ROOT/timing_repeat_$r"; mkdir -p "$rr"
  if (( r % 2 == 0 )); then
    run_exact "$rr" exact_no_learning
    run_cq "$rr" full_cqhpt full
  else
    run_cq "$rr" full_cqhpt full
    run_exact "$rr" exact_no_learning
  fi
done
python scripts/compare_repeated_timing.py \
  --latency-key primary_decision_latency_ms \
  --reference "$V14_ROOT/timing_repeat_0/exact_no_learning" "$V14_ROOT/timing_repeat_1/exact_no_learning" \
  --candidate "$V14_ROOT/timing_repeat_0/full_cqhpt" "$V14_ROOT/timing_repeat_1/full_cqhpt" \
  --output "$V14_ROOT/repeated_primary_exact_vs_full.json"

python scripts/assess_v14_fast.py \
  --full "$V14_ROOT/full_cqhpt" --late "$V14_ROOT/late_fusion" --neutral "$V14_ROOT/neutral_query" --query-only "$V14_ROOT/query_only" --exact "$V14_ROOT/exact_no_learning" --legacy "$V14_ROOT/legacy_static" \
  --full-exact "$V14_ROOT/full_vs_exact.json" --full-legacy "$V14_ROOT/full_vs_legacy.json" --full-late "$V14_ROOT/full_vs_late.json" --full-neutral "$V14_ROOT/full_vs_neutral.json" --full-query "$V14_ROOT/full_vs_query.json" \
  --exactness "$V14_ROOT/full_vs_exact_diagnostic.json" --repeat-primary "$V14_ROOT/repeated_primary_exact_vs_full.json" \
  --train-full "$MODEL_ROOT/full" --train-late "$MODEL_ROOT/late_fusion" --train-neutral "$MODEL_ROOT/neutral_query" --train-query "$MODEL_ROOT/query_only" \
  --output "$V14_ROOT/v14_full_gate.json"
cat "$V14_ROOT/v14_full_gate.json"
