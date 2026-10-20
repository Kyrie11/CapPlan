#!/usr/bin/env bash
# V12 four-layer reproducibility gate. Run in repository root after installing patch.
set -euo pipefail
export PYTHONPATH="${PWD}:${PYTHONPATH:-}"
OUT="${V12_OUT:-$PWD/outputs/handoff_science_v12}"
mkdir -p "$OUT"
MODE="${1:-local}"
case "$MODE" in
  local)
    python -m pytest -q tests/test_science_v11.py tests/test_science_v12.py
    python scripts/benchmark_handoff_heuristics_v12.py \
      --cases examples/handoff_finite_v10.jsonl \
      --output "$OUT/heuristics_24.jsonl" --depths 2,3,4
    python scripts/build_handoff_sensitivity_v12.py \
      --cases examples/handoff_finite_v10.jsonl \
      --output "$OUT/contract_stress_168.jsonl"
    python scripts/benchmark_handoff_heuristics_v12.py \
      --cases "$OUT/contract_stress_168.jsonl" \
      --output "$OUT/heuristics_stress_168.jsonl" --depths 2,4
    ;;
  actual)
    : "${CAP_DATA:?Set CAP_DATA=/data0/senzeyu2/dataset/CapPlan/data}"
    : "${V11_ALL:?Set V11_ALL to completed V11 all_existing_pudos_128.jsonl}"
    python scripts/audit_science_v12.py \
      --all_existing "$V11_ALL" \
      --reports_dir "$CAP_DATA/external/reports" \
      --output "$OUT/v11_evidence_audit.json"
    for split in train val test; do
      f="$CAP_DATA/outputs/datasets/abilitybench_av_hybrid_${split}/pudo_anchors.jsonl"
      if [[ -f "$f" ]]; then
        python scripts/audit_anchor_provenance_v12.py \
          --pudo_jsonl "$f" --output "$OUT/${split}_provenance.json" \
          --review_csv "$OUT/${split}_evidence_review_sample.csv"
      else
        echo "[NOT_FOUND] $f; skip, do not infer split coverage" >&2
      fi
    done
    if [[ -f "$CAP_DATA/outputs/datasets/abilitybench_av_hybrid_test/episodes.jsonl" ]]; then
      python scripts/sample_unscreened_handoff_controls_v4.py \
        --dataset_dir "$CAP_DATA/outputs/datasets/abilitybench_av_hybrid_test" \
        --output "$OUT/preregistered_random_controls.jsonl" \
        --per_city "${V12_PER_CITY:-30}" --seed 20261020
    fi
    ;;
  *)echo "Usage: bash scripts/run_science_v12.sh local|actual" >&2; exit 2;;
esac
