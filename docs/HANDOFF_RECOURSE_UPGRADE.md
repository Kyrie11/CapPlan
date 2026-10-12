# Recourse-preserving handoff extension

This extension keeps the existing static AbilityBench-AV files intact and adds
temporal sidecars. It is intentionally scoped to one passenger, one vehicle,
and one pickup or dropoff handoff. A complete service can compose a pickup
handoff, the ordinary vehicle ride, and a dropoff handoff, but the new planning
claim is evaluated at the handoff boundary.

## Evidence boundary

- Static geometry and accessibility fields retain their original provenance.
- A generated blockage/availability change is labelled
  `explicit_control_counterfactual_not_observed_prevalence`.
- Pedestrian geometry never proves vehicle reachability. A full A-to-B witness
  requires a method-specific motion packet whose validity interval contains the
  event time.
- A two-candidate witness does not prove that every recovery option is absent.
  `global_no_recovery_claim` therefore remains false unless an exhaustive
  verifier checks the complete action set.

## Rollout schema for `compute_handoff_metrics.py`

Each JSONL row represents one executed world, not a static plan. Core fields:

- outcome: `handoff_confirmed`, `safe_continuation_confirmed`,
  `assistance_transfer_confirmed`, `planner_accepted`;
- commitment: `passenger_commitment_issued`,
  `robust_recourse_existed_at_commit`, `recourse_preserved_after_commit`,
  `evidence_valid_at_commit`;
- burden: `executed_passenger_distance_m`, `passenger_waiting_time_s`,
  `unsafe_wait_exposure_s`, `passenger_redirections`,
  `excess_distance_vs_clairvoyant_m`;
- coordination: `passenger_arrival_s`, `vehicle_arrival_s`,
  `acknowledgement_latency_s`, `missed_handoff_window`,
  `reported_handoff_confirmed`;
- information/compute: `observation_count`, `verification_latency_s`,
  `planning_latency_ms`, `expanded_states`;
- stratification: `profile_id`, `site_id`, `city`, and event class.

Missing fields are excluded from their metric denominator. They are never
silently interpreted as `false` for safety-critical rates.

## Profile policy

Keep two disjoint suites:

1. `capability_profiles.counterfactual.yaml`: canonical one-axis contracts for
   semantic tests. They have no population weight and should not be described
   as prevalence-realistic passengers.
2. Empirical evaluation contracts: consented task-level requirements from a
   user study or audited service records, with source protocol, sample frame,
   parameter uncertainty, and sampling weight. Standards can define physical
   compliance thresholds; they must not be used as estimates of a person's
   comfortable distance or waiting tolerance.

