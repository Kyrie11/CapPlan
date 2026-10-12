#!/usr/bin/env python3
"""Mine A/B pickup pairs where direct choices fit but A->B recovery does not.

The screen uses existing pedestrian geometry and preserves its provenance.  It
does not infer vehicle reachability, observed blockage prevalence, or global
strandedness.  An optional motion-evidence file can upgrade a distance-only
candidate to a two-sided witness for a declared validity interval.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import heapq
import json
import math
from pathlib import Path
import random
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from capplan.data.accessibility_layer import load_accessibility_graph


def _records(path: Path):
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def _valid_edge(edge, args) -> bool:
    if edge.obstacle or str(edge.obstacle_state).lower() == "blocked":
        return False
    if not math.isfinite(edge.length_m) or edge.length_m < 0:
        return False
    if args.require_step_free and edge.step_free is not True:
        return False
    if edge.width_m is None or not math.isfinite(edge.width_m) or edge.width_m < args.min_width_m:
        return False
    if edge.slope is None or not math.isfinite(edge.slope) or abs(edge.slope) > args.max_slope:
        return False
    if edge.cross_slope is None or not math.isfinite(edge.cross_slope) or abs(edge.cross_slope) > args.max_cross_slope:
        return False
    return math.isfinite(edge.confidence) and edge.confidence >= args.min_confidence


def _shortest_tree(adjacency, start):
    distance, previous = {start: 0.0}, {}
    heap = [(0.0, start)]
    while heap:
        cost, node = heapq.heappop(heap)
        if cost > distance[node] + 1e-9:
            continue
        for other, edge in adjacency.get(node, []):
            candidate = cost + float(edge.length_m)
            if candidate < distance.get(other, float("inf")) - 1e-9:
                distance[other] = candidate
                previous[other] = (node, edge)
                heapq.heappush(heap, (candidate, other))
    return distance, previous


def _path(previous, start, target):
    nodes, edges = [target], []
    while target != start:
        prior, edge = previous[target]
        edges.append({
            "edge_id": edge.edge_id,
            "length_m": edge.length_m,
            "source": edge.source,
            "field_provenance": edge.metadata,
        })
        target = prior
        nodes.append(target)
    return {"node_ids": nodes[::-1], "edges": edges[::-1]}


def _motion_packet_valid(packet: dict) -> bool:
    required = ("reachable", "collision_free", "legal_stop", "interface_verified", "method_specific_motion_checked")
    return (
        all(packet.get(key) is True for key in required)
        and bool(packet.get("source"))
        and float(packet["valid_end_s"]) > float(packet["valid_start_s"])
    )


def mine_episode(episode, anchors, graph, args, motion_packets):
    counts = Counter()
    adjacency = defaultdict(list)
    node_ids = {node.node_id for node in graph.nodes}
    for edge in graph.edges:
        if _valid_edge(edge, args):
            adjacency[edge.from_node].append((edge.to_node, edge))
            adjacency[edge.to_node].append((edge.from_node, edge))
        else:
            counts["filtered_edges"] += 1
    origin = episode["origin_anchor"]
    if origin not in node_ids:
        counts["missing_origin"] += 1
        return [], counts
    from_origin, prev_origin = _shortest_tree(adjacency, origin)
    candidates = []
    node_by_id = {node.node_id:node for node in graph.nodes}
    def ped_node(anchor):
        return anchor.get('adjacent_ped_node_id') or anchor['anchor_id']
    def explicit_curb(ped):
        return any((edge.from_node==ped or edge.to_node==ped)
                   and str(edge.crossing_type).lower()=='curb'
                   and _valid_edge(edge,args) for edge in graph.edges)
    counts["anchors_examined"] += len(anchors)
    for anchor in anchors:
        anchor_id = anchor["anchor_id"]
        if anchor.get("kind") not in {"pickup", "pickup_dropoff"} or anchor.get("legal_stop") is not True:
            continue
        counts["anchors_legally_allowed"] += 1
        # A legal stop does not establish usable interface clearance. Mark
        # evidence-incomplete anchors as unknown; optionally screen them out.
        interface_known = (anchor.get("deployment_clearance_m") is not None and
                           anchor.get("side") in {"left", "right", "both"} and
                           anchor.get("sidewalk_width_m") is not None)
        if interface_known:
            counts["anchors_interface_fields_present"] += 1
        if getattr(args, "require_interface_complete", False) and not interface_known:
            counts["anchors_missing_interface_fields"] += 1
            continue
        # Require that the curb connector itself is traversable, rather than
        # merely nearest-neighbor snapping from an unconnected road point.
        node_id=ped_node(anchor)
        if node_id in node_ids and node_id in from_origin:
            curb_verified=explicit_curb(node_id)
            counts['explicit_curb_connector_candidates'] += int(curb_verified)
            if getattr(args,'require_explicit_curb_connector',False) and not curb_verified:
                counts['missing_explicit_curb_connector']+=1
                continue
            candidate=dict(anchor)
            candidate['_ped_graph_node']=node_id
            candidate['_explicit_curb_connector']=curb_verified
            candidates.append(candidate)
            counts["legal_geometry_anchored_candidates"] += 1
        else:
            counts["missing_full_curb_connector"] += 1

    rows = []
    budget, consumed = args.distance_budget_m, args.already_consumed_m
    for a in candidates:
        aid = a["anchor_id"]
        anode=a['_ped_graph_node']
        da, prev_a = _shortest_tree(adjacency, anode)
        for b in candidates:
            bid = b["anchor_id"]
            bnode=b['_ped_graph_node']
            if aid == bid or anode == bnode or bnode not in da:
                continue
            counts["ordered_pair_denominator"] += 1
            key_a = a.get("hybrid_physical_site_key")
            key_b = b.get("hybrid_physical_site_key")
            if key_a and key_b and key_a == key_b:
                counts["same_physical_site_pairs_rejected"] += 1
                continue
            d_oa, d_ob, d_ab = from_origin[anode], from_origin[bnode], da[bnode]
            if 0 < d_oa < d_ob and consumed + d_ob <= budget:
                counts["ordered_both_direct_budget_feasible"] += 1
            if not (0 < d_oa < d_ob and consumed + d_ob <= budget and consumed + d_oa + d_ab > budget + 1e-9):
                continue
            counts["two_anchor_distance_asymmetry"] += 1
            packet = motion_packets.get((episode["episode_id"], aid, bid))
            motion_checked = bool(packet and _motion_packet_valid(packet))
            # A window containing a single timestamp is insufficient to
            # validate an executed motion. Require a declared execution
            # duration and verify the complete interval is covered.
            duration = packet.get("motion_duration_s") if packet else None
            interval_valid = bool(
                motion_checked and duration is not None
                and math.isfinite(float(duration)) and float(duration) >= 0
                and float(packet["valid_start_s"]) <= args.event_time_s
                and args.event_time_s + float(duration) <= float(packet["valid_end_s"])
            )
            counts["motion_packet_present"] += int(packet is not None)
            counts["motion_packet_semantically_passed"] += int(motion_checked)
            counts["motion_time_window_verified"] += int(interval_valid)
            rows.append({
                "episode_id": episode["episode_id"],
                "split": episode.get("split"),
                "map_name": episode.get("map_name"),
                "scenario_token": episode.get("scenario_token"),
                "origin_anchor": origin,
                "a_anchor": aid,
                "b_anchor": bid,
                "pedestrian_graph_nodes": {"origin": origin,"A":anode,"B":bnode},
                "explicit_curb_connector_evidence": {
                    "A":bool(a['_explicit_curb_connector']),
                    "B":bool(b['_explicit_curb_connector'])},
                "physical_site_pair": [key_a, key_b] if key_a and key_b else None,
                "anchor_vehicle_poses": {
                    "A_stop_pose": a.get("stop_pose"),
                    "B_stop_pose": b.get("stop_pose"),
                    "A_curb_pose": a.get("curb_pose"),
                    "B_curb_pose": b.get("curb_pose"),
                },
                "pudo_interface_a": {
                    "side": a.get("side"),
                    "deployment_clearance_m": a.get("deployment_clearance_m"),
                    "sidewalk_width_m": a.get("sidewalk_width_m"),
                    "evidence_kind": a.get("evidence_kind"),
                    "truth_mode": a.get("truth_mode"),
                    "field_provenance": a.get("field_provenance"),
                },
                "pudo_interface_b": {
                    "side": b.get("side"),
                    "deployment_clearance_m": b.get("deployment_clearance_m"),
                    "sidewalk_width_m": b.get("sidewalk_width_m"),
                    "evidence_kind": b.get("evidence_kind"),
                    "truth_mode": b.get("truth_mode"),
                    "field_provenance": b.get("field_provenance"),
                },
                "screening_contract": {
                    "distance_budget_m": budget,
                    "already_consumed_m": consumed,
                    "min_width_m": args.min_width_m,
                    "max_slope": args.max_slope,
                    "max_cross_slope": args.max_cross_slope,
                    "require_step_free": args.require_step_free,
                    "min_confidence": args.min_confidence,
                },
                "distances_m": {"origin_to_a": d_oa, "origin_to_b": d_ob, "a_to_b": d_ab},
                "reroute_total_m": consumed + d_oa + d_ab,
                "distance_excess_m": consumed + d_oa + d_ab - budget,
                "paths": {
                    "origin_to_a": _path(prev_origin, origin, anode),
                    "origin_to_b": _path(prev_origin, origin, bnode),
                    "a_to_b": _path(prev_a, anode, bnode),
                },
                "a_to_b_vehicle_motion_packet": packet,
                "vehicle_reroute_checked": interval_valid,
                "motion_packet_semantically_valid": motion_checked,
                "interface_completeness_claim": bool(
                    a.get("deployment_clearance_m") is not None and
                    b.get("deployment_clearance_m") is not None and
                    a.get("side") in {"left", "right", "both"} and
                    b.get("side") in {"left", "right", "both"}),
                "two_candidate_distance_and_vehicle_witness": interval_valid,
                "global_no_recovery_claim": False,
                "evidence_warnings": (
                    [] if a['_explicit_curb_connector'] and b['_explicit_curb_connector']
                    else ['curb connector not independently established by typed graph edges']),
                "event": {
                    "kind": "a_unusable_after_passenger_reaches_a",
                    "event_time_s": args.event_time_s,
                    "evidence_class": "explicit_counterfactual_not_observed_event",
                },
                "evidence_class": "existing_geometry_with_original_provenance_plus_controlled_event",
            })
            counts["distance_recourse_candidates"] += 1
            counts["full_two_sided_witnesses"] += int(interval_valid)
    return rows, counts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset_dir", required=True)
    parser.add_argument("--output_dir", required=True)
    parser.add_argument("--distance_budget_m", type=float, default=200.0)
    parser.add_argument("--already_consumed_m", type=float, default=0.0)
    parser.add_argument("--min_width_m", type=float, default=0.9)
    parser.add_argument("--max_slope", type=float, default=0.08)
    parser.add_argument("--max_cross_slope", type=float, default=0.04)
    parser.add_argument("--min_confidence", type=float, default=0.0)
    parser.add_argument("--require_step_free", action="store_true")
    parser.add_argument("--episode_limit", type=int, default=100)
    parser.add_argument("--seed", type=int, default=13)
    parser.add_argument("--event_time_s", type=float, default=90.0)
    parser.add_argument("--vehicle_reachability_jsonl")
    parser.add_argument("--require_explicit_curb_connector", action="store_true",
                        help="require traversable typed curb edge incident to BOTH pedestrian PUDO nodes")
    parser.add_argument("--require_interface_complete", action="store_true",
                        help="exclude anchors with unknown curb width/clearance/door side fields")
    args = parser.parse_args()
    if args.distance_budget_m <= 0 or args.already_consumed_m < 0 or args.episode_limit <= 0:
        parser.error("distance budget and episode limit must be positive; consumption must be nonnegative")

    root = Path(args.dataset_dir)
    episodes = list(_records(root / "episodes.jsonl"))
    random.Random(args.seed).shuffle(episodes)
    episodes = episodes[: args.episode_limit]
    selected = {row["episode_id"] for row in episodes}
    anchors = defaultdict(list)
    for row in _records(root / "pudo_anchors.jsonl"):
        if row["episode_id"] in selected:
            anchors[row["episode_id"]].append(row)
    packets = {}
    if args.vehicle_reachability_jsonl:
        for row in _records(Path(args.vehicle_reachability_jsonl)):
            key = (row["episode_id"], row["from_anchor"], row["to_anchor"])
            if key in packets:
                raise ValueError(f"duplicate motion packet: {key}")
            # Never silently promote a generic geometric travel estimate to
            # method-specific motion evidence.
            if "motion_duration_s" not in row:
                raise ValueError(f"motion packet lacks motion_duration_s: {key}")
            packets[key] = row

    output = Path(args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    all_rows, totals, errors = [], Counter(), []
    for episode in episodes:
        try:
            graph = load_accessibility_graph(root, episode["episode_id"])
            rows, counts = mine_episode(episode, anchors[episode["episode_id"]], graph, args, packets)
            all_rows.extend(rows)
            totals.update(counts)
            totals["episodes_read"] += 1
            totals["episodes_with_candidates"] += int(bool(rows))
        except (FileNotFoundError, KeyError, ValueError, TypeError) as exc:
            errors.append({"episode_id": episode["episode_id"], "error": str(exc)})
    (output / "recourse_pairs.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in all_rows), encoding="utf-8"
    )
    audit = {
        "selected_episodes": len(episodes),
        "counts": dict(totals),
        "errors": errors,
        "parameters": vars(args),
        "interpretation": "Motivation candidate screen; not an observed prevalence or global strandedness estimate.",
    }
    (output / "mining_audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"rows": len(all_rows), "errors": len(errors), "output_dir": str(output)}, ensure_ascii=False))
    return 2 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())

