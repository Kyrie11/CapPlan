#!/usr/bin/env bash
set -euo pipefail
CAP_HOME="${CAP_HOME:-$(cd "$(dirname "$0")/.." && pwd)}"; DATA_ROOT="${DATA_ROOT:?set DATA_ROOT}"; DATASET_DIR="${DATASET_DIR:-$DATA_ROOT/outputs/datasets/abilitybench_av_hybrid_test}"
SEED="${SEED:-13}"; GPU0="${GPU0:-0}"; ROBUSTNESS_WEIGHT="${ROBUSTNESS_WEIGHT:-0.35}"; CASA_CHECKPOINT="${CASA_CHECKPOINT:-$CAP_HOME/outputs/models/casa_relation_mlp_seed13/checkpoint.pt}"; V15_ROOT="${V15_ROOT:-$CAP_HOME/outputs/eval/v15_full_seed${SEED}}"
mkdir -p "$V15_ROOT"; cd "$CAP_HOME"
for spec in 'ecrp_full full' 'exact_no_ordering exact' 'summary_count_only count_only' 'legacy_static legacy_static'; do set -- $spec; CUDA_VISIBLE_DEVICES="$GPU0" python scripts/run_v15_eval_variant.py --dataset_dir "$DATASET_DIR" --output_dir "$V15_ROOT/$1" --casa_checkpoint "$CASA_CHECKPOINT" --mode "$2" --robustness_weight "$ROBUSTNESS_WEIGHT" --episode_seed 13 --show_progress; done
python scripts/compare_v15_variants.py --reference "$V15_ROOT/exact_no_ordering" --candidate "$V15_ROOT/ecrp_full" --output "$V15_ROOT/full_vs_exact.json"
python scripts/compare_v15_variants.py --reference "$V15_ROOT/legacy_static" --candidate "$V15_ROOT/ecrp_full" --output "$V15_ROOT/full_vs_legacy.json"
python scripts/compare_v15_variants.py --reference "$V15_ROOT/summary_count_only" --candidate "$V15_ROOT/ecrp_full" --output "$V15_ROOT/full_vs_count.json"
python scripts/compare_diagnostic_exactness.py --reference "$V15_ROOT/exact_no_ordering" --candidate "$V15_ROOT/ecrp_full" --output "$V15_ROOT/full_vs_exact_diagnostic.json"
