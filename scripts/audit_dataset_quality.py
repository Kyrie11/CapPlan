#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path as _Path
from typing import Any, Dict, Iterable, List

sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))

from capplan.utils.serialization import dump_json, read_jsonl


def _safe_read(path: _Path) -> List[Dict[str, Any]]:
    return read_jsonl(path) if path.exists() else []


def _counter(rows: Iterable[Dict[str, Any]], key: str) -> Dict[str, int]:
    return dict(Counter(str(r.get(key)) for r in rows))


def _quantiles(values: List[float]) -> Dict[str, float | None]:
    vals = sorted(float(v) for v in values if v is not None)
    if not vals:
        return {"count": 0, "min": None, "p50": None, "p90": None, "max": None}
    def q(p: float) -> float:
        if len(vals) == 1:
            return vals[0]
        idx = p * (len(vals) - 1)
        lo = int(idx)
        hi = min(lo + 1, len(vals) - 1)
        w = idx - lo
        return vals[lo] * (1 - w) + vals[hi] * w
    return {"count": len(vals), "min": vals[0], "p50": q(0.50), "p90": q(0.90), "max": vals[-1]}


def _resource_values(resources: List[Dict[str, Any]], name: str) -> List[float]:
    vals = []
    for r in resources:
        if r.get("resource_name") == name and not r.get("missing") and isinstance(r.get("value"), (int, float)):
            vals.append(float(r["value"]))
    return vals


def _bad_source(value: Any) -> bool:
    s = str(value or "").lower()
    return s.startswith("synthetic") or "proxy" in s or s in {"toy", "mock"}


def _rate(count: int, total: int) -> float:
    return float(count) / max(1, int(total))


def audit_dataset(dataset_dir: str | _Path, paper_mode: bool = False, min_graph_nodes: int = 100, min_graph_edges: int = 150, max_core_pudo_missing_rate: float = 1.0, min_edge_positive_rate: float = 0.10, min_skeleton_positive_rate: float = 0.10, min_paper_eligible_pudos_per_episode: int = 2, min_episode_pudo_coverage_rate: float = 0.80, min_failure_phase_diversity: int = 2) -> Dict[str, Any]:
    root = _Path(dataset_dir)
    episodes = _safe_read(root / "episodes.jsonl")
    scenes = _safe_read(root / "scenes.jsonl")
    entrances = _safe_read(root / "entrances.jsonl")
    pudos = _safe_read(root / "pudo_anchors.jsonl")
    transitions = _safe_read(root / "candidate_transitions.jsonl")
    transition_labels = _safe_read(root / "transition_labels.jsonl")
    passenger_labels = _safe_read(root / "passenger_edge_labels.jsonl")
    resources = _safe_read(root / "resource_labels.jsonl")
    skeletons = _safe_read(root / "skeleton_labels.jsonl")
    certificates = _safe_read(root / "certificate_labels.jsonl")
    validation = json.loads((root / "validation_report.json").read_text()) if (root / "validation_report.json").exists() else {}
    manifest = json.loads((root / "dataset_manifest.json").read_text()) if (root / "dataset_manifest.json").exists() else {}
    service_requests = _safe_read(root / "service_requests.jsonl")

    graph_node_counts: List[int] = []
    graph_edge_counts: List[int] = []
    graph_edge_sources: Counter[str] = Counter()
    graph_metadata_sources: Counter[str] = Counter()
    graph_dir = root / "accessibility_graphs"
    for ep in episodes:
        eid = ep.get("episode_id")
        nodes = _safe_read(graph_dir / f"{eid}.nodes.jsonl")
        edges = _safe_read(graph_dir / f"{eid}.edges.jsonl")
        graph_node_counts.append(len(nodes))
        graph_edge_counts.append(len(edges))
        graph_edge_sources.update(str(e.get("source")) for e in edges)
        meta_path = graph_dir / f"{eid}.jsonl"
        if meta_path.exists():
            for row in _safe_read(meta_path):
                if isinstance(row, dict) and row.get("metadata"):
                    graph_metadata_sources[str(row.get("metadata", {}).get("source"))] += 1

    transition_z_by_action: Dict[str, Counter[str]] = defaultdict(Counter)
    tid_to_action = {t.get("transition_id"): t.get("action") for t in transitions}
    for lbl in transition_labels:
        action = str(tid_to_action.get(lbl.get("transition_id"), "unknown"))
        transition_z_by_action[action]["z_true" if lbl.get("z_e") else "z_false"] += 1

    resource_missing = Counter(r.get("resource_name") for r in resources if r.get("missing"))
    missing_with_nonnull = [r for r in resources if r.get("missing") and r.get("value") is not None]
    fabricated_clearance = [
        p for p in pudos
        if str(p.get("source", "")).startswith("nuplan_route")
        and p.get("deployment_clearance_m") is not None
        and p.get("sidewalk_width_m") is None
    ]

    failed_resources = Counter()
    for row in passenger_labels:
        failed_resources.update(row.get("failed_resources") or [])

    passenger_true = sum(1 for r in passenger_labels if r.get("y_e_p"))
    passenger_false = sum(1 for r in passenger_labels if not r.get("y_e_p"))
    transition_true = sum(1 for r in transition_labels if r.get("z_e"))
    transition_false = sum(1 for r in transition_labels if not r.get("z_e"))
    passenger_true_rate = passenger_true / max(1, passenger_true + passenger_false)
    skeleton_rate = len(skeletons) / max(1, len(certificates) + len(skeletons))

    pudo_by_episode: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in pudos:
        pudo_by_episode[str(row.get("episode_id") or "unknown")].append(row)

    def _pudo_complete(row: Dict[str, Any]) -> bool:
        if row.get("paper_evidence_complete") is not None:
            return bool(row.get("paper_evidence_complete"))
        core = all(row.get(k) is not None for k in ("curb_height_m", "deployment_clearance_m", "sidewalk_width_m"))
        legality = str(row.get("legal_stop_source") or "").lower()
        independent = bool(legality) and "no_matching_regulation" not in legality and "heuristic" not in legality and "no_legality_evidence" not in legality
        return core and bool(row.get("adjacent_ped_node_id")) and independent and not _bad_source(row.get("source"))

    def _pudo_eligible(row: Dict[str, Any]) -> bool:
        if row.get("paper_eligible") is not None:
            return bool(row.get("paper_eligible"))
        return _pudo_complete(row) and bool(row.get("legal_stop"))

    eligible_by_episode = {eid: sum(1 for row in rows if _pudo_eligible(row)) for eid, rows in pudo_by_episode.items()}
    complete_by_episode = {eid: sum(1 for row in rows if _pudo_complete(row)) for eid, rows in pudo_by_episode.items()}
    episode_ids = [str(ep.get("episode_id")) for ep in episodes]
    episodes_meeting_pudo_gate = sum(1 for eid in episode_ids if eligible_by_episode.get(eid, 0) >= min_paper_eligible_pudos_per_episode)
    episode_pudo_coverage_rate = _rate(episodes_meeting_pudo_gate, len(episode_ids))
    certificate_phase_counts = Counter(str(c.get("phase") or "unknown") for c in certificates)
    certificate_phase_diversity = len([k for k, v in certificate_phase_counts.items() if k != "unknown" and v > 0])

    issues: List[str] = []
    if validation.get("ok") is False:
        issues.append("schema_validation_failed")
    if len(skeletons) == 0:
        issues.append("no_passenger_complete_skeletons")
    if len(certificates) == 0:
        issues.append("no_failure_certificates")
    if passenger_labels and passenger_true == 0:
        issues.append("no_passenger_feasible_edges")
    elif passenger_labels and passenger_true_rate < 0.05:
        issues.append("passenger_feasible_edges_too_sparse")
    if (certificates or skeletons) and skeleton_rate < 0.05:
        issues.append("oracle_passenger_complete_skeletons_too_sparse")
    if transition_labels and transition_true == 0:
        issues.append("no_transition_valid_edges")
    if fabricated_clearance:
        issues.append("route_pudo_clearance_without_sidewalk_width")
    if any("proxy" in str(e.get("source", "")) for e in entrances):
        issues.append("proxy_entrances_used")
    if any("synthetic" in str(src) for src in graph_edge_sources):
        issues.append("synthetic_accessibility_edges_used")

    if manifest.get("source_policy") != "paper":
        issues.append("source_policy_not_paper")
    if not manifest.get("paper_mode"):
        issues.append("dataset_not_built_in_paper_mode")
    preflight = manifest.get("external_source_preflight") if isinstance(manifest.get("external_source_preflight"), dict) else {}
    missing_external = []
    if preflight:
        if isinstance(preflight.get("cities"), list):
            missing_external = list(preflight.get("blockers", []))
            if not preflight.get("publication_ready", False):
                issues.append("external_sources_not_publication_ready")
        else:
            # Backward compatibility with v1 existence-only preflight reports.
            missing_external = [f"{r.get('city')}:{r.get('key')}" for r in preflight.get("sources", []) if not r.get("exists") and r.get("key") != "georeference_json"]
        if missing_external:
            issues.append("missing_real_external_sources")

    synthetic_sources = sorted({str(x) for x in list(graph_edge_sources) + [e.get("source") for e in entrances] + [p.get("source") for p in pudos] if _bad_source(x)})
    proxy_sources = sorted({str(x) for x in list(graph_edge_sources) + [e.get("source") for e in entrances] + [p.get("source") for p in pudos] if "proxy" in str(x).lower()})
    unknown_sources = sorted({str(x) for x in list(graph_edge_sources) + [e.get("source") for e in entrances] + [p.get("source") for p in pudos] if str(x) in {"", "None", "unknown"}})

    pudo_missing_rates = {
        "sidewalk_width_m": _rate(sum(1 for p in pudos if p.get("sidewalk_width_m") is None), len(pudos)),
        "deployment_clearance_m": _rate(sum(1 for p in pudos if p.get("deployment_clearance_m") is None), len(pudos)),
        "curb_height_m": _rate(sum(1 for p in pudos if p.get("curb_height_m") is None), len(pudos)),
    }
    if paper_mode:
        if manifest.get("scene_source") != "nuplan": issues.append("paper_mode_requires_nuplan_scene_source")
        if manifest.get("accessibility_source") in {"synthetic", "synthetic_local"}: issues.append("paper_mode_rejects_synthetic_accessibility_source")
        if manifest.get("service_layer_source") in {None, "synthetic_smoke"}: issues.append("paper_mode_rejects_synthetic_service_layer")
        if synthetic_sources: issues.append("paper_mode_synthetic_or_proxy_sources_present")
        if graph_node_counts and min(graph_node_counts) < min_graph_nodes: issues.append("paper_mode_graph_nodes_too_few")
        if graph_edge_counts and min(graph_edge_counts) < min_graph_edges: issues.append("paper_mode_graph_edges_too_few")
        # Missing values are allowed on retained uncertain candidates. Publication
        # readiness instead requires enough fully evidenced, legally usable PUDOs.
        if episode_pudo_coverage_rate < min_episode_pudo_coverage_rate:
            issues.append("paper_mode_insufficient_episode_pudo_evidence_coverage")
        if passenger_labels and passenger_true_rate < min_edge_positive_rate: issues.append("paper_mode_passenger_edge_positive_rate_too_low")
        if (certificates or skeletons) and skeleton_rate < min_skeleton_positive_rate: issues.append("paper_mode_skeleton_positive_rate_too_low")
        if certificate_phase_diversity < min_failure_phase_diversity:
            issues.append("paper_mode_failure_certificate_phase_diversity_too_low")

    blocking_issues = sorted(set(issues)) if paper_mode else sorted(set(issues))
    warnings = []
    if not service_requests and manifest.get("service_layer_source") in {"real_jsonl", "calibrated_od"}:
        warnings.append("service_requests_jsonl_not_copied_into_dataset")

    report = {
        "dataset_dir": str(root),
        "manifest": {
            "scene_source": manifest.get("scene_source"),
            "accessibility_source": manifest.get("accessibility_source"),
            "pudo_source": manifest.get("pudo_source"),
            "num_episodes": manifest.get("num_episodes"),
            "num_contracts": manifest.get("num_contracts"),
            "num_transitions": manifest.get("num_transitions"),
            "source_policy": manifest.get("source_policy"),
            "paper_mode": manifest.get("paper_mode"),
            "publication_ready": manifest.get("publication_ready"),
        },
        "validation": {
            "ok": validation.get("ok"),
            "num_errors": len(validation.get("errors", [])),
            "num_warnings": len(validation.get("warnings", [])),
            "first_errors": validation.get("errors", [])[:10],
            "first_warnings": validation.get("warnings", [])[:10],
        },
        "counts": {
            "episodes": len(episodes),
            "scenes": len(scenes),
            "entrances": len(entrances),
            "pudos": len(pudos),
            "transitions": len(transitions),
            "transition_labels": len(transition_labels),
            "passenger_edge_labels": len(passenger_labels),
            "resource_labels": len(resources),
            "skeleton_labels": len(skeletons),
            "certificate_labels": len(certificates),
            "service_requests": len(service_requests),
        },
        "provenance": {
            "scene_sources": _counter(scenes, "source"),
            "entrance_sources": _counter(entrances, "source"),
            "pudo_sources": _counter(pudos, "source"),
            "graph_edge_sources": dict(graph_edge_sources),
            "graph_metadata_sources": dict(graph_metadata_sources),
        },
        "geometry": {
            "nodes_per_episode": _quantiles([float(x) for x in graph_node_counts]),
            "edges_per_episode": _quantiles([float(x) for x in graph_edge_counts]),
            "access_distance_m": _quantiles(_resource_values(resources, "access_distance_m")),
            "egress_distance_m": _quantiles(_resource_values(resources, "egress_distance_m")),
        },
        "missingness": {
            "pudo_missing_sidewalk_width": sum(1 for p in pudos if p.get("sidewalk_width_m") is None),
            "pudo_missing_deployment_clearance": sum(1 for p in pudos if p.get("deployment_clearance_m") is None),
            "pudo_missing_curb_height": sum(1 for p in pudos if p.get("curb_height_m") is None),
            "pudo_missing_lighting": sum(1 for p in pudos if p.get("lighting") is None),
            "pudo_missing_shelter": sum(1 for p in pudos if p.get("shelter") is None),
            "resource_missing_by_name": dict(resource_missing.most_common()),
            "missing_with_nonnull_value": len(missing_with_nonnull),
        },
        "pudo_evidence_readiness": {
            "paper_eligible_total": sum(eligible_by_episode.values()),
            "paper_evidence_complete_total": sum(complete_by_episode.values()),
            "paper_eligible_by_episode": eligible_by_episode,
            "paper_evidence_complete_by_episode": complete_by_episode,
            "min_required_eligible_per_episode": min_paper_eligible_pudos_per_episode if paper_mode else None,
            "episodes_meeting_gate": episodes_meeting_pudo_gate,
            "episode_coverage_rate": episode_pudo_coverage_rate,
            "min_required_episode_coverage_rate": min_episode_pudo_coverage_rate if paper_mode else None,
            "note": "Missing fields on uncertain candidates are permitted; only evidence-complete legal interfaces count as paper_eligible.",
        },
        "label_health": {
            "transition_z_by_action": {k: dict(v) for k, v in sorted(transition_z_by_action.items())},
            "transition_z_true": transition_true,
            "transition_z_false": transition_false,
            "transition_z_true_rate": transition_true / max(1, transition_true + transition_false),
            "passenger_y_true": passenger_true,
            "passenger_y_false": passenger_false,
            "passenger_y_true_rate": passenger_true_rate,
            "skeleton_label_count": len(skeletons),
            "oracle_skeleton_rate": skeleton_rate,
            "failed_resources": dict(failed_resources.most_common(30)),
            "certificate_phase_counts": dict(certificate_phase_counts),
            "certificate_phase_diversity": certificate_phase_diversity,
        },
        "truthfulness_flags": {
            "uses_proxy_entrances": any("proxy" in str(e.get("source", "")) for e in entrances),
            "uses_synthetic_accessibility_edges": any("synthetic" in str(src) for src in graph_edge_sources),
            "route_pudo_clearance_without_width_count": len(fabricated_clearance),
            "route_pudo_clearance_without_width_examples": [p.get("anchor_id") for p in fabricated_clearance[:10]],
        },
        "source_integrity": {
            "synthetic_sources": synthetic_sources,
            "proxy_sources": proxy_sources,
            "unknown_sources": unknown_sources,
            "missing_external_sources": missing_external,
        },
        "graph_quality": {
            "nodes_per_episode": _quantiles([float(x) for x in graph_node_counts]),
            "edges_per_episode": _quantiles([float(x) for x in graph_edge_counts]),
            "min_required_nodes": min_graph_nodes if paper_mode else None,
            "min_required_edges": min_graph_edges if paper_mode else None,
            "entrance_snap_success_rate": 1.0 if entrances else 0.0,
            "pudo_connector_success_rate": sum(1 for p in pudos if p.get("adjacent_ped_node_id")) / max(1, len(pudos)),
        },
        "evidence_missingness": {
            "curb_height_m": pudo_missing_rates["curb_height_m"],
            "deployment_clearance_m": pudo_missing_rates["deployment_clearance_m"],
            "sidewalk_width_m": pudo_missing_rates["sidewalk_width_m"],
            "slope": _rate(resource_missing.get("slope", 0), len(resources)),
            "cross_slope": _rate(resource_missing.get("cross_slope", 0), len(resources)),
        },
        "publication_readiness": {
            "status": "PASS" if len(blocking_issues) == 0 else "FAIL",
            "ready_for_main_results": len(blocking_issues) == 0,
            "issues": blocking_issues,
            "blocking_issues": blocking_issues,
            "warnings": warnings,
            "paper_mode": bool(paper_mode),
            "note": "Proxy/synthetic evidence can support smoke or ablation experiments only if it is disclosed separately from real accessibility-map results.",
        },
    }
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit CapPlan dataset quality/provenance beyond schema validation.")
    parser.add_argument("--dataset_dir", required=True)
    parser.add_argument("--output", default=None)
    parser.add_argument("--paper_mode", action="store_true")
    parser.add_argument("--fail_if_not_publication_ready", action="store_true")
    parser.add_argument("--min_graph_nodes", type=int, default=100)
    parser.add_argument("--min_graph_edges", type=int, default=150)
    parser.add_argument("--max_core_pudo_missing_rate", type=float, default=1.0, help="Deprecated compatibility flag; candidate missingness no longer blocks paper mode.")
    parser.add_argument("--min_paper_eligible_pudos_per_episode", type=int, default=2)
    parser.add_argument("--min_episode_pudo_coverage_rate", type=float, default=0.80)
    parser.add_argument("--min_failure_phase_diversity", type=int, default=2)
    parser.add_argument("--min_edge_positive_rate", type=float, default=0.10)
    parser.add_argument("--min_skeleton_positive_rate", type=float, default=0.10)
    args = parser.parse_args()
    report = audit_dataset(
        args.dataset_dir, paper_mode=args.paper_mode, min_graph_nodes=args.min_graph_nodes, min_graph_edges=args.min_graph_edges,
        max_core_pudo_missing_rate=args.max_core_pudo_missing_rate, min_edge_positive_rate=args.min_edge_positive_rate,
        min_skeleton_positive_rate=args.min_skeleton_positive_rate, min_paper_eligible_pudos_per_episode=args.min_paper_eligible_pudos_per_episode,
        min_episode_pudo_coverage_rate=args.min_episode_pudo_coverage_rate, min_failure_phase_diversity=args.min_failure_phase_diversity,
    )
    text = json.dumps(report, indent=2, sort_keys=True)
    print(text)
    ready = bool(report.get("publication_readiness", {}).get("ready_for_main_results", False))
    print(f"ABILITYBENCH_DATASET_CHECK={'PASS' if ready else 'FAIL'}")
    if args.output:
        dump_json(args.output, report)
    if args.fail_if_not_publication_ready and not ready:
        raise SystemExit("dataset is not publication-ready: " + ", ".join(report.get("publication_readiness", {}).get("blocking_issues", [])))


if __name__ == "__main__":
    main()
