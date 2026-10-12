#!/usr/bin/env bash
# Controlled counterfactual matrix on FROZEN mined geometry. No real incident rates.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUTPUT_ROOT="${OUTPUT_ROOT:-$ROOT/outputs/handoff_funnel}"
PAIRS="${PAIRS:-$OUTPUT_ROOT/mining/recourse_pairs.jsonl}"
[[ -f "$PAIRS" ]] || { echo "Missing mined pairs: $PAIRS" >&2; exit 2; }
cd "$ROOT"
run_case() {
  local name="$1" timing="$2" schedule="$3" delay="$4" board="$5" clear="$6" event="$7"
  local dir="$OUTPUT_ROOT/sensitivity/$name"
  mkdir -p "$dir"
  python scripts/build_handoff_temporal_overlays.py \
    --recourse_pairs "$PAIRS" --output "$dir/overlays.jsonl" \
    --event_time_s "$event" --event_timing "$timing" \
    --event_lead_s "${EVENT_LEAD_S:-15}" \
    --passenger_speed_mps "${PASSENGER_SPEED_MPS:-1}" \
    --observation_schedule "$schedule" --observation_delay_s "$delay" \
    --ack_delay_s "${ACK_DELAY_S:-3}" --clear_probability "$clear"
  python scripts/evaluate_handoff_policies.py \
    --overlays "$dir/overlays.jsonl" --output_dir "$dir/policies" \
    --speed_mps "${PASSENGER_SPEED_MPS:-1}" --boarding_s "$board"
}
run_case early_observation before_a_arrival immediate 5 10 0.5 90
run_case timed_information_upper_bound before_a_arrival event_aligned_upper_bound 5 10 0.5 90
run_case slow_timed_information_upper_bound before_a_arrival event_aligned_upper_bound 20 10 0.5 90
run_case extended_boarding_upper_bound before_a_arrival event_aligned_upper_bound 5 30 0.5 90
run_case rare_failure_upper_bound before_a_arrival event_aligned_upper_bound 5 10 0.9 90
run_case frequent_failure_upper_bound before_a_arrival event_aligned_upper_bound 5 10 0.1 90
run_case late_event_negative_control fixed immediate 5 10 0.5 100000
python scripts/summarize_handoff_sensitivity.py \
  --sensitivity_dir "$OUTPUT_ROOT/sensitivity" \
  --output "$OUTPUT_ROOT/sensitivity/sensitivity_report.json"
