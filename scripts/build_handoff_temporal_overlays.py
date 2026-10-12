#!/usr/bin/env python3
"""Build auditable temporal-world sidecars from mined A/B handoff pairs.

The generated failure is an explicit controlled counterfactual.  It is useful
for mechanism evaluation but must not be reported as observed event prevalence.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from capplan.data.handoff_schema import validate_temporal_overlay


def _rows(path: str):
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            yield json.loads(line)


def build_overlay(pair: dict, index: int, event_time_s: float, observation_delay_s: float,
                  ack_delay_s: float, clear_probability: float,
                  observation_request_time_s: float = 0.0,
                  event_timing: str = 'fixed', event_lead_s: float = 15.0,
                  passenger_speed_mps: float = 1.0,
                  observation_schedule: str = 'immediate') -> dict:
    a, b, origin = pair["a_anchor"], pair["b_anchor"], pair["origin_anchor"]
    distances = pair["distances_m"]
    budget = pair["screening_contract"]["distance_budget_m"]
    already = pair["screening_contract"].get("already_consumed_m", 0.0)
    source_class = pair.get("evidence_class", "unknown")
    if passenger_speed_mps <= 0 or event_lead_s < 0:
        raise ValueError('invalid passenger speed or event lead')
    # For a stress counterfactual, make the event potentially relevant to the
    # passenger's actual A arrival; this is NOT a measurement of event timing.
    a_arrival_s = ack_delay_s + float(distances['origin_to_a']) / passenger_speed_mps
    if event_timing == 'before_a_arrival':
        event_time_s = max(0.0, a_arrival_s - event_lead_s)
    elif event_timing != 'fixed':
        raise ValueError('unsupported event timing')
    if observation_schedule == 'immediate':
        scheduled_request = 0.0
        schedule_provenance = 'request_now_not_future_event_knowledge'
    elif observation_schedule == 'fixed':
        scheduled_request = observation_request_time_s
        schedule_provenance = 'exogenous_fixed_observation_schedule'
    elif observation_schedule == 'event_aligned_upper_bound':
        scheduled_request = event_time_s
        schedule_provenance = 'PRIVILEGED_KNOWN_INTERVENTION_TIME_UPPER_BOUND_NOT_DEPLOYABLE'
    else:
        raise ValueError('unsupported observation schedule')
    overlay = {
        "overlay_id": f'{pair["episode_id"]}:pickup_recourse:{index:04d}',
        "episode_id": pair["episode_id"],
        "phase": "pickup",
        "initial_execution_state": {
            "timestamp_s": 0.0,
            "passenger_anchor": origin,
            "vehicle_anchor": "nuplan_ego_initial",
            "walked_or_rolled_m": float(already),
            "waited_s": 0.0,
            "redirections": 0,
            "commitments": [],
            "observations": [],
            "handoff_status": "not_started",
        },
        "capability_budget": {
            "distance_budget_m": float(budget),
            "remaining_distance_m": float(budget) - float(already),
            "wait_budget_s": None,
            "profile_role": "controlled_functional_contract_not_population_sample",
        },
        "candidate_handoffs": [
            {
                "anchor_id": a,
                "direct_passenger_distance_m": float(distances["origin_to_a"]),
                "from_a_to_b_distance_m": float(distances["a_to_b"]),
                "vehicle_motion_evidence": pair.get("a_to_b_vehicle_motion_packet"),
            },
            {
                "anchor_id": b,
                "direct_passenger_distance_m": float(distances["origin_to_b"]),
                "vehicle_motion_evidence": None,
            },
        ],
        "world_support": [
            {
                "world_id": "a_remains_available",
                "probability": float(clear_probability),
                "availability_trace": {a: [[0.0, True]], b: [[0.0, True]]},
                "evidence_class": "controlled_counterfactual_world",
            },
            {
                "world_id": "a_fails_after_commitment",
                "probability": float(1.0 - clear_probability),
                "availability_trace": {a: [[0.0, True], [float(event_time_s), False]], b: [[0.0, True]]},
                "evidence_class": "controlled_counterfactual_world",
            },
        ],
        "observation_model": {
            "availability_observation_delay_s": float(observation_delay_s),
            "scheduled_observation_request_s": float(scheduled_request),
            "schedule_provenance": schedule_provenance,
            "instruction_acknowledgement_delay_s": float(ack_delay_s),
            "observations_reveal_current_state_only": True,
            "future_events_not_revealed": True,
            "validity_interval_required_for_commit": True,
        },
        "handoff_confirmation": {
            "requires_passenger_vehicle_colocation": True,
            "requires_interface_available_for_full_boarding_interval": True,
            "requires_identity_confirmation": True,
            "requires_explicit_confirmation": True,
            "failure_is_not_inferred_from_vehicle_arrival_alone": True,
        },
        "motivation_witness": {
            "both_direct_choices_within_budget": (
                already + distances["origin_to_a"] <= budget
                and already + distances["origin_to_b"] <= budget
            ),
            "a_then_b_exceeds_budget": already + distances["origin_to_a"] + distances["a_to_b"] > budget,
            "vehicle_a_to_b_verified_for_event_time": bool(pair.get("two_candidate_distance_and_vehicle_witness")),
            "global_no_recovery_claim": False,
            "counterfactual_a_arrival_s": float(a_arrival_s),
            "counterfactual_event_time_s": float(event_time_s),
            "a_failure_affects_arrival": bool(event_time_s <= a_arrival_s + 10.0),
        },
        "provenance": {
            "static_geometry": source_class,
            "dynamic_event": "explicit_control_counterfactual_not_observed_prevalence",
            "event_timing_design": event_timing,
            "observation_schedule_design": observation_schedule,
            "privileged_schedule_upper_bound": observation_schedule == 'event_aligned_upper_bound',
            "source_pair": pair,
        },
    }
    validate_temporal_overlay(overlay)
    return overlay


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--recourse_pairs", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--event_time_s", type=float, default=90.0)
    parser.add_argument("--observation_delay_s", type=float, default=5.0)
    parser.add_argument("--ack_delay_s", type=float, default=3.0)
    parser.add_argument("--clear_probability", type=float, default=0.5)
    parser.add_argument("--observation_request_time_s", type=float, default=0.0,
                        help="observable exogenous request slot, not derived from future traces")
    parser.add_argument("--observation_schedule", choices=['immediate','fixed','event_aligned_upper_bound'],
                        default='immediate', help='aligned is an explicitly privileged experimental upper bound')
    parser.add_argument("--event_timing", choices=['fixed','before_a_arrival'], default='before_a_arrival')
    parser.add_argument("--event_lead_s", type=float, default=15.0)
    parser.add_argument("--passenger_speed_mps", type=float, default=1.0)
    args = parser.parse_args()
    if not 0.0 < args.clear_probability < 1.0:
        parser.error("clear_probability must be in (0,1)")
    if min(args.event_time_s, args.observation_delay_s, args.ack_delay_s,
           args.observation_request_time_s, args.event_lead_s) < 0 or args.passenger_speed_mps <= 0:
        parser.error("time parameters must be nonnegative")
    overlays = [build_overlay(row, i, args.event_time_s, args.observation_delay_s,
                              args.ack_delay_s, args.clear_probability,
                              args.observation_request_time_s, args.event_timing,
                              args.event_lead_s, args.passenger_speed_mps, args.observation_schedule)
                for i, row in enumerate(_rows(args.recourse_pairs))]
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in overlays), encoding="utf-8")
    print(json.dumps({"overlays": len(overlays), "output": str(target)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

