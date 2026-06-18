#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path as _Path
from typing import Any, Dict, Iterable, List

sys.path.insert(0, str(_Path(__file__).resolve().parents[1]))
from capplan.utils.serialization import dump_json, read_jsonl


def _safe_read(path: _Path) -> List[Dict[str, Any]]:
    return read_jsonl(path) if path.exists() else []


def _safe_json(path: _Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def _rate(num: int | float, den: int | float) -> float:
    return float(num) / float(den) if den else 0.0


def _q(values: Iterable[float]) -> Dict[str, Any]:
    vals = sorted(float(v) for v in values if v is not None)
    if not vals:
        return {"count": 0, "min": None, "p50": None, "p90": None, "max": None}
    def quantile(p: float) -> float:
        if len(vals) == 1:
            return vals[0]
        idx = p * (len(vals) - 1)
        lo = int(idx)
        hi = min(lo + 1, len(vals) - 1)
        w = idx - lo
        return vals[lo] * (1 - w) + vals[hi] * w
    return {"count": len(vals), "min": vals[0], "p50": quantile(0.5), "p90": quantile(0.9), "max": vals[-1]}




def _graph_topology(nodes: List[Dict[str, Any]], edges: List[Dict[str, Any]]) -> Dict[str, Any]:
    node_ids = {str(n.get('node_id')) for n in nodes}
    adj: Dict[str, set[str]] = {nid: set() for nid in node_ids}
    dangling = 0
    total_len = 0.0
    missing_width = missing_slope = missing_conf = 0
    for e in edges:
        u, v = str(e.get('from_node')), str(e.get('to_node'))
        if u not in node_ids or v not in node_ids:
            dangling += 1
            continue
        adj.setdefault(u, set()).add(v)
        try:
            total_len += float(e.get('length_m') or 0.0)
        except Exception:
            pass
        if e.get('width_m') is None:
            missing_width += 1
        if e.get('slope') is None:
            missing_slope += 1
        if e.get('confidence') is None:
            missing_conf += 1
    seen: set[str] = set()
    comps = []
    for nid in node_ids:
        if nid in seen:
            continue
        stack = [nid]
        seen.add(nid)
        c = 0
        while stack:
            x = stack.pop(); c += 1
            for y in adj.get(x, set()):
                if y not in seen:
                    seen.add(y); stack.append(y)
            # weak connectivity: add reverse traversal without building a second graph
            for z, outs in adj.items():
                if x in outs and z not in seen:
                    seen.add(z); stack.append(z)
        comps.append(c)
    node_kinds = Counter(str(n.get('kind')) for n in nodes)
    edge_types = Counter(str(e.get('crossing_type') or e.get('edge_type') or 'path') for e in edges)
    n_edges = max(1, len(edges))
    return {
        'nodes': len(nodes),
        'edges': len(edges),
        'weak_components': len(comps),
        'largest_component_nodes': max(comps) if comps else 0,
        'largest_component_fraction': _rate(max(comps) if comps else 0, len(nodes)),
        'dangling_edge_refs': dangling,
        'total_edge_length_m': total_len,
        'node_kinds': dict(node_kinds.most_common()),
        'edge_types': dict(edge_types.most_common()),
        'missing_width_rate': missing_width / n_edges,
        'missing_slope_rate': missing_slope / n_edges,
        'missing_confidence_rate': missing_conf / n_edges,
    }


def _read_external_jsonl(path: str | _Path | None) -> List[Dict[str, Any]]:
    if not path:
        return []
    p = _Path(path)
    return read_jsonl(p) if p.exists() else []

def diagnose_dataset(dataset_dir: str | _Path, eval_dir: str | _Path | None = None, audit_json: str | _Path | None = None, accessibility_graph_dir: str | _Path | None = None, service_requests_jsonl: str | _Path | None = None, pudo_evidence_jsonl: str | _Path | None = None) -> Dict[str, Any]:
    root = _Path(dataset_dir)
    episodes = _safe_read(root / 'episodes.jsonl')
    transitions = _safe_read(root / 'candidate_transitions.jsonl')
    passenger_labels = _safe_read(root / 'passenger_edge_labels.jsonl')
    transition_labels = _safe_read(root / 'transition_labels.jsonl')
    contracts = _safe_read(root / 'capability_contracts.jsonl')
    skeletons = _safe_read(root / 'skeleton_labels.jsonl')
    certificates = _safe_read(root / 'certificate_labels.jsonl')
    resources = _safe_read(root / 'resource_labels.jsonl')
    pudos = _safe_read(root / 'pudo_anchors.jsonl')
    entrances = _safe_read(root / 'entrances.jsonl')
    service_request_records = _safe_read(root / 'service_requests.jsonl')
    manifest = _safe_json(root / 'dataset_manifest.json')

    by_ep_edges: Counter[str] = Counter()
    by_action: Counter[str] = Counter()
    for t in transitions:
        by_ep_edges[str(t.get('episode_id'))] += 1
        by_action[str(t.get('action'))] += 1

    graph_node_counts: List[int] = []
    graph_edge_counts: List[int] = []
    graph_sources: Counter[str] = Counter()
    graph_topologies: Dict[str, Any] = {}
    graph_dir = _Path(accessibility_graph_dir) if accessibility_graph_dir else root / 'accessibility_graphs'
    for ep in episodes:
        eid = ep.get('episode_id')
        nodes = _safe_read(graph_dir / f'{eid}.nodes.jsonl')
        edges = _safe_read(graph_dir / f'{eid}.edges.jsonl')
        graph_node_counts.append(len(nodes))
        graph_edge_counts.append(len(edges))
        graph_sources.update(str(e.get('source')) for e in edges)
        if nodes or edges:
            graph_topologies[str(eid)] = _graph_topology(nodes, edges)

    p_true = sum(1 for r in passenger_labels if r.get('y_e_p'))
    z_true = sum(1 for r in transition_labels if r.get('z_e'))
    passenger_by_idx: Counter[str] = Counter()
    passenger_by_idx_true: Counter[str] = Counter()
    failed_resources: Counter[str] = Counter()
    for r in passenger_labels:
        pid = str(r.get('passenger_id', ''))
        idx = pid.split(':')[-1] if ':' in pid else pid
        passenger_by_idx[idx] += 1
        if r.get('y_e_p'):
            passenger_by_idx_true[idx] += 1
        failed_resources.update(r.get('failed_resources') or [])

    skeleton_by_idx: Counter[str] = Counter()
    for r in skeletons:
        pid = str(r.get('passenger_id', ''))
        skeleton_by_idx[pid.split(':')[-1] if ':' in pid else pid] += 1

    cert_by_phase: Counter[str] = Counter()
    cert_by_resource: Counter[str] = Counter()
    cert_sources: Counter[str] = Counter()
    for r in certificates:
        cert_by_phase[str(r.get('phase'))] += 1
        cert_by_resource.update([str(r.get('resource_type'))])
        cert_sources.update([str(r.get('evidence_source'))])
        for ev in r.get('violations') or r.get('evidence') or []:
            if isinstance(ev, dict):
                cert_by_resource.update([str(ev.get('resource_type', ev.get('resource_name')))])
                cert_sources.update([str(ev.get('evidence_source', ev.get('source')))])

    eval_summary: Dict[str, Any] = {}
    if eval_dir is not None:
        eroot = _Path(eval_dir)
        em = _safe_read(eroot / 'episode_metrics.jsonl')
        if em:
            denom: Counter[str] = Counter()
            ok: Counter[str] = Counter()
            for r in em:
                pid = str(r.get('passenger_id', ''))
                idx = pid.split(':')[-1] if ':' in pid else pid
                denom[idx] += 1
                if r.get('passenger_complete'):
                    ok[idx] += 1
            eval_summary = {
                'num_rows': len(em),
                'passenger_complete_rate': _rate(sum(1 for r in em if r.get('passenger_complete')), len(em)),
                'traffic_safe_rate': _rate(sum(1 for r in em if r.get('traffic_safe')), len(em)),
                'route_completion': _q(float(r.get('route_completion', 0.0)) for r in em),
                'completion_by_passenger_index': {k: {'ok': ok[k], 'total': denom[k], 'rate': _rate(ok[k], denom[k])} for k in sorted(denom)},
                'failure_phase': dict(Counter(str(r.get('failure_phase') or (r.get('certificate') or {}).get('phase') or (r.get('oracle_certificate') or {}).get('phase')) for r in em if not r.get('passenger_complete')).most_common()),
                'failure_resource': dict(Counter(str(r.get('failure_resource') or (r.get('certificate') or {}).get('resource_type') or (r.get('oracle_certificate') or {}).get('resource_type')) for r in em if not r.get('passenger_complete')).most_common()),
            }
        metrics = _safe_json(eroot / 'metrics.json')
        if metrics:
            eval_summary['aggregate_metrics'] = metrics

    audit = _safe_json(_Path(audit_json)) if audit_json else {}
    external_service_requests = _read_external_jsonl(service_requests_jsonl)
    external_pudo_evidence = _read_external_jsonl(pudo_evidence_jsonl)

    contract_counts_by_episode: Counter[str] = Counter()
    for c in contracts:
        pid = str(c.get('passenger_id', ''))
        eid = pid.split(':p')[0] if ':p' in pid else str(c.get('episode_id', 'unknown'))
        contract_counts_by_episode[eid] += 1

    service_profile_counts = Counter(str(r.get('passenger_profile_id')) for r in (service_request_records if 'service_request_records' in locals() else []))
    if external_service_requests:
        service_profile_counts.update(str(r.get('passenger_profile_id')) for r in external_service_requests)
    service_episode_counts = Counter(str(r.get('episode_id')) for r in service_request_records)
    if external_service_requests:
        service_episode_counts.update(str(r.get('episode_id')) for r in external_service_requests)
    pudo_legal = sum(1 for p in pudos if p.get('legal_stop'))
    if external_pudo_evidence:
        pudo_legal += sum(1 for p in external_pudo_evidence if p.get('legal_stop'))

    warnings: List[str] = []
    if episodes and len(episodes) < 1000:
        warnings.append('dataset_episode_count_below_paper_scale')
    if graph_node_counts and max(graph_node_counts) <= 20:
        warnings.append('accessibility_graph_is_toy_sized')
    if passenger_labels and _rate(p_true, len(passenger_labels)) < 0.10:
        warnings.append('passenger_feasible_edge_rate_too_low_for_training')
    if contracts and _rate(len(skeletons), len(contracts)) < 0.20:
        warnings.append('passenger_complete_positive_rate_too_low')
    if any('proxy' in str(e.get('source', '')) for e in entrances):
        warnings.append('proxy_service_entrances_present')
    if any('synthetic' in s for s in graph_sources):
        warnings.append('synthetic_accessibility_edges_present')
    if any(p.get('curb_height_m') is None for p in pudos):
        warnings.append('pudo_curb_height_missing')
    if graph_topologies:
        if any(v.get('largest_component_fraction', 0.0) < 0.70 for v in graph_topologies.values()):
            warnings.append('accessibility_graph_fragmented_largest_component_below_70pct')
        if any(v.get('dangling_edge_refs', 0) > 0 for v in graph_topologies.values()):
            warnings.append('accessibility_graph_has_dangling_edge_references')
        if any(v.get('node_kinds', {}).get('entrance', 0) < 2 for v in graph_topologies.values()):
            warnings.append('episode_has_fewer_than_two_real_entrances')
        if any((v.get('node_kinds', {}).get('curb', 0) + v.get('node_kinds', {}).get('curb_ramp', 0)) == 0 for v in graph_topologies.values()):
            warnings.append('episode_has_no_curb_or_curb_ramp_nodes')
    if service_profile_counts and len(service_profile_counts) < 3:
        warnings.append('service_requests_do_not_cover_all_three_passenger_layers')
    if pudos and _rate(pudo_legal, len(pudos) + len(external_pudo_evidence)) < 0.05:
        warnings.append('very_low_legal_pudo_rate_check_curb_regulation_alignment')

    readiness = 'paper_ready_small_sample'
    blocking = [w for w in warnings if w not in {'dataset_episode_count_below_paper_scale'}]
    if blocking:
        readiness = 'not_ready_needs_data_or_alignment_fix'
    elif len(episodes) < 1000:
        readiness = 'small_sample_sanity_only_not_paper_scale'

    return {
        'dataset_dir': str(root),
        'manifest': manifest,
        'counts': {
            'episodes': len(episodes),
            'contracts': len(contracts),
            'transitions': len(transitions),
            'transition_labels': len(transition_labels),
            'passenger_edge_labels': len(passenger_labels),
            'skeleton_labels': len(skeletons),
            'certificate_labels': len(certificates),
            'resource_labels': len(resources),
            'pudos': len(pudos),
            'entrances': len(entrances),
        },
        'graph_scale': {
            'nodes_per_episode': _q(graph_node_counts),
            'edges_per_episode': _q(graph_edge_counts),
            'edge_sources': dict(graph_sources.most_common()),
            'topology_by_episode': graph_topologies,
        },
        'transition_scale': {
            'transitions_per_episode': _q(by_ep_edges.values()),
            'actions': dict(by_action.most_common()),
            'z_true_rate': _rate(z_true, len(transition_labels)),
        },
        'passenger_label_health': {
            'y_true': p_true,
            'y_false': len(passenger_labels) - p_true,
            'y_true_rate': _rate(p_true, len(passenger_labels)),
            'by_passenger_index': {k: {'true': passenger_by_idx_true[k], 'total': passenger_by_idx[k], 'rate': _rate(passenger_by_idx_true[k], passenger_by_idx[k])} for k in sorted(passenger_by_idx)},
            'failed_resources_top30': dict(failed_resources.most_common(30)),
        },
        'passenger_complete_health': {
            'skeleton_count': len(skeletons),
            'contracts': len(contracts),
            'skeleton_rate_over_contracts': _rate(len(skeletons), len(contracts)),
            'skeleton_by_passenger_index': dict(skeleton_by_idx.most_common()),
            'certificate_failed_phase': dict(cert_by_phase.most_common()),
            'certificate_resource_top30': dict(cert_by_resource.most_common(30)),
            'certificate_source_top30': dict(cert_sources.most_common(30)),
        },
        'contract_distribution': {'contracts_per_episode': _q(contract_counts_by_episode.values())},
        'pudo_missingness': {
            'curb_height_m': sum(1 for p in pudos if p.get('curb_height_m') is None),
            'sidewalk_width_m': sum(1 for p in pudos if p.get('sidewalk_width_m') is None),
            'deployment_clearance_m': sum(1 for p in pudos if p.get('deployment_clearance_m') is None),
        },
        'pudo_health': {
            'legal_stop_rate': _rate(pudo_legal, len(pudos) + len(external_pudo_evidence)),
            'sources': dict(Counter(str(p.get('source')) for p in pudos + external_pudo_evidence).most_common()),
            'external_evidence_rows': len(external_pudo_evidence),
        },
        'service_layer_health': {
            'service_requests': len(service_request_records),
            'external_service_requests': len(external_service_requests),
            'profile_distribution': dict(service_profile_counts.most_common()),
            'requests_per_episode': _q(service_episode_counts.values()),
        },
        'diagnostic_readiness': readiness,
        'eval_summary': eval_summary,
        'audit_publication_readiness': audit.get('publication_readiness', {}),
        'warnings': warnings,
    }


def main() -> None:
    p = argparse.ArgumentParser(description='Diagnose whether a CapPlan dataset/eval is paper-scale and passenger-conditioned.')
    p.add_argument('--dataset_dir', required=True)
    p.add_argument('--eval_dir', default=None)
    p.add_argument('--audit_json', default=None)
    p.add_argument('--accessibility_graph_dir', default=None, help='Optional external graph dir to diagnose before/alongside a built dataset.')
    p.add_argument('--service_requests_jsonl', default=None, help='Optional external service requests JSONL to check profile/episode coverage.')
    p.add_argument('--pudo_evidence_jsonl', default=None, help='Optional external PUDO evidence JSONL to check legal-stop/source/missingness before final build.')
    p.add_argument('--output', default=None)
    args = p.parse_args()
    report = diagnose_dataset(args.dataset_dir, args.eval_dir, args.audit_json, args.accessibility_graph_dir, args.service_requests_jsonl, args.pudo_evidence_jsonl)
    print(json.dumps(report, indent=2, sort_keys=True))
    if args.output:
        dump_json(args.output, report)


if __name__ == '__main__':
    main()
