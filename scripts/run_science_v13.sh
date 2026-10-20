#!/usr/bin/env bash
# Bounded, non-destructive Science V13 experiment suite.
set -euo pipefail
export PYTHONPATH="${PWD}:${PYTHONPATH:-}"
V13_OUT="${V13_OUT:-$PWD/outputs/handoff_science_v13}"
mkdir -p "$V13_OUT"
mode="${1:-local}"
case "$mode" in
  local)
    python -m pytest -q tests/test_science_v13.py tests/test_science_v12.py
    python scripts/structural_gap_toy_v13.py --output "$V13_OUT/static_two_site_truth_table.json"
    if [[ -n "${V12_RESULTS:-}" ]]; then
      python scripts/reanalyse_heuristics_v13.py \
        --inputs "$V12_RESULTS/heuristics_24.jsonl" \
        --output "$V13_OUT/reanalysis_24.json"
      python scripts/reanalyse_heuristics_v13.py \
        --inputs "$V12_RESULTS/heuristics_stress_168.jsonl" \
        --output "$V13_OUT/reanalysis_168.json"
    else
      echo "[SKIP] V12_RESULTS unset; set it to extracted handoff_science_v12/ for post-analysis" >&2
    fi
    ;;
  inventory)
    : "${CAP_DATA:?Set CAP_DATA=/data0/senzeyu2/dataset/CapPlan/data}"
    python scripts/probe_capplan_tree_v13.py --data_root "$CAP_DATA" \
      --depth 7 --max_nodes 50000 \
      --output "$V13_OUT/data_tree_inventory.json"
    ;;
  actual)
    : "${CAP_DATA:?Set CAP_DATA=/data0/senzeyu2/dataset/CapPlan/data}"
    COHORT="${V12_COHORT:-$PWD/outputs/handoff_science_v12/preregistered_random_controls.jsonl}"
    DATASET="$CAP_DATA/outputs/datasets/abilitybench_av_hybrid_test"
    python scripts/audit_natural_structural_v13.py \
      --dataset "$DATASET" --sample "$COHORT" \
      --output "$V13_OUT/natural_120.jsonl" \
      --graph_limit "${V13_GRAPH_LIMIT:-12}"
    python scripts/audit_pudo_event_tags_v13.py \
      --episodes "$DATASET/episodes.jsonl" --cohort "$COHORT" \
      --output "$V13_OUT/natural_120_pudo_scenario_tags.json"
    ;;
  *) echo 'Usage: bash scripts/run_science_v13.sh local|inventory|actual' >&2; exit 2;;
esac
