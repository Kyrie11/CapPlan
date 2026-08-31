"""Dataset validation for canonical CapPlan/AbilityBench-AV layout."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Tuple

from capplan.data.capability_contracts import contract_episode_id
from capplan.data.schemas import contract_from_dict, transition_from_dict
from capplan.utils.serialization import load_json, iter_jsonl

try:
    from tqdm.auto import tqdm  # type: ignore
except Exception:  # pragma: no cover
    def tqdm(iterable=None, **kwargs):  # type: ignore
        return iterable if iterable is not None else []


VALIDATION_VERSION = "capplan_dataset_validation_v2_linear_20260831"

CANONICAL_FILES = [
    "dataset_manifest.json",
    "scenes.jsonl",
    "episodes.jsonl",
    "entrances.jsonl",
    "pudo_anchors.jsonl",
    "vehicle_interfaces.jsonl",
    "capability_profiles.jsonl",
    "capability_contracts.jsonl",
    "requirement_groups.jsonl",
    "candidate_transitions.jsonl",
    "transition_labels.jsonl",
    "passenger_edge_labels.jsonl",
    "resource_labels.jsonl",
    "skeleton_labels.jsonl",
    "certificate_labels.jsonl",
    "counterfactual_pairs.jsonl",
]


def _read(path: Path, desc: str, progress: bool) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    iterator = iter_jsonl(path)
    if progress:
        iterator = tqdm(iterator, desc=desc, unit="row")
    rows.extend(iterator)
    return rows


def _iter(path: Path, desc: str, progress: bool) -> Iterable[dict[str, Any]]:
    iterator = iter_jsonl(path)
    return tqdm(iterator, desc=desc, unit="row") if progress else iterator


def _first_missing_edge_pairs(
    transitions_by_episode: Dict[str, list[str]],
    passengers_by_episode: Dict[str, set[str]],
    seen: set[tuple[str, str]],
    limit: int = 5,
) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for eid, tids in transitions_by_episode.items():
        pids = passengers_by_episode.get(eid, set())
        for tid in tids:
            for pid in pids:
                pair = (tid, pid)
                if pair not in seen:
                    out.append(pair)
                    if len(out) >= limit:
                        return out
    return out


def validate_dataset(
    dataset_dir: str | Path,
    strict: bool = False,
    *,
    progress: bool = False,
    skip_graph_membership: bool = False,
) -> Dict[str, Any]:
    """Validate a canonical dataset in approximately linear time.

    ``skip_graph_membership`` is intended only for merged datasets produced by
    ``merge_datasets.py`` from pairwise-disjoint inputs whose own strict
    validation reports are already PASS.  The merge byte-preserves episode rows
    and hardlinks/copies graph files, so repeating tens of millions of graph-node
    JSON parses adds cost without adding a new integrity check.  All cross-input
    split, contract, transition and passenger-label checks still run.
    """
    root = Path(dataset_dir)
    errors: List[str] = []
    warnings: List[str] = []
    if not root.exists():
        raise FileNotFoundError(root)

    print(
        f"[CAPPLAN_VALIDATE] version={VALIDATION_VERSION} dataset={root} strict={strict} "
        f"skip_graph_membership={skip_graph_membership}",
        flush=True,
    )

    for name in CANONICAL_FILES:
        if not (root / name).exists():
            errors.append(f"missing canonical file {name}")
    for split in ("train", "val", "test"):
        if not (root / "splits" / f"{split}_episodes.txt").exists():
            errors.append(f"missing splits/{split}_episodes.txt")

    try:
        manifest = load_json(root / "dataset_manifest.json") if (root / "dataset_manifest.json").exists() else {}
    except Exception as e:
        manifest = {}
        errors.append(f"invalid dataset_manifest.json: {e}")

    scenes = _read(root / "scenes.jsonl", "validate scenes", progress)
    episodes = _read(root / "episodes.jsonl", "validate episodes", progress)
    entrances = _read(root / "entrances.jsonl", "validate entrances", progress)
    pudos = _read(root / "pudo_anchors.jsonl", "validate PUDO anchors", progress)
    contracts_raw = _read(root / "capability_contracts.jsonl", "validate contracts", progress)
    transitions_raw = _read(root / "candidate_transitions.jsonl", "validate transitions", progress)
    pairs = _read(root / "counterfactual_pairs.jsonl", "validate counterfactual pairs", progress)
    service_requests = _read(root / "service_requests.jsonl", "validate service requests", progress) if (root / "service_requests.jsonl").exists() else []

    episode_ids = {str(e.get("episode_id")) for e in episodes if e.get("episode_id") is not None}
    scene_ids = {str(s.get("episode_id")) for s in scenes if s.get("episode_id") is not None}
    if episode_ids != scene_ids:
        diff = sorted(episode_ids ^ scene_ids)
        errors.append(f"episodes/scenes mismatch: {diff[:20]}{' ...' if len(diff) > 20 else ''}")
    if manifest.get("scene_source") == "nuplan":
        bad = [s.get("episode_id") for s in scenes if s.get("source") == "synthetic"]
        if bad:
            errors.append(f"nuPlan dataset contains synthetic scenes: {bad[:5]}")
    if manifest.get("scene_source") == "synthetic":
        bad = [s.get("episode_id") for s in scenes if s.get("source") != "synthetic"]
        if bad:
            errors.append(f"synthetic dataset contains non-synthetic scenes: {bad[:5]}")

    entrance_ids = {str(a.get("anchor_id")) for a in entrances if a.get("anchor_id") is not None}
    pudo_ids = {str(p.get("anchor_id")) for p in pudos if p.get("anchor_id") is not None}

    # Always verify graph files exist.  Deep mode additionally verifies each
    # PUDO adjacency against the corresponding episode graph, while holding only
    # one episode's node IDs in memory at a time.
    pudo_ped_by_episode: Dict[str, list[tuple[str, str]]] = defaultdict(list)
    for p in pudos:
        eid = str(p.get("episode_id") or "")
        aid = str(p.get("anchor_id") or "")
        if eid not in episode_ids:
            errors.append(f"PUDO {aid} references unknown episode {eid}")
        ped = p.get("adjacent_ped_node_id")
        if ped:
            pudo_ped_by_episode[eid].append((aid, str(ped)))
        if not p.get("roadblock_id") and strict:
            warnings.append(f"PUDO {aid} lacks roadblock_id")

    graph_iter = tqdm(sorted(episode_ids), desc="validate graph membership", unit="episode", disable=not progress)
    for eid in graph_iter:
        nodes_path = root / "accessibility_graphs" / f"{eid}.nodes.jsonl"
        edges_path = root / "accessibility_graphs" / f"{eid}.edges.jsonl"
        if not nodes_path.exists() or not edges_path.exists():
            errors.append(f"missing accessibility node/edge files for {eid}")
            continue
        if skip_graph_membership:
            continue
        needed = pudo_ped_by_episode.get(eid, [])
        if not needed:
            continue
        wanted = {ped for _, ped in needed}
        found: set[str] = set()
        for n in iter_jsonl(nodes_path):
            nid = n.get("node_id")
            if nid is not None and str(nid) in wanted:
                found.add(str(nid))
                if len(found) == len(wanted):
                    break
        if len(found) != len(wanted):
            for aid, ped in needed:
                if ped not in found:
                    errors.append(f"PUDO {aid} adjacent_ped_node_id {ped} missing from graph")

    contracts = []
    passengers_by_episode: Dict[str, set[str]] = defaultdict(set)
    contract_episode_by_passenger: Dict[str, str] = {}
    for d in contracts_raw:
        try:
            c = contract_from_dict(d)
            contracts.append(c)
            eid = str(contract_episode_id(c))
            pid = str(c.passenger_id)
            contract_episode_by_passenger[pid] = eid
            passengers_by_episode[eid].add(pid)
            if eid not in episode_ids:
                errors.append(f"contract {pid} references unknown episode {eid}")
        except Exception as e:
            errors.append(f"invalid contract {d.get('passenger_id')}: {e}")

    transition_ids: set[str] = set()
    transition_episode_by_id: Dict[str, str] = {}
    transitions_by_episode: Dict[str, list[str]] = defaultdict(list)
    transitions = []
    for d in transitions_raw:
        try:
            t = transition_from_dict(d)
            transitions.append(t)
            tid = str(t.transition_id)
            eid = str(t.episode_id)
            if tid in transition_ids:
                errors.append(f"duplicate transition_id: {tid}")
            transition_ids.add(tid)
            transition_episode_by_id[tid] = eid
            transitions_by_episode[eid].append(tid)
            if eid not in episode_ids:
                errors.append(f"transition {tid} references unknown episode {eid}")
            for anchor in [t.from_anchor, t.to_anchor]:
                if anchor.startswith("veh:") or anchor == "destination" or anchor == "origin":
                    continue
                if anchor.startswith("replan:"):
                    continue
                if anchor not in pudo_ids and anchor not in entrance_ids:
                    errors.append(f"transition {tid} references unknown anchor {anchor}")
        except Exception as e:
            errors.append(f"invalid transition: {e}")

    label_ids: set[str] = set()
    for l in _iter(root / "transition_labels.jsonl", "validate transition labels", progress):
        tid = str(l.get("transition_id") or "")
        if tid in label_ids:
            errors.append(f"duplicate transition label: {tid}")
        label_ids.add(tid)
        if tid and tid not in transition_ids:
            errors.append(f"transition label references unknown transition {tid}")
    missing_labels = transition_ids - label_ids
    if missing_labels:
        errors.append(f"candidate transitions without transition labels: {len(missing_labels)}")

    passenger_ids = set(contract_episode_by_passenger)
    expected_edge_label_count = sum(
        len(tids) * len(passengers_by_episode.get(eid, set()))
        for eid, tids in transitions_by_episode.items()
    )
    pel_pairs: set[tuple[str, str]] = set()
    pel_count = 0
    for l in _iter(root / "passenger_edge_labels.jsonl", "validate passenger edge labels", progress):
        pel_count += 1
        tid = str(l.get("transition_id") or "")
        pid = str(l.get("passenger_id") or "")
        pair = (tid, pid)
        if pair in pel_pairs:
            errors.append(f"duplicate passenger edge label: {tid} {pid}")
        pel_pairs.add(pair)
        teid = transition_episode_by_id.get(tid)
        peid = contract_episode_by_passenger.get(pid)
        if teid is None:
            errors.append(f"passenger edge label references unknown transition {tid}")
        if peid is None:
            errors.append(f"passenger edge label references unknown passenger {pid}")
        if teid is not None and peid is not None and teid != peid:
            errors.append(f"passenger edge label crosses episodes: {tid}({teid}) {pid}({peid})")
        if l.get("y_e_p") and l.get("failed_resources"):
            errors.append(f"passenger edge label feasible despite failed resources: {tid} {pid}")
        if l.get("y_e_p") and any(float(v) < 0 for v in (l.get("margins") or {}).values()):
            errors.append(f"passenger edge label feasible despite negative margin: {tid} {pid}")
    if len(pel_pairs) != expected_edge_label_count:
        missing_count = max(0, expected_edge_label_count - len(pel_pairs))
        examples = _first_missing_edge_pairs(transitions_by_episode, passengers_by_episode, pel_pairs)
        if missing_count:
            errors.append(f"missing passenger edge labels: {missing_count}; first examples: {examples}")
        if len(pel_pairs) > expected_edge_label_count:
            errors.append(
                f"unexpected passenger edge label pairs: seen={len(pel_pairs)} expected={expected_edge_label_count}"
            )

    resource_count = 0
    for r in _iter(root / "resource_labels.jsonl", "validate resource labels", progress):
        resource_count += 1
        if str(r.get("transition_id") or "") not in transition_ids:
            errors.append(f"resource label references unknown transition {r.get('transition_id')}")
        if r.get("missing") and r.get("value") is not None:
            warnings.append(f"resource label {r.get('transition_id')} marks missing with non-null value")

    requests_by_episode_profile: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for request in service_requests:
        key = (str(request.get("episode_id")), str(request.get("passenger_profile_id")))
        requests_by_episode_profile.setdefault(key, []).append(request)
    for pair in pairs:
        if str(pair.get("episode_id")) not in episode_ids:
            errors.append(f"counterfactual pair references unknown episode {pair.get('episode_id')}")
        if str(pair.get("weak_passenger_id")) not in passenger_ids or str(pair.get("strict_passenger_id")) not in passenger_ids:
            errors.append(f"counterfactual pair references unknown passengers {pair}")
        weak_pid = str(pair.get("weak_passenger_id") or "")
        strict_pid = str(pair.get("strict_passenger_id") or "")
        weak_eid = contract_episode_by_passenger.get(weak_pid)
        strict_eid = contract_episode_by_passenger.get(strict_pid)
        if weak_eid is not None and strict_eid is not None and weak_eid != strict_eid:
            errors.append(f"counterfactual pair crosses episodes {pair}")
        pair_eid = str(pair.get("episode_id") or "")
        if weak_eid is not None and pair_eid and weak_eid != pair_eid:
            errors.append(f"counterfactual pair weak contract episode mismatch {pair}")
        if strict_eid is not None and pair_eid and strict_eid != pair_eid:
            errors.append(f"counterfactual pair strict contract episode mismatch {pair}")
        wp = str(pair.get("weak_profile_id") or "")
        sp = str(pair.get("strict_profile_id") or "")
        if wp and sp and service_requests:
            weak_rows = requests_by_episode_profile.get((pair_eid, wp), [])
            strict_rows = requests_by_episode_profile.get((pair_eid, sp), [])
            if not weak_rows or not strict_rows:
                errors.append(f"counterfactual pair missing service request profile binding {pair.get('pair_id')}")
            else:
                w, st = weak_rows[0], strict_rows[0]
                w_key = (w.get("origin_entrance_id"), w.get("destination_entrance_id"), float(w.get("request_time_s", 0.0)))
                s_key = (st.get("origin_entrance_id"), st.get("destination_entrance_id"), float(st.get("request_time_s", 0.0)))
                if w_key != s_key:
                    errors.append(f"counterfactual pair is not same-OD/time {pair.get('pair_id')}: {w_key} != {s_key}")
                gid = pair.get("counterfactual_group_id")
                if gid and (w.get("counterfactual_group_id") != gid or st.get("counterfactual_group_id") != gid):
                    errors.append(f"counterfactual pair group mismatch {pair.get('pair_id')}")

    result = {
        "ok": not errors,
        "valid": not errors,
        "status": "PASS" if not errors else "FAIL",
        "version": VALIDATION_VERSION,
        "errors": errors,
        "warnings": warnings,
        "num_episodes": len(episodes),
        "num_contracts": len(contracts),
        "num_transitions": len(transitions),
        "num_passenger_edge_labels": pel_count,
        "num_resource_labels": resource_count,
        "expected_passenger_edge_labels": expected_edge_label_count,
        "graph_membership_check": "reused_upstream_validated_inputs" if skip_graph_membership else "deep_episode_scan",
    }
    print(
        f"[CAPPLAN_VALIDATE] done ok={result['ok']} episodes={result['num_episodes']} "
        f"contracts={result['num_contracts']} transitions={result['num_transitions']} "
        f"passenger_edge_labels={pel_count}/{expected_edge_label_count} errors={len(errors)} warnings={len(warnings)}",
        flush=True,
    )
    if strict and errors:
        raise ValueError("dataset validation failed:\n" + "\n".join(errors))
    return result
