"""Stable, version-independent, *read-only* CapPlan handoff research utilities.

Evidence levels are strictly separated:
  ENCODED_GRAPH: routing on serialized GIS/nuPlan accessibility arcs;
  CARLA_MOTION_SURROGATE: controlled simulator stop with no boarding/legal proof;
  DECLARED_FINITE_MODEL: mathematical commitment calculations;
  AUDITED_REAL_HANDOFF: requires independent site, vehicle and human attestations.

No method in this module upgrades absent evidence to verified real handoffs.
The existing dated scripts are retained only for reproducible historical replay.
"""
from __future__ import annotations

from collections import defaultdict, Counter
from pathlib import Path
from types import SimpleNamespace
from typing import Iterable
import hashlib
import heapq
import json
import math


def _read_jsonl(path: Path):
    with path.open(encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)


def _atomic_json(path: Path, content: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(content, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _digest_file(path: Path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def classify_carla_triplet(root: Path):
    """Audit target identity, actual redirect, matched controls and stop proxy.

    In particular, a run configured as 'redirect_B' is NOT successful merely
    because it finished a scripted dwell near A before the redirect deadline.
    These are CARLA trajectory diagnostics, never real curb/boarding claims.
    """
    from capplan.handoff.carla_calibration_v12 import audit, _load_runs

    root = Path(root)
    mode_paths = {"clear_A": root / "clear_A", "blocked_A": root / "blocked_A", "redirect_B": root / "redirect_B"}
    raw = {mode: {p.parent.name: json.loads(p.read_text(encoding="utf-8"))
                  for p in mode_root.glob("*/execution_report.json")}
           for mode, mode_root in mode_paths.items()}
    validated = {mode: {v["trial"]: v for v in _load_runs(path)}
                 for mode, path in mode_paths.items()}
    all_trials = sorted(set().union(*(set(v) for v in raw.values())))
    rows = []
    counts = Counter()
    for trial in all_trials:
        reports = {m: raw[m].get(trial) for m in mode_paths}
        states = {m: validated[m].get(trial) for m in mode_paths}
        if any(reports[m] is None or states[m] is None for m in mode_paths):
            rows.append({"trial": trial, "status": "UNKNOWN_INCOMPLETE_TRIPLET"})
            counts["incomplete_triplets"] += 1
            continue
        clear, blocked, redirect = (reports[m] for m in mode_paths)
        cstate, bstate, rstate = (states[m] for m in mode_paths)
        # The simulator's own `paired_controlled_comparability` is NOT a
        # cross-run verification. Check all logged setting fields excluding
        # the intended intervention, and verify identical initial coordinates.
        signatures = [states[m].get("experiment_config_signature") for m in mode_paths]
        configurations_match = all(v is not None and v == signatures[0] for v in signatures)
        loc = [reports[m].get("carla_locations", {}) for m in mode_paths]
        positions_match = all(x == loc[0] for x in loc)
        target_trace = root / "redirect_B" / trial / "trajectory.jsonl"
        has_target_B = False
        try:
            has_target_B = any(row.get("target") == "B" for row in _read_jsonl(target_trace))
        except (OSError, ValueError, TypeError):
            pass
        # Backward compatible: old runs omitted an explicit redirect flag.
        recorded_flag = redirect.get("redirect_command_applied")
        redirected = (recorded_flag is True if recorded_flag is not None else has_target_B)
        redirected = bool(redirected and has_target_B)
        stop_A = bool(cstate.get("valid_clear"))
        blocked_stop_A = bool(bstate.get("valid_clear") and blocked.get("goal") == "A")
        stop_B = bool(rstate.get("valid_clear") and redirected and redirect.get("goal") == "B")
        sensor_events = int(blocked.get("collisions") or 0)
        counts["triplets"] += 1
        counts["clear_A_stop_proxy"] += int(stop_A)
        counts["blocked_A_stop_proxy"] += int(blocked_stop_A)
        counts["redirect_command_executed"] += int(redirected)
        counts["redirect_B_stop_proxy"] += int(stop_B)
        counts["blocked_sensor_event_runs"] += int(sensor_events > 0)
        aligned = bool(configurations_match and positions_match and stop_A)
        counts["matched_control_configuration"] += int(aligned)
        if aligned and not blocked_stop_A:
            counts["matched_blocked_A_stop_lost"] += 1
        row = {
            "trial": trial,
            "status": "MATCHED_SIMULATOR_CONFIGURATION_ONLY" if aligned else "UNMATCHED_OR_UNCALIBRATED",
            "clear_A_stop_proxy": stop_A,
            "blocked_A_stop_proxy": blocked_stop_A,
            "redirect_command_executed": redirected,
            "redirect_target_logged": redirect.get("goal"),
            "redirect_B_stop_proxy": stop_B,
            "blocked_collision_sensor_callbacks": sensor_events,
            "config_equal_except_interventions": bool(configurations_match),
            "initial_and_target_points_identical": bool(positions_match),
            "blocked_target_gap_min_m": blocked.get("minimum_goal_gap_m"),
            "service_complete": None,
            "independent_curb_verified": False,
            "boarding_verified": False,
        }
        rows.append(row)
    return {
        "schema": "capplan_handoff_carla_triplet_audit",
        "scope": "CARLA_TOWN_TRAJECTORY_SURROGATE_ONLY",
        "not_real_pudo_or_passenger_service_ground_truth": True,
        "counts": dict(counts),
        "trials": rows,
        "next": "Fix actual redirect and freeze independently audited stop pose/interface/time before claiming handoff",
    }


def _mode_city(pair):
    text = str(pair.get("map_name", pair.get("city", ""))).lower()
    if "boston" in text: return "boston"
    if "pittsburgh" in text or "hazelwood" in text: return "pittsburgh"
    if "singapore" in text or "one-north" in text: return "singapore"
    if "vegas" in text or "las-vegas" in text: return "vegas"
    return "unknown"


def select_pairs(pairs: Path, per_city: int):
    """Bounded-memory, reproducible site-disjoint screened sampling.

    Reads a potentially multi-GB JSONL once, retains at most per_city records
    per city. It never loads the entire 65k+ candidate set into memory.
    """
    if per_city < 1: raise ValueError("per_city must be positive")
    pool = defaultdict(dict)
    for p in _read_jsonl(Path(pairs)):
        city = _mode_city(p)
        site = p.get("physical_site_pair") or [p.get("a_anchor"), p.get("b_anchor")]
        site_key = "|".join(sorted(str(v) for v in site)) if isinstance(site, list) else str(site)
        identity = "|".join([city, str(p.get("episode_id")), site_key])
        rank = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        bucket = pool[city]
        if site_key in bucket:
            if rank < bucket[site_key][0]: bucket[site_key] = (rank, p)
            continue
        if len(bucket) < per_city:
            bucket[site_key] = (rank, p)
        else:
            worst_site = max(bucket, key=lambda k: bucket[k][0])
            if rank < bucket[worst_site][0]:
                del bucket[worst_site]
                bucket[site_key] = (rank, p)
    return [p for city in sorted(pool) for _, p in sorted(pool[city].values(), key=lambda x:x[0])]


def _edges_for_contract(graph, contract, *, directed: bool):
    """Graph encoding sensitivity: one serialization arc != real one-way law."""
    from scripts.mine_handoff_recourse_pairs import _valid_edge
    opts = SimpleNamespace(
        min_width_m=float(contract.get("min_width_m", 0)),
        max_slope=float(contract.get("max_slope", 1)),
        max_cross_slope=float(contract.get("max_cross_slope", 1)),
        min_confidence=float(contract.get("min_confidence", 0)),
        require_step_free=bool(contract.get("require_step_free", False)),
    )
    adj = defaultdict(list)
    rev = defaultdict(list)
    by_id = defaultdict(list)
    for e in graph.edges:
        if not _valid_edge(e, opts): continue
        cost = float(e.length_m)
        adj[e.from_node].append((e.to_node, cost, e))
        rev[e.to_node].append((e.from_node, cost, e))
        by_id[e.edge_id].append((e.from_node, e.to_node))
        if not directed:
            adj[e.to_node].append((e.from_node, cost, e))
            rev[e.from_node].append((e.to_node, cost, e))
    return adj, rev, by_id


def _tree(adj, source):
    d = {source: 0.0}
    prev = {}
    pq = [(0.0, source)]
    while pq:
        cost, node = heapq.heappop(pq)
        if cost > d[node] + 1e-9: continue
        for dest, weight, edge in adj.get(node, ()):
            cand = cost + weight
            if cand + 1e-9 < d.get(dest, float("inf")):
                d[dest] = cand
                prev[dest] = (node, edge)
                heapq.heappush(pq, (cand, dest))
    return d, prev


def _recover_path(prev, source, target):
    if source == target: return [], [source]
    path, nodes = [], [target]
    u = target
    while u != source:
        if u not in prev: return None, None
        parent, edge = prev[u]
        path.append((parent, u, float(edge.length_m), str(edge.edge_id)))
        u = parent
        nodes.append(u)
    return path[::-1], nodes[::-1]


def _mid_edge_recourse(path, progress, rev, target_dist, reverse_costs):
    length = sum(e[2] for e in path)
    if progress >= length - 1e-8:
        return target_dist.get(path[-1][1], float("inf")) if path else float("inf")
    traversed = 0.0
    for u, v, ln, _ in path:
        if progress <= traversed + ln + 1e-8:
            p = max(0.0, progress - traversed)
            fw = (ln - p) + target_dist.get(v, float("inf"))
            rev_len = reverse_costs.get((v, u))
            bw = (p / ln * rev_len + target_dist.get(u, float("inf"))) if rev_len is not None and ln > 0 else float("inf")
            at_vertex = target_dist.get(u, float("inf")) if p <= 1e-8 else float("inf")
            return min(fw, bw, at_vertex)
        traversed += ln
    return float("inf")


def inspect_graph_pair(pair, graph, notice_times, *, speed_mps: float, ack_s: float,
                       directed: bool = True, other_anchors: Iterable[dict] = ()):  # graph only
    """Re-mine rather than validate a path originally generated undirected.

    True directed graph semantics might be encoded as one arc of an undirected
    feature; report the direction choice as a sensitivity assumption.
    """
    if speed_mps <= 0 or ack_s < 0 or not all(math.isfinite(x) and x >= 0 for x in notice_times):
        raise ValueError("invalid time/velocity")
    contract = pair["screening_contract"]
    node = pair["pedestrian_graph_nodes"]
    origin, A, B = node["origin"], node["A"], node["B"]
    adj, rev, _ = _edges_for_contract(graph, contract, directed=directed)
    dist_O, prev_O = _tree(adj, origin)
    path_A, nodes_A = _recover_path(prev_O, origin, A)
    distance_budget = float(contract["distance_budget_m"])
    consumed = float(contract.get("already_consumed_m", 0.0))
    if path_A is None or B not in dist_O:
        return {"episode_id": pair.get("episode_id"), "a_anchor": pair.get("a_anchor"),
                "b_anchor": pair.get("b_anchor"), "city": _mode_city(pair),
                "direction_mode": "AS_ENCODED" if directed else "OPTIMISTIC_SYMMETRIZED",
                "status": "NO_TWO_DIRECT_PATHS_UNDER_ENCODING",
                "origin_to_A_available": path_A is not None, "origin_to_B_available": B in dist_O,
                "direction_encoding_independently_audited": False,
                "wait_budgets_and_evidence_time_checked": False,
                "real_physical_handoff_verified": False,
                "evidence_level": "ENCODED_GRAPH_ONLY", "real_world_unreachable": None}
    dist_to_B, _ = _tree(rev, B)
    dist_A_B = dist_to_B.get(A, float("inf"))
    distance_A = dist_O[A]
    distance_B = dist_O[B]
    can_choose_both = consumed + max(distance_A, distance_B) <= distance_budget + 1e-9
    naive_over_budget = consumed + distance_A + dist_A_B > distance_budget + 1e-9
    if not can_choose_both:
        state = "NOT_INITIAL_TWO_OPTION_FEASIBLE"
    elif not naive_over_budget:
        state = "ORIGINAL_TERMINAL_AB_FAILURE_DISAPPEARED"
    else:
        state = "DIRECTED_TERMINAL_AB_ASYMMETRY_EXISTS"
    reverse_costs = {}
    for u, outs in adj.items():
        for v, weight, _ in outs:
            reverse_costs[(u, v)] = min(weight, reverse_costs.get((u, v), float("inf")))
    rest = []
    other_nodes = {str(z.get("anchor_id")): z.get("adjacent_ped_node_id") or z.get("anchor_id")
                   for z in other_anchors if z.get("legal_stop") is True and
                   z.get("kind") in ("pickup", "pickup_dropoff") and
                   z.get("anchor_id") not in (pair.get("a_anchor"), pair.get("b_anchor"))}
    # Third-option computation scales with the number of actionable path
    # positions rather than with the number of potentially thousands of PUDOs.
    cached_forward = {}
    def forward_from(n):
        if n not in cached_forward: cached_forward[n] = _tree(adj, n)[0]
        return cached_forward[n]

    def alternate_distance(progress, target):
        # On an arc, continue to v; reverse only if the reverse arc is encoded.
        traveled=0.
        for u, v, ln, _ in path_A:
            if progress <= traveled + ln + 1e-8:
                into=max(0.,progress-traveled)
                if into <= 1e-8: return forward_from(u).get(target,float('inf'))
                ahead=(ln-into)+forward_from(v).get(target,float('inf'))
                back_cost=reverse_costs.get((v,u))
                behind=(into / ln * back_cost + forward_from(u).get(target,float('inf'))
                        if back_cost is not None and ln>0 else float('inf'))
                return min(ahead,behind)
            traveled+=ln
        return forward_from(A).get(target,float('inf'))

    for t in notice_times:
        progress = min(float(t + ack_s) * speed_mps, distance_A)
        dB = _mid_edge_recourse(path_A, progress, rev, dist_to_B, reverse_costs)
        third = [name for name, destination in other_nodes.items() if
                 consumed + progress + alternate_distance(progress,destination)
                 <= distance_budget + 1e-9]
        rest.append({"notice_issued_s": t, "actionable_at_s": t + ack_s,
                     "distance_consumed_m": round(consumed + progress, 3),
                     "distance_to_B_m": round(dB, 3) if math.isfinite(dB) else None,
                     "B_within_distance_budget": bool(math.isfinite(dB) and consumed + progress + dB <= distance_budget + 1e-9),
                     "third_graph_feasible_count": len(third), "third_graph_examples": third[:10],
                     "unknown_physical_stopping_legality_and_vehicle_time": True})
    return {"episode_id": pair.get("episode_id"), "a_anchor": pair.get("a_anchor"),
            "b_anchor": pair.get("b_anchor"), "city": _mode_city(pair),
            "status": state, "direction_mode": "AS_ENCODED" if directed else "OPTIMISTIC_SYMMETRIZED",
            "direction_encoding_independently_audited": False,
            "path_O_A_node_ids": nodes_A,
            "path_O_A_edges": [{"edge_id": eid, "from": u, "to": v, "length_m": ln} for u, v, ln, eid in path_A],
            "O_A_m": round(distance_A, 4), "O_B_m": round(distance_B, 4),
            "A_B_m": round(dist_A_B, 4) if math.isfinite(dist_A_B) else None,
            "original_pair_distances_m": pair.get("distances_m"),
            "wait_budgets_and_evidence_time_checked": False,
            "event_incidence_estimated": False, "real_physical_handoff_verified": False,
            "midroute": rest, "evidence_level": "ENCODED_GRAPH_ONLY"}


def run_graph_screen(dataset_dir: Path, pairs_file: Path, output: Path, *, per_city: int = 8,
                     times=(0., 15., 30., 60., 90.), speed_mps=.8, ack_s=3.,
                     third_options=False):
    """Bounded episode-load screen, preserves all raw pair/graph provenance."""
    from capplan.data.accessibility_layer import load_accessibility_graph
    dataset_dir = Path(dataset_dir)
    chosen = select_pairs(pairs_file, per_city=per_city)
    group = defaultdict(list)
    for p in chosen: group[p["episode_id"]].append(p)
    anchors_by_ep = defaultdict(list)
    if third_options:
        for p in _read_jsonl(dataset_dir / "pudo_anchors.jsonl"):
            if p.get("episode_id") in group:
                anchors_by_ep[p["episode_id"]].append(p)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    counts = Counter()
    sample_by_city = Counter()
    errors = []
    successful_pairs = set()
    failed_pairs = set()
    written_rows = 0
    with output.open("w", encoding="utf-8") as handle:
        for episode, subset in sorted(group.items()):
            try:
                graph = load_accessibility_graph(dataset_dir, episode)
            except (ValueError, TypeError, KeyError, OSError) as exc:
                errors.append({"episode_id": episode, "stage": "load_graph", "error": repr(exc)})
                for pair in subset:
                    sample_by_city[_mode_city(pair)] += 1
                    failed_pairs.add((episode, str(pair.get("a_anchor")), str(pair.get("b_anchor"))))
                    handle.write(json.dumps({"episode_id": episode,
                        "a_anchor": pair.get("a_anchor"), "b_anchor": pair.get("b_anchor"),
                        "status": "UNKNOWN_GRAPH_LOAD_ERROR", "error": repr(exc),
                        "evidence_level": "ENCODED_GRAPH_ONLY"}, ensure_ascii=False) + "\n")
                    written_rows += 1
                continue
            for pair in subset:
                sample_by_city[_mode_city(pair)] += 1
                key = (episode, str(pair.get("a_anchor")), str(pair.get("b_anchor")))
                any_error = False
                for directed in (True, False):
                    mode = "AS_ENCODED" if directed else "OPTIMISTIC_SYMMETRIZED"
                    try:
                        r = inspect_graph_pair(pair, graph, list(times), speed_mps=speed_mps,
                                               ack_s=ack_s, directed=directed,
                                               other_anchors=anchors_by_ep[episode])
                    except (ValueError, TypeError, KeyError, OSError, ZeroDivisionError) as exc:
                        any_error = True
                        r = {"episode_id": episode, "a_anchor": pair.get("a_anchor"),
                             "b_anchor": pair.get("b_anchor"), "direction_mode": mode,
                             "status": "UNKNOWN_PAIR_EVALUATION_ERROR", "error": repr(exc),
                             "evidence_level": "ENCODED_GRAPH_ONLY"}
                        errors.append({"episode_id": episode, "direction_mode": mode,
                                       "stage": "evaluate_pair", "error": repr(exc)})
                    counts[f"{mode}::{r['status']}"] += 1
                    handle.write(json.dumps(r, ensure_ascii=False) + "\n")
                    written_rows += 1
                (failed_pairs if any_error else successful_pairs).add(key)
    report = {
        "schema": "capplan_handoff_directed_remine",
        "selection": "HASH_RANKED_SITE_PAIR_DEDUPLICATED_FROM_PREVIOUSLY_SCREENED_CANDIDATES",
        "not_natural_event_incidence": True, "source_pairs_file_sha256": _digest_file(pairs_file),
        "original_selected_pairs": len(chosen), "selected_by_city": dict(sample_by_city),
        "evaluated_pair_count": len(successful_pairs),
        "unknown_pair_count": len(failed_pairs),
        "direction_evaluation_rows_expected": 2 * len(chosen),
        "direction_evaluation_rows_written": written_rows,
        "counts": dict(counts), "errors": errors[:30], "third_options_checked": third_options,
        "screening_contract_and_arc_direction_must_be_independently_audited": True,
        "scope": "GRAPH_REMINING_ONLY_NO_PHYSICAL_HANDOFF_OR_GLOBAL_STRANDING_CERTIFICATE",
    }
    _atomic_json(output.with_suffix('.summary.json'), report)
    return report


def site_attestation_audit(packets_path: Path):
    """Fail-closed physical-packet completeness audit; never synthesize PASS.

    Metadata alone, simulated GIS data, and CARLA Town03 cannot certify real
    stop pose, curb legality or independent human boarding.
    """
    counts = Counter()
    missing = Counter()
    samples = []
    total = 0
    for row in _read_jsonl(Path(packets_path)):
        total += 1
        att = row.get("attestations") or {}
        statuses = []
        for field in ("directed_pedestrian_path", "legal_stop", "vehicle_trajectory_stop_pose",
                      "deployment_region", "dynamic_observation"):
            x = att.get(field) or {}
            status = x.get("review_status", "UNKNOWN")
            source = x.get("independent_source_ref")
            if status != "PASS" or not source:
                missing[field] += 1
            statuses.append((status, bool(source)))
        # No assumed constants for boarding and driver movement; require
        # independently validated time window and user capability first.
        time_fields = ("passenger_arrival_window_s", "passenger_wait_remaining_s",
                       "boarding_duration_s", "vehicle_stop_windows_s", "evidence_validity_windows_s")
        unknown_time = [k for k in time_fields if row.get(k) is None]
        for k in unknown_time: missing[k] += 1
        # INVALIDATED requires a failure attestation with a source, not an
        # absent field or a simulated negative. More evidence types may be
        # required for full handoff, even when every field here is populated.
        actual_failure = any(st == "FAIL" and src for st, src in statuses)
        all_provided = all(st == "PASS" and src for st, src in statuses) and not unknown_time
        if actual_failure: result = "ATTESTED_COMPONENT_FAILURE_NOT_GLOBAL_INFEASIBILITY"
        elif all_provided: result = "COMPONENT_RECORDS_PRESENT_REQUIRES_INDEPENDENT_VALIDATION"
        else: result = "UNKNOWN_MISSING_EVIDENCE"
        counts[result] += 1
        if len(samples) < 10:
            samples.append({"option_id": row.get("option_id"),"state": result,
                            "missing_time": unknown_time,
                            "unknown_attestations": [k for k, x in att.items()
                              if x.get("review_status") != "PASS" or not x.get("independent_source_ref")]})
    return {"schema": "capplan_site_attestation_status", "records": total,
            "status_counts": dict(counts), "missing_field_counts": dict(missing),
            "examples": samples,"simulated_input_does_not_upgrade_to_real_truth": True,
            "verified_real_handoff_count": 0,
            "note":"This audit checks completeness only; human/vehicle service proof requires separate independent validators"}


def legacy_research_inventory(repository: Path):
    """Non-destructive deprecation plan: no script is deleted or moved."""
    import re
    root = Path(repository)
    programs = sorted((root / "scripts").glob("*.py"))
    versioned = [p for p in programs if re.search(r'_v\d+\.py$', p.name)]
    refs = [p for folder in ("scripts", "tests", "capplan", "docs")
            for p in (root / folder).rglob("*") if p.is_file() and p.suffix in (".py", ".sh", ".md", ".yaml")]
    texts = {p: p.read_text(encoding="utf-8", errors="replace") for p in refs}
    records = []
    for p in versioned:
        dependencies = [str(q.relative_to(root)) for q, body in texts.items()
                        if q != p and (p.stem in body or p.name in body)]
        records.append({"path": str(p.relative_to(root)), "bytes": p.stat().st_size,
                        "references": dependencies[:40], "reference_count": len(dependencies),
                        "migration_policy": "MANUAL_REVIEW_REQUIRED_DO_NOT_AUTO_DELETE"})
    return {"schema": "capplan_non_destructive_legacy_research_inventory",
            "versioned_script_count": len(versioned),
            "versioned_script_bytes": sum(p.stat().st_size for p in versioned),
            "stable_cli": "scripts/handoff_study.py",
            "actions": ["Freeze old script entry points for historical reproducibility",
                        "Add all new experiments to stable research core and CLI",
                        "After reproducing historic artifacts and checking docs/shell callers, archive obsolete scripts with hashes"],
            "no_files_deleted": True, "records": records}
