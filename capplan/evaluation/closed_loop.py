"""Closed-loop / strict-mock evaluation over saved dataset artifacts."""
from __future__ import annotations

import time
import random
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

from capplan.data.accessibility_layer import load_accessibility_graph
from capplan.data.capability_contracts import contract_episode_id
from capplan.data.schemas import AccessibilityGraph, contract_from_dict, pudo_from_dict, transition_from_dict, vehicle_from_dict, to_dict
from capplan.evaluation.metrics import compute_all_metrics
from capplan.planning.planner import CapPlanPlanner, PlannerConfig
from capplan.semantics.capability_compiler import CapabilityCompiler
from capplan.semantics.typed_resource_algebra import all_margins, satisfy_all
from capplan.utils.serialization import dump_json, read_jsonl, write_jsonl


def _cert_key(c: Dict[str, Any]) -> Tuple[str, str]:
    return c.get("episode_id"), c.get("passenger_id")


def result_to_episode_metrics(
    result, metadata: Dict[str, Any], contract,
    oracle_certificate: Dict[str, Any] | None = None,
    oracle_skeleton: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    skeleton = result.skeleton
    traj = result.diagnostics.get("trajectory", {})
    # Planner already computes capability satisfaction/margins for this exact
    # contract and skeleton. Reusing them avoids compiling/evaluating the same
    # contract a second time for every test request. Keep the old path as a
    # compatibility fallback for externally constructed PlannerResult objects.
    diag = result.diagnostics or {}
    margins = dict(diag.get("capability_margins") or {})
    capability_satisfied = bool(diag.get("capability_satisfied", False))
    failed = list(diag.get("failed_resources") or [])
    if skeleton and "capability_satisfied" not in diag:
        compiled = CapabilityCompiler().compile(contract, trip_context=metadata)
        capability_satisfied, margins, failed = satisfy_all(
            skeleton.final_ledger,
            [] if compiled.soft_only else compiled.clauses,
            [] if compiled.soft_only else compiled.groups,
        )
    cert = to_dict(result.certificate) if result.certificate else None
    phase_accepted = bool(skeleton and skeleton.accepted)
    traffic_safe = bool(not traj.get("collision", False) and traj.get("drivable_area", True) and traj.get("rule_compliance", not traj.get("rule_violation", False)))
    route_completion_value = float(traj.get("route_completion", traj.get("route_completion_baseline", 0.0)))
    passenger_complete = bool(phase_accepted and traffic_safe and capability_satisfied)
    # The frozen benchmark guarantees skeleton XOR certificate.  Store the
    # verifier outcome explicitly so every algorithm/ablation can be judged by
    # the same passenger-complete decision semantics.  This prevents a relaxed
    # ablation from looking better merely because it returns more unsafe plans.
    oracle_label_available = bool(oracle_skeleton) ^ bool(oracle_certificate)
    oracle_passenger_complete = bool(oracle_skeleton) if oracle_label_available else None
    route_length = float(metadata.get("route_length_m", 1.0))
    motion_budget = next((float(c.threshold) for c in contract.clauses if c.resource_name == "motion_exposure"), 1.0)
    motion_exposure = float((skeleton.final_ledger if skeleton else {}).get("motion_exposure", traj.get("motion_exposure", 0.0)) or 0.0)
    flf_resources = ["access_distance_m", "egress_distance_m", "slope", "cross_slope", "path_width_m", "curb_ramp", "step_free", "surface", "map_confidence"]
    baf_resources = ["ramp", "lift", "low_floor_kneeling", "door_width_m", "deployment_clearance_m", "door_side", "curb_height_m", "deployment_risk"]
    # For any_of boarding groups, a negative margin on one option is not a BAF
    # failure if another option passes.  Group margins use prefixed keys.
    flf = bool(margins) and all(m >= 0 for k, m in margins.items() if any(r in k for r in flf_resources))
    baf = bool(margins) and not any(m < 0 for k, m in margins.items() if any(r in k for r in ["door_width_m", "deployment_clearance_m", "door_side", "curb_height_m", "deployment_risk"]))
    if any("g_boarding_any_of" in k for k in margins):
        group_vals = [m for k, m in margins.items() if "g_boarding_any_of" in k]
        baf = baf and bool(group_vals) and max(group_vals) >= 0
    return {
        "episode_id": metadata.get("episode_id"),
        "passenger_id": contract.passenger_id,
        "collision": bool(traj.get("collision", False)),
        "drivable_area": bool(traj.get("drivable_area", True)),
        "traffic_safe": traffic_safe,
        "vehicle_metric_semantics": str(traj.get("vehicle_metric_semantics", "unknown")),
        "method_specific_closed_loop": bool(traj.get("method_specific_closed_loop", False)),
        "completed_route_m": route_length * route_completion_value,
        "planned_route_m": route_length,
        "route_completion": route_completion_value,
        "rule_violation": bool(traj.get("rule_violation", False)),
        "rule_violation_count": 1 if traj.get("rule_violation", False) else 0,
        "travel_time_s": float(traj.get("travel_time_s", metadata.get("route_length_m", 0.0) / 8.0)),
        "vehicle_distance_m": float(traj.get("distance_m", route_length * route_completion_value)),
        "shortest_route_m": float(metadata.get("shortest_route_length_m", route_length)),
        "passenger_complete": passenger_complete,
        "plan_returned": bool(skeleton),
        "selected_transitions": list(skeleton.transitions) if skeleton else [],
        "search_expansions": int(result.diagnostics.get("expansions", 0) or 0),
        "search_violations": int(result.diagnostics.get("violations", 0) or 0),
        "continuation_pruned": int(result.diagnostics.get("continuation_pruned", 0) or 0),
        "continuation_scored": int(result.diagnostics.get("continuation_scored", 0) or 0),
        "viability_pruned": int(result.diagnostics.get("viability_pruned", 0) or 0),
        "viability_structural_pruned": int(result.diagnostics.get("viability_structural_pruned", 0) or 0),
        "viability_typed_pruned": int(result.diagnostics.get("viability_typed_pruned", 0) or 0),
        "viability_path_checks": int(result.diagnostics.get("viability_path_checks", 0) or 0),
        "viability_cache_hits": int(result.diagnostics.get("viability_cache_hits", 0) or 0),
        "precondition_summary_checks": int(result.diagnostics.get("precondition_summary_checks", 0) or 0),
        "precondition_proof_checks": int(result.diagnostics.get("precondition_proof_checks", 0) or 0),
        "precondition_proof_envelope_hits": int(result.diagnostics.get("precondition_proof_envelope_hits", 0) or 0),
        "precondition_raw_suffixes": int(result.diagnostics.get("precondition_raw_suffixes", 0) or 0),
        "precondition_antichain_size": int(result.diagnostics.get("precondition_antichain_size", 0) or 0),
        "precondition_raw_proofs": int(result.diagnostics.get("precondition_raw_proofs", 0) or 0),
        "precondition_proof_antichain_size": int(result.diagnostics.get("precondition_proof_antichain_size", 0) or 0),
        "phase_accepted": phase_accepted,
        "vehicle_safe": traffic_safe,
        "capability_satisfied": capability_satisfied,
        "capability_margins": margins,
        "first_last_meter_feasible": flf,
        "boarding_alighting_feasible": baf,
        "motion_exposure": motion_exposure,
        "motion_budget": motion_budget,
        "motion_violation": bool(margins) and any(margins.get(r, 1.0) < 0 for r in ["motion_exposure", "peak_accel_mps2", "peak_jerk_mps3"]),
        "budget_residuals": margins,
        "inconclusive": (cert or {}).get("resource_type") in ["map_confidence", "dynamic_confidence", "blockage_risk", "availability_risk", "availability", "deployment_risk"]
            or str((cert or {}).get("reason") or "") in ["missing_evidence", "low_confidence", "inconclusive_low_confidence"],
        "certificate": cert,
        "oracle_certificate": oracle_certificate,
        "oracle_passenger_complete": oracle_passenger_complete,
        "oracle_label_available": oracle_label_available,
        "tt_cap_s": float(traj.get("travel_time_s", 0.0)),
        # ECA requires a measured/evaluated standard-planner baseline.  Do not
        # synthesize it from route_length/speed because that makes publication
        # ECA look measured when it is not.
        **({"tt_std_s": float(metadata.get("standard_travel_time_s"))} if metadata.get("standard_travel_time_s") is not None else {}),
        "failed_resources": failed,
        "failure_phase": (cert or oracle_certificate or {}).get("phase"),
        "failure_resource": (cert or oracle_certificate or {}).get("resource_type"),
        "failure_source": (cert or oracle_certificate or {}).get("evidence_source"),
        **{k: traj[k] for k in [
            "at_fault_collision_rate", "drivable_area_compliance", "ego_progress_along_expert_route",
            "time_to_collision_within_bound", "speed_limit_compliance", "driving_direction_compliance",
            "comfort", "nuplan_score"
        ] if k in traj},
    }


class ClosedLoopRunner:
    def __init__(self, planner_config: PlannerConfig | None = None, trajectory_mode: str | None = None, casa_mode: str | None = None) -> None:
        cfg = planner_config or PlannerConfig()
        if trajectory_mode is not None:
            cfg.trajectory_mode = trajectory_mode
        if casa_mode is not None:
            cfg.casa_mode = casa_mode
        self.planner = CapPlanPlanner(cfg)
        self.config = cfg

    def _load_dataset(self, dataset_dir: Path, *, episode_limit: int | None = None, episode_seed: int = 13) -> Dict[str, Any]:
        all_episodes = read_jsonl(dataset_dir / "episodes.jsonl")
        if episode_limit is not None and int(episode_limit) > 0 and int(episode_limit) < len(all_episodes):
            rng = random.Random(int(episode_seed))
            picked = set(rng.sample([str(e["episode_id"]) for e in all_episodes], int(episode_limit)))
            episodes = [e for e in all_episodes if str(e["episode_id"]) in picked]
        else:
            episodes = all_episodes
        selected = {str(e["episode_id"]) for e in episodes}
        scenes = {s["episode_id"]: s for s in read_jsonl(dataset_dir / "scenes.jsonl") if str(s.get("episode_id")) in selected}
        entrances = [e for e in read_jsonl(dataset_dir / "entrances.jsonl") if str(e.get("episode_id")) in selected]
        pudos_by_episode: Dict[str, List[Any]] = {}
        for d in read_jsonl(dataset_dir / "pudo_anchors.jsonl"):
            if str(d.get("episode_id")) not in selected:
                continue
            p = pudo_from_dict(d); pudos_by_episode.setdefault(p.episode_id, []).append(p)
        vehicles_by_episode: Dict[str, List[Any]] = {}
        for d in read_jsonl(dataset_dir / "vehicle_interfaces.jsonl"):
            if str(d.get("episode_id")) not in selected:
                continue
            v = vehicle_from_dict(d); vehicles_by_episode.setdefault(v.episode_id, []).append(v)
        contracts_by_episode: Dict[str, List[Any]] = {}
        for d in read_jsonl(dataset_dir / "capability_contracts.jsonl"):
            c = contract_from_dict(d); eid = contract_episode_id(c)
            if eid in selected:
                contracts_by_episode.setdefault(eid, []).append(c)
        transitions_by_episode: Dict[str, List[Any]] = {}
        for d in read_jsonl(dataset_dir / "candidate_transitions.jsonl"):
            if str(d.get("episode_id")) not in selected:
                continue
            t = transition_from_dict(d); transitions_by_episode.setdefault(t.episode_id, []).append(t)
        oracle_certs = {_cert_key(c): c for c in read_jsonl(dataset_dir / "certificate_labels.jsonl") if str(c.get("episode_id")) in selected}
        skeletons = {(x.get("episode_id"), x.get("passenger_id")): x for x in read_jsonl(dataset_dir / "skeleton_labels.jsonl") if str(x.get("episode_id")) in selected}
        counterfactual_pairs = [x for x in read_jsonl(dataset_dir / "counterfactual_pairs.jsonl") if str(x.get("episode_id")) in selected]
        service_requests = [x for x in read_jsonl(dataset_dir / "service_requests.jsonl") if str(x.get("episode_id")) in selected]
        requests_by_episode: Dict[str, List[Dict[str, Any]]] = {}
        for r in service_requests:
            requests_by_episode.setdefault(r.get("episode_id"), []).append(r)
        vehicle_metrics_path = dataset_dir / "nuplan_vehicle_metrics.jsonl"
        vehicle_metrics_by_episode = {}
        if vehicle_metrics_path.exists():
            for row in read_jsonl(vehicle_metrics_path):
                eid = str(row.get("episode_id") or row.get("scenario_id") or "")
                if eid in selected:
                    vehicle_metrics_by_episode[eid] = row
        return {"scenes": scenes, "episodes": episodes, "entrances": entrances, "pudos": pudos_by_episode, "vehicles": vehicles_by_episode, "contracts": contracts_by_episode, "transitions": transitions_by_episode, "oracle_certs": oracle_certs, "skeletons": skeletons, "counterfactual_pairs": counterfactual_pairs, "service_requests": requests_by_episode, "vehicle_metrics": vehicle_metrics_by_episode}

    @staticmethod
    def _graph_for_episode(dataset_dir: Path, episode_id: str, transitions: List[Any]) -> AccessibilityGraph:
        if transitions:
            return AccessibilityGraph(episode_id, [], [], {"evaluation_fast_path": "saved_transitions"})
        return load_accessibility_graph(dataset_dir, episode_id)

    def run_dataset(
        self,
        dataset_dir: str | Path,
        output_dir: str | Path,
        *,
        show_progress: bool = False,
        progress_update_interval: int = 25,
        progress_desc: str = "CapPlan eval",
        episode_limit: int | None = None,
        episode_seed: int = 13,
        preloaded_data: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        dataset_dir = Path(dataset_dir)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        dump_json(output_dir / "planner_config.json", asdict(self.config))
        data = preloaded_data if preloaded_data is not None else self._load_dataset(dataset_dir, episode_limit=episode_limit, episode_seed=episode_seed)
        metrics_rows: List[Dict[str, Any]] = []
        plans: List[Dict[str, Any]] = []
        result_lookup: Dict[Tuple[str, str], Dict[str, Any]] = {}
        from tqdm.auto import tqdm
        total_requests = sum(len(data["contracts"].get(str(meta.get("episode_id")), [])) for meta in data["episodes"])
        progress = tqdm(
            total=total_requests, desc=progress_desc, unit="request",
            dynamic_ncols=True, disable=not show_progress,
        )
        completed_requests = 0
        completed_pc = 0
        latency_sum = 0.0
        for meta in data["episodes"]:
            eid = meta["episode_id"]
            transitions = data["transitions"].get(eid, [])
            # Saved candidate transitions already contain all path/interface typed
            # evidence consumed by the current planner. Loading and JSON-parsing the
            # full accessibility graph here was pure overhead whenever transitions
            # were present (about 90% of V1 wall time). Only materialize the graph
            # when transitions must be generated on the fly.
            graph = self._graph_for_episode(dataset_dir, eid, transitions)
            pudo = data["pudos"].get(eid, [])
            vehicles = data["vehicles"].get(eid, [])
            if not vehicles:
                raise RuntimeError(f"dataset has no saved vehicle interface for {eid}")
            scene = data["scenes"].get(eid, {})
            requests = data.get("service_requests", {}).get(eid, [])
            request_by_profile = {str(r.get("passenger_profile_id")): r for r in requests}
            trip_context_base = {**meta, "route_corridor": scene.get("route_corridor", meta.get("metadata", {}).get("route_corridor", {})), **(meta.get("metadata") or {}), **(scene.get("metadata") or {})}
            for contract in data["contracts"].get(eid, []):
                profile_key = str(contract.passenger_id).split(":")[-1]
                request = request_by_profile.get(profile_key) or (requests[0] if requests else {})
                requested_vehicle_id = request.get("vehicle_id") or request.get("fleet_vehicle_id")
                vehicle = next((v for v in vehicles if requested_vehicle_id and v.vehicle_id == requested_vehicle_id), next((v for v in vehicles if v.vehicle_id == "wav_ramp_right"), vehicles[0]))
                trip_context = {**trip_context_base, "service_request": request, "request_time_s": request.get("request_time_s", trip_context_base.get("request_time_s")), "origin_entrance_id": request.get("origin_entrance_id", trip_context_base.get("origin_entrance_id")), "destination_entrance_id": request.get("destination_entrance_id", trip_context_base.get("destination_entrance_id"))}
                if eid in data.get("vehicle_metrics", {}):
                    trip_context["nuplan_vehicle_metrics"] = data["vehicle_metrics"][eid]
                plan_t0 = time.perf_counter()
                result = self.planner.plan(eid, contract, graph, pudo, vehicle, transitions=transitions, trip_context=trip_context)
                planning_latency_ms = (time.perf_counter() - plan_t0) * 1000.0
                oracle_cert = data["oracle_certs"].get((eid, contract.passenger_id))
                oracle_skeleton = data["skeletons"].get((eid, contract.passenger_id))
                row = result_to_episode_metrics(
                    result, trip_context, contract, oracle_cert, oracle_skeleton
                )
                row["planning_latency_ms"] = float(planning_latency_ms)
                metrics_rows.append(row)
                result_lookup[(eid, contract.passenger_id)] = row
                plans.append({"episode_id": eid, "passenger_id": contract.passenger_id, "success": result.success, "skeleton": to_dict(result.skeleton) if result.skeleton else None, "certificate": to_dict(result.certificate) if result.certificate else None})
                completed_requests += 1
                completed_pc += int(bool(row.get("passenger_complete")))
                latency_sum += float(planning_latency_ms)
                progress.update(1)
                if show_progress and (completed_requests == 1 or completed_requests % max(1, progress_update_interval) == 0 or completed_requests == total_requests):
                    progress.set_postfix({
                        "PCR": f"{completed_pc/max(completed_requests,1):.4f}",
                        "lat_ms": f"{latency_sum/max(completed_requests,1):.1f}",
                        "episode": str(eid)[-18:],
                    }, refresh=False)
        progress.close()
        pair_rows = self._evaluate_counterfactual_pairs(
            data["counterfactual_pairs"], result_lookup, data["skeletons"], data["oracle_certs"]
        )
        write_jsonl(output_dir / "episode_metrics.jsonl", metrics_rows)
        write_jsonl(output_dir / "plans.jsonl", plans)
        write_jsonl(output_dir / "counterfactual_metrics.jsonl", pair_rows)
        axis_summary: Dict[str, Dict[str, Any]] = {}
        for row in pair_rows:
            axis = str(row.get("counterfactual_axis") or row.get("axis") or "unknown")
            rec = axis_summary.setdefault(axis, {"count": 0, "response_correct": 0, "oracle_changed": 0, "model_changed": 0})
            rec["count"] += 1
            rec["response_correct"] += int(bool(row.get("response_correct")))
            rec["oracle_changed"] += int(bool(row.get("oracle_changed")))
            rec["model_changed"] += int(bool(row.get("model_changed")))
        for rec in axis_summary.values():
            n = max(int(rec["count"]), 1)
            rec["CRsp"] = float(rec["response_correct"]) / n
            rec["oracle_change_rate"] = float(rec["oracle_changed"]) / n
            rec["model_change_rate"] = float(rec["model_changed"]) / n
        dump_json(output_dir / "counterfactual_axis_summary.json", axis_summary)
        aggregate = compute_all_metrics(metrics_rows, pair_rows)
        dump_json(output_dir / "metrics.json", aggregate)
        vehicle_semantics = sorted({str(r.get("vehicle_metric_semantics") or "unknown") for r in metrics_rows})
        integrated_ready = bool(metrics_rows) and all(bool(r.get("method_specific_closed_loop")) for r in metrics_rows)
        attribution_warnings = []
        if aggregate.get("PCR", 0.0) <= 0.0:
            attribution_warnings.append("PCR is zero; component ablations cannot support positive passenger-completion attribution.")
        if aggregate.get("PlanReturnRate", 0.0) <= 0.0:
            attribution_warnings.append("TSBS returned no service skeletons; search-level ablations are bottleneck-confounded.")
        if aggregate.get("TSBS_expansions_p95", 0.0) <= 1.0:
            attribution_warnings.append("TSBS p95 expansions <= 1; most requests terminate at the initial frontier.")
        internal_margin_comparable = not any([
            bool(self.config.no_capability_compiler),
            bool(self.config.soft_only_capability),
            bool(self.config.no_typed_resource_ledger),
        ])
        eval_semantics = {
            "algorithm_version": str(self.config.algorithm_version),
            "evidence_grounded_runtime": bool(self.config.evidence_grounded_runtime),
            "hard_feasibility_evidence_policy": ("explicit_typed_evidence_v2plus" if self.config.evidence_grounded_runtime else "learned_overwrite_v1"),
            "frontier_guidance_policy": (
                "proof_carrying_weakest_precondition_antichain_v6"
                if str(self.config.algorithm_version).upper().startswith("V6") and not self.config.no_viability_kernel and not self.config.v2_reference_runtime and not self.config.v5_reference_runtime
                else (
                    "proof_carrying_capability_viability_v5"
                    if (str(self.config.algorithm_version).upper().startswith("V5") or (str(self.config.algorithm_version).upper().startswith("V6") and self.config.v5_reference_runtime)) and not self.config.no_viability_kernel and not self.config.v2_reference_runtime
                    else (
                    "capability_continuation_envelope_v4"
                    if str(self.config.algorithm_version).upper().startswith("V4") and not self.config.no_continuation_envelope and not self.config.v2_reference_runtime
                    else (
                        "executable_capability_frontier_v3"
                        if str(self.config.algorithm_version).upper().startswith("V3") and self.config.frontier_ranker_checkpoint and not self.config.no_frontier_ranker and not self.config.v2_reference_runtime
                        else ("v2_static_typed_feasibility" if self.config.v2_reference_runtime else "none_or_legacy")
                    )
                )
                )
            ),
            "frontier_ranker_checkpoint": str(self.config.frontier_ranker_checkpoint) if self.config.frontier_ranker_checkpoint else None,
            "continuation_envelope_enabled": bool(str(self.config.algorithm_version).upper().startswith("V4") and not self.config.no_continuation_envelope and not self.config.v2_reference_runtime),
            "continuation_pruning_enabled": bool(str(self.config.algorithm_version).upper().startswith("V4") and not self.config.no_continuation_envelope and not self.config.no_continuation_pruning and not self.config.v2_reference_runtime),
            "capability_viability_kernel_enabled": bool((str(self.config.algorithm_version).upper().startswith("V5") or str(self.config.algorithm_version).upper().startswith("V6")) and not self.config.no_viability_kernel and not self.config.v2_reference_runtime),
            "typed_viability_pruning_enabled": bool((str(self.config.algorithm_version).upper().startswith("V5") or str(self.config.algorithm_version).upper().startswith("V6")) and not self.config.no_viability_kernel and not self.config.no_typed_viability and not self.config.v2_reference_runtime),
            "precondition_antichain_enabled": bool(str(self.config.algorithm_version).upper().startswith("V6") and not self.config.no_viability_kernel and not self.config.no_precondition_antichain and not self.config.v5_reference_runtime and not self.config.v2_reference_runtime),
            "viability_proof_envelope_enabled": bool(str(self.config.algorithm_version).upper().startswith("V6") and not self.config.no_viability_kernel and not self.config.no_viability_proof_envelope and not self.config.v5_reference_runtime and not self.config.v2_reference_runtime),
            "proof_carrying_viability_certificates": bool(
                (
                    str(self.config.algorithm_version).upper().startswith("V5")
                    or (str(self.config.algorithm_version).upper().startswith("V6") and not self.config.no_viability_proof_envelope)
                )
                and not self.config.no_viability_kernel
                and not self.config.generic_viability_certificates
                and not self.config.v2_reference_runtime
            ),
            "vehicle_metric_semantics": vehicle_semantics,
            "publication_integrated_vehicle_closed_loop_ready": integrated_ready,
            "passenger_service_metrics_available": bool(metrics_rows),
            "oracle_referenced_passenger_completion_metrics_available": aggregate.get("PCDecisionEvaluableCount", 0.0) > 0.0,
            "planner_internal_margin_metrics_cross_ablation_comparable": internal_margin_comparable,
            "planner_internal_margin_metric_note": (
                "CVR/CSM/FLF/BAF are computed from the planner's active/internal capability margins. "
                "When compiler/typed-ledger semantics are ablated they are not cross-ablation verifier metrics; "
                "use OraclePCR and PCDecision* for common success-decision comparison."
            ),
            "algorithm_attribution_ready": not attribution_warnings,
            "algorithm_attribution_warnings": attribution_warnings,
            "note": (
                "Passenger/service metrics can be used for offline/service evaluation. Final vehicle closed-loop claims require "
                "method-specific nuPlan simulation of CapPlan-selected service decisions; post-hoc episode metrics and mock_strict are not sufficient."
            ),
        }
        dump_json(output_dir / "evaluation_semantics.json", eval_semantics)
        return {"episodes": metrics_rows, "metrics": aggregate, "plans": plans, "counterfactual_pairs": pair_rows, "evaluation_semantics": eval_semantics}

    @staticmethod
    def _evaluate_counterfactual_pairs(
        pairs: List[Dict[str, Any]],
        result_lookup: Dict[Tuple[str, str], Dict[str, Any]],
        oracle_skeletons: Dict[Tuple[str, str], Dict[str, Any]],
        oracle_certs: Dict[Tuple[str, str], Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Compare model counterfactual behavior with offline-verifier behavior.

        A pair is responsive only when the model changes (or preserves) its
        success/path/certificate in the same way as the verifier.  Merely having
        a smaller strict margin is not counted as a plan change.
        """
        def oracle_signature(eid: str, pid: str):
            sk = oracle_skeletons.get((eid, pid))
            if sk:
                return ("success", tuple(sk.get("transitions") or []))
            c = oracle_certs.get((eid, pid)) or {}
            return ("fail", c.get("phase"), c.get("transition_id"), c.get("resource_type"), c.get("reason"))

        def model_signature(row: Dict[str, Any] | None):
            if not row:
                return ("missing",)
            if row.get("passenger_complete"):
                return ("success", tuple(row.get("selected_transitions") or []))
            c = row.get("certificate") or {}
            return ("fail", c.get("phase"), c.get("transition_id"), c.get("resource_type"), c.get("reason"))

        rows = []
        for pair in pairs:
            eid = str(pair.get("episode_id") or "")
            weak_pid = str(pair.get("weak_passenger_id") or "")
            strict_pid = str(pair.get("strict_passenger_id") or "")
            weak = result_lookup.get((eid, weak_pid))
            strict = result_lookup.get((eid, strict_pid))
            ow = oracle_signature(eid, weak_pid)
            os = oracle_signature(eid, strict_pid)
            mw = model_signature(weak)
            ms = model_signature(strict)
            oracle_changed = ow != os
            model_changed = mw != ms
            oracle_weak_success = ow[0] == "success"
            oracle_strict_success = os[0] == "success"
            model_weak_success = bool(weak and weak.get("passenger_complete"))
            model_strict_success = bool(strict and strict.get("passenger_complete"))
            outcomes_match = (model_weak_success == oracle_weak_success and model_strict_success == oracle_strict_success)
            response_correct = bool(outcomes_match and model_changed == oracle_changed)
            rows.append({
                **pair,
                "oracle_changed": bool(oracle_changed),
                "model_changed": bool(model_changed),
                "oracle_weak_success": oracle_weak_success,
                "oracle_strict_success": oracle_strict_success,
                "model_weak_success": model_weak_success,
                "model_strict_success": model_strict_success,
                "outcomes_match_oracle": bool(outcomes_match),
                "response_correct": response_correct,
                "responsive": response_correct,
            })
        return rows

