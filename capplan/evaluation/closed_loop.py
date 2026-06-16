"""Closed-loop / strict-mock evaluation over saved dataset artifacts."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Tuple

from capplan.data.accessibility_layer import load_accessibility_graph
from capplan.data.schemas import contract_from_dict, pudo_from_dict, transition_from_dict, vehicle_from_dict, to_dict
from capplan.evaluation.metrics import compute_all_metrics
from capplan.planning.planner import CapPlanPlanner, PlannerConfig
from capplan.semantics.capability_compiler import CapabilityCompiler
from capplan.semantics.typed_resource_algebra import all_margins, satisfy_all
from capplan.utils.serialization import dump_json, read_jsonl, write_jsonl


def _contract_episode_id(passenger_id: str) -> str:
    return passenger_id.split(":p")[0]


def _cert_key(c: Dict[str, Any]) -> Tuple[str, str]:
    return c.get("episode_id"), c.get("passenger_id")


def result_to_episode_metrics(result, metadata: Dict[str, Any], contract, oracle_certificate: Dict[str, Any] | None = None) -> Dict[str, Any]:
    skeleton = result.skeleton
    traj = result.diagnostics.get("trajectory", {})
    compiled = CapabilityCompiler().compile(contract, trip_context=metadata)
    margins = {}
    capability_satisfied = False
    failed = []
    if skeleton:
        capability_satisfied, margins, failed = satisfy_all(skeleton.final_ledger, [] if compiled.soft_only else compiled.clauses, [] if compiled.soft_only else compiled.groups)
    cert = to_dict(result.certificate) if result.certificate else None
    phase_accepted = bool(skeleton and skeleton.accepted)
    traffic_safe = bool(not traj.get("collision", False) and traj.get("drivable_area", True) and traj.get("rule_compliance", not traj.get("rule_violation", False)))
    route_completion_value = float(traj.get("route_completion", traj.get("route_completion_baseline", 0.0)))
    passenger_complete = bool(phase_accepted and traffic_safe and capability_satisfied)
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
        "completed_route_m": route_length * route_completion_value,
        "planned_route_m": route_length,
        "route_completion": route_completion_value,
        "rule_violation": bool(traj.get("rule_violation", False)),
        "rule_violation_count": 1 if traj.get("rule_violation", False) else 0,
        "travel_time_s": float(traj.get("travel_time_s", metadata.get("route_length_m", 0.0) / 8.0)),
        "vehicle_distance_m": float(traj.get("distance_m", route_length * route_completion_value)),
        "shortest_route_m": float(metadata.get("shortest_route_length_m", route_length)),
        "passenger_complete": passenger_complete,
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
        "inconclusive": (cert or {}).get("resource_type") in ["map_confidence", "dynamic_confidence", "blockage_risk", "availability_risk"],
        "certificate": cert,
        "oracle_certificate": oracle_certificate,
        "tt_cap_s": float(traj.get("travel_time_s", 0.0)),
        "tt_std_s": max(1.0, float(metadata.get("route_length_m", 4000.0)) / 10.0),
        "failed_resources": failed,
        "failure_phase": (cert or oracle_certificate or {}).get("phase"),
        "failure_resource": (cert or oracle_certificate or {}).get("resource_type"),
        "failure_source": (cert or oracle_certificate or {}).get("evidence_source"),
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

    def _load_dataset(self, dataset_dir: Path) -> Dict[str, Any]:
        scenes = {s["episode_id"]: s for s in read_jsonl(dataset_dir / "scenes.jsonl")}
        episodes = read_jsonl(dataset_dir / "episodes.jsonl")
        entrances = read_jsonl(dataset_dir / "entrances.jsonl")
        pudos_by_episode: Dict[str, List[Any]] = {}
        for d in read_jsonl(dataset_dir / "pudo_anchors.jsonl"):
            p = pudo_from_dict(d)
            pudos_by_episode.setdefault(p.episode_id, []).append(p)
        vehicles_by_episode: Dict[str, List[Any]] = {}
        for d in read_jsonl(dataset_dir / "vehicle_interfaces.jsonl"):
            v = vehicle_from_dict(d)
            vehicles_by_episode.setdefault(v.episode_id, []).append(v)
        contracts_by_episode: Dict[str, List[Any]] = {}
        for d in read_jsonl(dataset_dir / "capability_contracts.jsonl"):
            c = contract_from_dict(d)
            contracts_by_episode.setdefault(_contract_episode_id(c.passenger_id), []).append(c)
        transitions_by_episode: Dict[str, List[Any]] = {}
        for d in read_jsonl(dataset_dir / "candidate_transitions.jsonl"):
            t = transition_from_dict(d)
            transitions_by_episode.setdefault(t.episode_id, []).append(t)
        oracle_certs = {_cert_key(c): c for c in read_jsonl(dataset_dir / "certificate_labels.jsonl")}
        skeletons = {(s.get("episode_id"), s.get("passenger_id")): s for s in read_jsonl(dataset_dir / "skeleton_labels.jsonl")}
        counterfactual_pairs = read_jsonl(dataset_dir / "counterfactual_pairs.jsonl")
        service_requests = read_jsonl(dataset_dir / "service_requests.jsonl")
        requests_by_episode = {}
        for r in service_requests:
            requests_by_episode.setdefault(r.get("episode_id"), []).append(r)
        return {"scenes": scenes, "episodes": episodes, "entrances": entrances, "pudos": pudos_by_episode, "vehicles": vehicles_by_episode, "contracts": contracts_by_episode, "transitions": transitions_by_episode, "oracle_certs": oracle_certs, "skeletons": skeletons, "counterfactual_pairs": counterfactual_pairs, "service_requests": requests_by_episode}

    def run_dataset(self, dataset_dir: str | Path, output_dir: str | Path) -> Dict[str, Any]:
        dataset_dir = Path(dataset_dir)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        data = self._load_dataset(dataset_dir)
        metrics_rows: List[Dict[str, Any]] = []
        plans: List[Dict[str, Any]] = []
        result_lookup: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for meta in data["episodes"]:
            eid = meta["episode_id"]
            graph = load_accessibility_graph(dataset_dir, eid)
            pudo = data["pudos"].get(eid, [])
            vehicles = data["vehicles"].get(eid, [])
            if not vehicles:
                raise RuntimeError(f"dataset has no saved vehicle interface for {eid}")
            transitions = data["transitions"].get(eid, [])
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
                result = self.planner.plan(eid, contract, graph, pudo, vehicle, transitions=transitions, trip_context=trip_context)
                oracle_cert = data["oracle_certs"].get((eid, contract.passenger_id))
                row = result_to_episode_metrics(result, trip_context, contract, oracle_cert)
                metrics_rows.append(row)
                result_lookup[(eid, contract.passenger_id)] = row
                plans.append({"episode_id": eid, "passenger_id": contract.passenger_id, "success": result.success, "skeleton": to_dict(result.skeleton) if result.skeleton else None, "certificate": to_dict(result.certificate) if result.certificate else None})
        pair_rows = self._evaluate_counterfactual_pairs(data["counterfactual_pairs"], result_lookup)
        write_jsonl(output_dir / "episode_metrics.jsonl", metrics_rows)
        write_jsonl(output_dir / "plans.jsonl", plans)
        write_jsonl(output_dir / "counterfactual_metrics.jsonl", pair_rows)
        aggregate = compute_all_metrics(metrics_rows, pair_rows)
        dump_json(output_dir / "metrics.json", aggregate)
        return {"episodes": metrics_rows, "metrics": aggregate, "plans": plans, "counterfactual_pairs": pair_rows}

    @staticmethod
    def _evaluate_counterfactual_pairs(pairs: List[Dict[str, Any]], result_lookup: Dict[Tuple[str, str], Dict[str, Any]]) -> List[Dict[str, Any]]:
        rows = []
        for pair in pairs:
            eid = pair.get("episode_id")
            weak = result_lookup.get((eid, pair.get("weak_passenger_id")))
            strict = result_lookup.get((eid, pair.get("strict_passenger_id")))
            responsive = False
            if weak and strict:
                if weak.get("passenger_complete") and not strict.get("passenger_complete"):
                    responsive = True
                elif weak.get("passenger_complete") and strict.get("passenger_complete"):
                    wm = min((weak.get("capability_margins") or {"m": 0}).values())
                    sm = min((strict.get("capability_margins") or {"m": 0}).values())
                    responsive = sm <= wm + 1e-9
                elif not weak.get("passenger_complete") and not strict.get("passenger_complete"):
                    wc = weak.get("oracle_certificate") or weak.get("certificate") or {}
                    sc = strict.get("oracle_certificate") or strict.get("certificate") or {}
                    same_failure = all(sc.get(k) == wc.get(k) for k in ["phase", "transition_id", "resource_type", "reason"])
                    margin_drop = float(sc.get("signed_margin", 0.0)) < float(wc.get("signed_margin", 0.0)) - 1e-9
                    certificate_changed = any(sc.get(k) != wc.get(k) for k in ["phase", "transition_id", "resource_type", "reason"])
                    # Identical failure certificates are not capability-responsive;
                    # they usually mean the scene/evidence is already impossible
                    # before the stricter contract matters.
                    responsive = (margin_drop or certificate_changed) and not same_failure
                else:
                    responsive = pair.get("relation") != "stricter_or_equal"
            rows.append({**pair, "responsive": bool(responsive)})
        return rows
