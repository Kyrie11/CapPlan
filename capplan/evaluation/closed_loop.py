"""Closed-loop and deterministic mock evaluation."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List

from capplan.data.schemas import contract_from_dict, transition_from_dict
from capplan.data.accessibility_layer import synthetic_accessibility_graph
from capplan.data.pudo_interface_layer import synthetic_pudo_anchors, synthetic_vehicle_interface
from capplan.evaluation.metrics import compute_all_metrics
from capplan.planning.planner import CapPlanPlanner, PlannerConfig
from capplan.semantics.capability_compiler import CapabilityCompiler
from capplan.semantics.typed_resource_algebra import all_margins
from capplan.utils.serialization import dump_json, read_jsonl, write_jsonl


def result_to_episode_metrics(result, metadata: Dict[str, Any], contract) -> Dict[str, Any]:
    skeleton = result.skeleton
    traj = result.diagnostics.get("trajectory", {})
    margins = {}
    if skeleton:
        try:
            compiled = CapabilityCompiler().compile(contract)
            margins = all_margins(skeleton.final_ledger, compiled.clauses)
        except Exception:
            margins = {}
    cert = asdict(result.certificate) if result.certificate else None
    pc = bool(result.success)
    route_completion = 1.0 if traj.get("available") else 0.0
    motion_budget = next((float(c.threshold) for c in contract.clauses if c.resource_name == "motion_exposure"), 1.0)
    motion_exposure = float((skeleton.final_ledger if skeleton else {}).get("motion_exposure", 0.0))
    return {
        "episode_id": metadata.get("episode_id"),
        "passenger_id": contract.passenger_id,
        "collision": bool(traj.get("collision", False)),
        "completed_route_m": metadata.get("route_length_m", 0.0) * route_completion,
        "planned_route_m": metadata.get("route_length_m", 1.0),
        "route_completion": route_completion,
        "rule_violation": bool(traj.get("rule_violation", False)),
        "rule_violation_count": 1 if traj.get("rule_violation", False) else 0,
        "travel_time_s": float(traj.get("travel_time_s", metadata.get("route_length_m", 0.0) / 8.0)),
        "vehicle_distance_m": float(traj.get("distance_m", metadata.get("route_length_m", 0.0))),
        "shortest_route_m": float(metadata.get("shortest_route_length_m", metadata.get("route_length_m", 1.0))),
        "passenger_complete": pc,
        "phase_accepted": pc,
        "capability_margins": margins,
        "first_last_meter_feasible": pc and all(margins.get(r, 1.0) >= 0 for r in ["access_distance_m", "egress_distance_m", "slope", "path_width_m", "curb_ramp"]),
        "boarding_alighting_feasible": pc and all(margins.get(r, 1.0) >= 0 for r in ["ramp", "door_side_clearance_m", "door_side"]),
        "motion_exposure": motion_exposure,
        "motion_budget": motion_budget,
        "motion_violation": bool(margins) and any(margins.get(r, 1.0) < 0 for r in ["motion_exposure", "peak_accel_mps2", "peak_jerk_mps3"]),
        "budget_residuals": margins,
        "inconclusive": (cert or {}).get("resource_type") in ["map_confidence", "blockage_risk", "availability_risk"],
        "certificate": cert,
        "oracle_certificate": cert,
        "tt_cap_s": float(traj.get("travel_time_s", 0.0)),
        "tt_std_s": max(1.0, float(metadata.get("route_length_m", 4000.0)) / 10.0),
    }


class ClosedLoopRunner:
    def __init__(self, planner_config: PlannerConfig | None = None) -> None:
        self.planner = CapPlanPlanner(planner_config or PlannerConfig())

    def run_dataset(self, dataset_dir: str | Path, output_dir: str | Path) -> Dict[str, Any]:
        dataset_dir = Path(dataset_dir)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        episodes_meta = read_jsonl(dataset_dir / "episodes.jsonl")
        contracts = [contract_from_dict(d) for d in read_jsonl(dataset_dir / "capability_contracts.jsonl")]
        contracts_by_episode: Dict[str, List[Any]] = {}
        for c in contracts:
            eid = c.passenger_id.split(":p")[0]
            contracts_by_episode.setdefault(eid, []).append(c)
        transitions_by_episode: Dict[str, List[Any]] = {}
        for d in read_jsonl(dataset_dir / "candidate_transitions.jsonl"):
            t = transition_from_dict(d)
            transitions_by_episode.setdefault(t.episode_id, []).append(t)
        metrics_rows: List[Dict[str, Any]] = []
        plans = []
        for meta in episodes_meta:
            eid = meta["episode_id"]
            graph = synthetic_accessibility_graph(eid, seed=int(meta.get("seed", 0)))
            pudo = synthetic_pudo_anchors(eid, seed=int(meta.get("seed", 0)))
            vehicle = synthetic_vehicle_interface(eid)
            for contract in contracts_by_episode.get(eid, []):
                result = self.planner.plan(eid, contract, graph, pudo, vehicle, transitions=transitions_by_episode.get(eid), trip_context=meta)
                metrics_rows.append(result_to_episode_metrics(result, meta, contract))
                plans.append({"episode_id": eid, "passenger_id": contract.passenger_id, "success": result.success, "skeleton": asdict(result.skeleton) if result.skeleton else None, "certificate": asdict(result.certificate) if result.certificate else None})
        write_jsonl(output_dir / "episode_metrics.jsonl", metrics_rows)
        write_jsonl(output_dir / "plans.jsonl", plans)
        aggregate = compute_all_metrics(metrics_rows)
        dump_json(output_dir / "metrics.json", aggregate)
        return {"episodes": metrics_rows, "metrics": aggregate, "plans": plans}
