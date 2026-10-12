#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
: "${DATASET_TEST:?Set DATASET_TEST to the hybrid test dataset path}"
OUTPUT_ROOT="${OUTPUT_ROOT:-$ROOT/outputs/handoff_funnel}"
EPISODE_LIMIT="${EPISODE_LIMIT:-100}"
BUDGET_M="${BUDGET_M:-200}"
EXTRA=()
if [[ -n "${MOTION_PACKETS:-}" ]]; then EXTRA+=(--vehicle_reachability_jsonl "$MOTION_PACKETS"); fi
if [[ "${REQUIRE_INTERFACE:-0}" == 1 ]]; then EXTRA+=(--require_interface_complete); fi
if [[ "${REQUIRE_CURB_CONNECTOR:-0}" == 1 ]]; then EXTRA+=(--require_explicit_curb_connector); fi
cd "$ROOT"
python scripts/mine_handoff_recourse_pairs.py --dataset_dir "$DATASET_TEST" \
 --output_dir "$OUTPUT_ROOT/mining" --episode_limit "$EPISODE_LIMIT" \
 --seed "${SEED:-13}" --distance_budget_m "$BUDGET_M" --event_time_s "${EVENT_TIME_S:-90}" \
 --min_width_m "${MIN_WIDTH_M:-0.9}" --max_slope "${MAX_SLOPE:-0.08}" \
 --max_cross_slope "${MAX_CROSS_SLOPE:-0.04}" --require_step_free "${EXTRA[@]}"
python scripts/audit_handoff_funnel.py \
 --pairs "$OUTPUT_ROOT/mining/recourse_pairs.jsonl" \
 --mining_audit "$OUTPUT_ROOT/mining/mining_audit.json" \
 --output_dir "$OUTPUT_ROOT/audit"
python scripts/export_handoff_motion_requests.py \
 --pairs "$OUTPUT_ROOT/mining/recourse_pairs.jsonl" \
 --output "$OUTPUT_ROOT/motion_requests.jsonl"
python scripts/build_handoff_temporal_overlays.py \
 --recourse_pairs "$OUTPUT_ROOT/mining/recourse_pairs.jsonl" \
 --output "$OUTPUT_ROOT/temporal_overlays.jsonl" \
 --event_time_s "${EVENT_TIME_S:-90}" --observation_delay_s "${OBS_DELAY_S:-5}" \
 --ack_delay_s "${ACK_DELAY_S:-3}" --clear_probability "${CLEAR_PROB:-0.5}" \
 --observation_request_time_s "${OBS_REQUEST_TIME_S:-0}" \
 --observation_schedule "${OBS_SCHEDULE:-immediate}" \
 --event_timing "${EVENT_TIMING:-before_a_arrival}" \
 --event_lead_s "${EVENT_LEAD_S:-15}" \
 --passenger_speed_mps "${PASSENGER_SPEED_MPS:-1.0}"
python scripts/evaluate_handoff_policies.py \
 --overlays "$OUTPUT_ROOT/temporal_overlays.jsonl" --output_dir "$OUTPUT_ROOT/policies" \
 --speed_mps "${PASSENGER_SPEED_MPS:-1.0}" --boarding_s "${BOARDING_S:-10}"
python scripts/bootstrap_handoff_site_ci.py \
 --rollouts "$OUTPUT_ROOT/policies/policy_rollouts.jsonl" \
 --output "$OUTPUT_ROOT/policies/site_cluster_ci.json" --seed "${SEED:-13}"
python scripts/build_handoff_profile_suite.py \
 --suite canonical --seed "${SEED:-13}" --per_archetype "${PROFILE_SAMPLES:-16}" \
 --output "$OUTPUT_ROOT/canonical_profiles.jsonl"

# Optional non-population sensitivity sweep; may be comparatively expensive.
if [[ "${PROFILE_SWEEP:-0}" == 1 ]]; then
  python scripts/screen_handoff_profile_sweep.py --dataset_dir "$DATASET_TEST" \
    --output_dir "$OUTPUT_ROOT/profile_sweep" --episode_limit "$EPISODE_LIMIT" \
    --samples "${PROFILE_SWEEP_SAMPLES:-3}" --seed "${SEED:-13}"
fi

python scripts/assess_handoff_research_gate.py \
 --funnel_report "$OUTPUT_ROOT/audit/funnel_report.json" \
 --policy_summary "$OUTPUT_ROOT/policies/policy_summary.json" \
 --review_csv "$OUTPUT_ROOT/audit/human_review_queue.csv" \
 --output "$OUTPUT_ROOT/research_gate.json"
