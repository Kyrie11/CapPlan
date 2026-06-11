"""Dataset validation for canonical CapPlan/AbilityBench-AV layout."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

from capplan.data.schemas import contract_from_dict, graph_from_records, transition_from_dict
from capplan.semantics.typed_resource_algebra import MissingEvidence
from capplan.utils.serialization import load_json, read_jsonl

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


def validate_dataset(dataset_dir: str | Path, strict: bool = False) -> Dict[str, Any]:
    root = Path(dataset_dir)
    errors: List[str] = []
    warnings: List[str] = []
    if not root.exists():
        raise FileNotFoundError(root)
    for name in CANONICAL_FILES:
        if not (root / name).exists():
            errors.append(f"missing canonical file {name}")
    if not (root / "splits" / "train_episodes.txt").exists():
        errors.append("missing splits/train_episodes.txt")
    if not (root / "splits" / "val_episodes.txt").exists():
        errors.append("missing splits/val_episodes.txt")
    if not (root / "splits" / "test_episodes.txt").exists():
        errors.append("missing splits/test_episodes.txt")
    try:
        manifest = load_json(root / "dataset_manifest.json") if (root / "dataset_manifest.json").exists() else {}
    except Exception as e:
        manifest = {}
        errors.append(f"invalid dataset_manifest.json: {e}")
    scenes = read_jsonl(root / "scenes.jsonl")
    episodes = read_jsonl(root / "episodes.jsonl")
    entrances = read_jsonl(root / "entrances.jsonl")
    pudos = read_jsonl(root / "pudo_anchors.jsonl")
    vehicles = read_jsonl(root / "vehicle_interfaces.jsonl")
    contracts_raw = read_jsonl(root / "capability_contracts.jsonl")
    transitions_raw = read_jsonl(root / "candidate_transitions.jsonl")
    transition_labels = read_jsonl(root / "transition_labels.jsonl")
    passenger_edge_labels = read_jsonl(root / "passenger_edge_labels.jsonl")
    resource_labels = read_jsonl(root / "resource_labels.jsonl")
    pairs = read_jsonl(root / "counterfactual_pairs.jsonl")
    episode_ids = {e.get("episode_id") for e in episodes}
    scene_ids = {s.get("episode_id") for s in scenes}
    if episode_ids != scene_ids:
        errors.append(f"episodes/scenes mismatch: {sorted(episode_ids ^ scene_ids)}")
    if manifest.get("scene_source") == "nuplan":
        bad = [s.get("episode_id") for s in scenes if s.get("source") == "synthetic"]
        if bad:
            errors.append(f"nuPlan dataset contains synthetic scenes: {bad[:5]}")
    if manifest.get("scene_source") == "synthetic":
        bad = [s.get("episode_id") for s in scenes if s.get("source") != "synthetic"]
        if bad:
            errors.append(f"synthetic dataset contains non-synthetic scenes: {bad[:5]}")
    entrance_ids = {a.get("anchor_id") for a in entrances}
    pudo_ids = {p.get("anchor_id") for p in pudos}
    node_ids_by_episode: Dict[str, set[str]] = {}
    for eid in episode_ids:
        nodes_path = root / "accessibility_graphs" / f"{eid}.nodes.jsonl"
        edges_path = root / "accessibility_graphs" / f"{eid}.edges.jsonl"
        if not nodes_path.exists() or not edges_path.exists():
            errors.append(f"missing accessibility node/edge files for {eid}")
            continue
        nodes = read_jsonl(nodes_path)
        node_ids_by_episode[eid] = {n.get("node_id") for n in nodes}
    for p in pudos:
        eid = p.get("episode_id")
        if eid not in episode_ids:
            errors.append(f"PUDO {p.get('anchor_id')} references unknown episode {eid}")
        ped = p.get("adjacent_ped_node_id")
        if ped and ped not in node_ids_by_episode.get(eid, set()):
            errors.append(f"PUDO {p.get('anchor_id')} adjacent_ped_node_id {ped} missing from graph")
        if not p.get("roadblock_id") and strict:
            warnings.append(f"PUDO {p.get('anchor_id')} lacks roadblock_id")
    contracts = []
    for d in contracts_raw:
        try:
            c = contract_from_dict(d)
            contracts.append(c)
            eid = c.passenger_id.split(":p")[0]
            if eid not in episode_ids:
                errors.append(f"contract {c.passenger_id} references unknown episode {eid}")
        except Exception as e:
            errors.append(f"invalid contract {d.get('passenger_id')}: {e}")
    transition_ids = set()
    transitions = []
    for d in transitions_raw:
        try:
            t = transition_from_dict(d)
            transitions.append(t)
            transition_ids.add(t.transition_id)
            if t.episode_id not in episode_ids:
                errors.append(f"transition {t.transition_id} references unknown episode {t.episode_id}")
            # Transition anchors may be vehicle states; only validate service anchors.
            for anchor in [t.from_anchor, t.to_anchor]:
                if anchor.startswith("veh:") or anchor == "destination" or anchor == "origin":
                    continue
                if anchor.startswith("replan:"):
                    continue
                if anchor not in pudo_ids and anchor not in entrance_ids:
                    errors.append(f"transition {t.transition_id} references unknown anchor {anchor}")
        except Exception as e:
            errors.append(f"invalid transition: {e}")
    label_ids = {l.get("transition_id") for l in transition_labels}
    missing_labels = transition_ids - label_ids
    if missing_labels:
        errors.append(f"candidate transitions without transition labels: {len(missing_labels)}")
    passenger_ids = {c.passenger_id for c in contracts}
    pel_pairs = {(l.get("transition_id"), l.get("passenger_id")) for l in passenger_edge_labels}
    expected_edge_labels = {(t.transition_id, c.passenger_id) for t in transitions for c in contracts if c.passenger_id.split(":p")[0] == t.episode_id}
    missing_pel = expected_edge_labels - pel_pairs
    if missing_pel:
        errors.append(f"missing passenger edge labels: {len(missing_pel)}")
    for r in resource_labels:
        if r.get("transition_id") not in transition_ids:
            errors.append(f"resource label references unknown transition {r.get('transition_id')}")
        if r.get("missing") and r.get("value") is not None:
            warnings.append(f"resource label {r.get('transition_id')} marks missing with non-null value")
    for l in passenger_edge_labels:
        if l.get("y_e_p") and l.get("failed_resources"):
            errors.append(f"passenger edge label feasible despite failed resources: {l.get('transition_id')} {l.get('passenger_id')}")
        if l.get("y_e_p") and any(float(v) < 0 for v in (l.get("margins") or {}).values()):
            errors.append(f"passenger edge label feasible despite negative margin: {l.get('transition_id')} {l.get('passenger_id')}")
    for pair in pairs:
        if pair.get("episode_id") not in episode_ids:
            errors.append(f"counterfactual pair references unknown episode {pair.get('episode_id')}")
        if pair.get("weak_passenger_id") not in passenger_ids or pair.get("strict_passenger_id") not in passenger_ids:
            errors.append(f"counterfactual pair references unknown passengers {pair}")
        if pair.get("weak_passenger_id", "").split(":p")[0] != pair.get("strict_passenger_id", "").split(":p")[0]:
            errors.append(f"counterfactual pair crosses episodes {pair}")
    result = {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "num_episodes": len(episodes),
        "num_contracts": len(contracts),
        "num_transitions": len(transitions),
        "num_passenger_edge_labels": len(passenger_edge_labels),
    }
    if strict and errors:
        raise ValueError("dataset validation failed:\n" + "\n".join(errors))
    return result
