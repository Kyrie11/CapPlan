"""Non-destructive bridge from encoded pedestrian geometry to controlled handoff tests.

The key separation is intentional:
  GEO: routed path in a serialized (potentially semantically incomplete) graph.
  CONTROL: deliberately constructed clocks, vehicle windows and evidence expiry.
  REAL: independently attested physical curb and passenger boarding; NEVER inferred here.

Do not use any result of this module as a real-world incidence estimate.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
import hashlib
import json
import math

from capplan.handoff.research import _atomic_json, _read_jsonl, _digest_file
from capplan.handoff.baselines import compare_case


def _city(label: Any) -> str:
    text = str(label or '').lower()
    for city, aliases in {
        'boston': ('boston',), 'pittsburgh': ('pittsburgh', 'hazelwood'),
        'vegas': ('vegas', 'las-vegas'), 'singapore': ('singapore', 'one-north')
    }.items():
        if any(alias in text for alias in aliases):
            return city
    return 'unknown'


def _known_provenance(value: Any) -> str:
    if isinstance(value, dict):
        kind = value.get('kind', value.get('source_kind', value.get('evidence_kind')))
        return str(kind).lower() if kind else 'unknown'
    return 'unknown'


def _physical_site(anchor: dict) -> str | None:
    return anchor.get('hybrid_physical_site_key') or anchor.get('physical_site_key')


def _bounded_anchors(dataset: Path, interest: set[tuple[str, str]]) -> dict[tuple[str, str], dict]:
    """Stream one existing PUDO file; only store requested episode/anchor rows."""
    found = {}
    path = Path(dataset) / 'pudo_anchors.jsonl'
    if not path.exists():
        return found
    for record in _read_jsonl(path):
        key = (str(record.get('episode_id')), str(record.get('anchor_id')))
        if key in interest:
            found[key] = record
            if len(found) == len(interest):
                break
    return found


def _anchor_summary(row: dict | None) -> dict:
    if not row:
        return {'evidence_status': 'UNKNOWN_ANCHOR_NOT_AVAILABLE', 'real_physical_validated': False}
    fields = ('legal_stop', 'curb_ramp', 'curb_height_m', 'sidewalk_width_m',
              'deployment_clearance_m', 'blockage_risk', 'side')
    provenance = row.get('field_provenance') or {}
    return {
        'evidence_status': 'ATTRIBUTES_FROM_HYBRID_BENCHMARK_NOT_INDEPENDENT_CERTIFICATION',
        'physical_site_key': _physical_site(row),
        'adjacent_ped_node_id': row.get('adjacent_ped_node_id'),
        'stop_pose': row.get('stop_pose'), 'curb_pose': row.get('curb_pose'),
        'fields': {k: {'value': row.get(k), 'provenance_kind':
                        _known_provenance(provenance.get(k))} for k in fields},
        'real_physical_validated': False,
    }


def export_progress_packets(*, remine: Path, output: Path, pairs: Path | None = None,
                            dataset: Path | None = None, max_packets: int = 300) -> dict:
    """Export mid-route progress with VERIFIED *graph-encoding* distances only.

    The historical mining contract is used as a test threshold, never a factual
    estimate of an individual's safe walking/rolling limit.
    """
    if max_packets < 1:
        raise ValueError('max_packets must be positive')
    remine, output = Path(remine), Path(output)
    candidates = []
    seen = set()
    stats = Counter()
    for row in _read_jsonl(remine):
        if row.get('direction_mode') != 'AS_ENCODED':
            continue
        stats['as_encoded_rows'] += 1
        if row.get('status') != 'DIRECTED_TERMINAL_AB_ASYMMETRY_EXISTS':
            stats['excluded_not_two_option_asymmetry'] += 1
            continue
        # The same site may recur in multiple temporal episodes, which are
        # NOT independent physical sites. Keep original episode provenance.
        key = tuple(str(row.get(k)) for k in ('episode_id','a_anchor','b_anchor'))
        if key in seen:
            continue
        seen.add(key)
        candidates.append(row)
        if len(candidates) >= max_packets:
            break
    source_contracts = {}
    if pairs is not None:
        wanted = {tuple(str(r.get(k)) for k in ('episode_id','a_anchor','b_anchor')) for r in candidates}
        for row in _read_jsonl(Path(pairs)):
            key = tuple(str(row.get(k)) for k in ('episode_id','a_anchor','b_anchor'))
            if key in wanted and key not in source_contracts:
                source_contracts[key] = row.get('screening_contract')
            if len(source_contracts) == len(wanted):
                break
    interest = {(str(r['episode_id']),str(r[name])) for r in candidates for name in ('a_anchor','b_anchor')}
    anchors = _bounded_anchors(Path(dataset), interest) if dataset else {}
    output.parent.mkdir(parents=True,exist_ok=True)
    n=0;eligible=0;missing=Counter()
    with output.open('w',encoding='utf-8') as out:
        for r in candidates:
            key=tuple(str(r.get(k)) for k in ('episode_id','a_anchor','b_anchor'))
            contract=source_contracts.get(key)
            if not isinstance(contract,dict):missing['screening_contract']+=1
            for mid in r.get('midroute',[]):
                dist_B=mid.get('distance_to_B_m')
                if dist_B is None or not math.isfinite(float(dist_B)) or float(dist_B)<0:
                    missing['directed_midroute_distance_to_B']+=1
                    continue
                if mid.get('B_directed_suffix_witness') is None:
                    missing['B_recovery_suffix_witness_not_exported_or_not_available']+=1
                raw_needed=float(mid['distance_consumed_m'])+float(dist_B)
                budget=contract.get('distance_budget_m') if contract else None
                if budget is not None and math.isfinite(float(budget)):
                    within = raw_needed <= float(budget)+1e-7
                    eligible += int(within)
                else:within=None
                record={
                  'schema':'capplan_handoff_progress_packet_v1',
                  'episode_id':r['episode_id'],'city':r.get('city', 'unknown'),
                  'a_anchor':r['a_anchor'],'b_anchor':r['b_anchor'],
                  'physical_site_pair':[
                      _physical_site(anchors[(str(r['episode_id']), str(r[name]))])
                      if (str(r['episode_id']),str(r[name])) in anchors else None
                      for name in ('a_anchor','b_anchor')],
                  'graph_witness':{'evidence_level':'ENCODED_GRAPH_ONLY',
                     'path_O_A_edges':r.get('path_O_A_edges',[]),
                     'midroute_B_directed_suffix':mid.get('B_directed_suffix_witness'),
                     'complete_B_suffix_available':mid.get('B_directed_suffix_witness') is not None,
                     'O_A_m':r.get('O_A_m'),'O_B_m':r.get('O_B_m'),
                     'A_B_m':r.get('A_B_m'),
                     'arc_orientation_semantics_independently_verified':False},
                  'action_history':{'notice_issued_s':mid['notice_issued_s'],
                     'actionable_at_s':mid['actionable_at_s'],
                     'already_moved_total_m':mid['distance_consumed_m'],
                     'directed_distance_to_B_m':dist_B,
                     'cumulative_movement_needed_for_B_m':round(raw_needed,6),
                     'screening_budget_m':budget,
                     'within_screening_budget':within,
                     'other_graph_candidates_count':mid.get('third_graph_feasible_count'),
                     'other_graph_candidates_exhaustively_verified':False},
                  'pudo_hybrid_A':_anchor_summary(anchors.get((str(r['episode_id']),str(r['a_anchor'])))),
                  'pudo_hybrid_B':_anchor_summary(anchors.get((str(r['episode_id']),str(r['b_anchor'])))),
                  'unobserved_execution':{'vehicle_stop_windows_s':None,
                     'evidence_validity_windows_s':None,
                     'interface_windows_s':None,'boarding_duration_s':None,
                     'observation_and_acknowledgement_attestation':None},
                  'research_labels':{'real_handoff_verified':False,
                     'overall_recourselessness_verified':False,
                     'unknown_not_equivalent_to_infeasible':True,
                     'screened_not_unbiased_incidence':True},
                }
                out.write(json.dumps(record,ensure_ascii=False)+'\n');n+=1
    report={'schema':'capplan_handoff_geometry_to_evidence_bridge_v1',
        'packet_count':n,'selected_A_B_pairs':len(candidates),
        'within_screening_budget_count':eligible,'missing_source_counts':dict(missing),
        'has_independent_real_handoff_verifications':False,
        'graph_source_sha256':_digest_file(remine),
        'historical_pairs_sha256':_digest_file(Path(pairs)) if pairs else None,
        'physical_pudo_source':str(dataset / 'pudo_anchors.jsonl') if dataset else None,
        'data_interpretation':'ENCODED_GIS_GEOMETRY_PLUS_UNKNOWN_EXECUTION_NOT_INCIDENCE',
        'output':str(output)}
    _atomic_json(output.with_suffix('.summary.json'),report)
    return report


def _numbers(cfg: dict, field: str) -> tuple[float,float]:
    vals=cfg.get(field)
    if not isinstance(vals,list) or len(vals)!=2 or any(type(v) not in (int,float) or not math.isfinite(v) for v in vals):
        raise ValueError(f'{field} must have two finite numeric levels')
    return float(vals[0]),float(vals[1])


def controlled_factorial(*, packets: Path, assumptions: Path, output: Path,
                         max_packets: int = 200) -> dict:
    """2x2x2 paired counterfactuals, with all *clock* values declared synthetic.

    Evidence expiry and vehicle windows are anchored to a reference O-to-B ETA
    but are held FIXED across the five notice times of each episode. This
    avoids making future windows conveniently follow passenger movement.
    """
    from itertools import product
    cfg=json.loads(Path(assumptions).read_text(encoding='utf-8'))
    if cfg.get('truth_claim_scope') != 'CONTROLLED_SYNTHETIC_INTERVENTION_NOT_REAL_MEASUREMENT':
        raise ValueError('Refuse to present construction assumptions as city measurements')
    budget_offsets=_numbers(cfg,'budget_offsets_m')
    expiry_offsets=_numbers(cfg,'evidence_expiry_offsets_s')
    vehicle_offsets=_numbers(cfg,'vehicle_start_offsets_s')
    if budget_offsets[0]>budget_offsets[1] or expiry_offsets[0]>expiry_offsets[1]:
        raise ValueError('capability and evidence levels must be ordered from stricter to looser')
    dwell=float(cfg['vehicle_dwell_s']); boarding=float(cfg['boarding_duration_s'])
    wait=float(cfg['passenger_wait_remaining_s']); speed=float(cfg['speed_mps'])
    arr_unc=float(cfg['passenger_arrival_uncertainty_s'])
    if not (dwell>0 and boarding>0 and wait>=0 and speed>0 and arr_unc>=0):
        raise ValueError('Invalid controlled movement/time model')
    if max_packets<1:raise ValueError('max_packets must be positive')
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    counts=Counter(); flips=Counter();dissent=[];contexts=defaultdict(list)
    monotonicity_counterexamples=[]
    rows=0;packets_used=0
    with output.open('w',encoding='utf-8') as dst:
        for packet in _read_jsonl(Path(packets)):
            if packets_used>=max_packets:break
            info=packet['action_history']; witness=packet['graph_witness']
            budget=info.get('screening_budget_m'); dist_B=info.get('directed_distance_to_B_m')
            direct_B=witness.get('O_B_m')
            if budget is None or dist_B is None or direct_B is None:
                counts['UNKNOWN_MISSING_GRAPH_BUDGET']+=1;continue
            if not all(math.isfinite(float(x)) for x in (budget,dist_B,direct_B)):
                counts['UNKNOWN_INVALID_GEOMETRY']+=1;continue
            packets_used+=1
            base_eta=math.ceil(float(direct_B)/speed)
            actionable=float(info['actionable_at_s'])
            arrival=math.ceil(actionable + float(dist_B)/speed)
            needed=float(info['cumulative_movement_needed_for_B_m'])
            source_key=(str(packet['episode_id']),str(packet['a_anchor']),str(packet['b_anchor']))
            for C,E,V in product(range(2),repeat=3):
                available_budget=float(budget)+budget_offsets[C]
                contract_ok=(needed<=available_budget+1e-8)
                expiry=max(0,math.floor(base_eta+expiry_offsets[E]))
                vehicle_start=max(0,math.floor(base_eta+vehicle_offsets[V]))
                horizon=int(max(300,arrival+arr_unc+wait+boarding+120,
                                expiry+boarding+120,vehicle_start+dwell+120))
                case={'case_id':f'{rows}_{packet["episode_id"]}',
                      'world_support':['declared_B_available'],
                      'options':[{
                        'option_id':'B','contract_feasible':bool(contract_ok),
                        'passenger_arrival_quantifier':'all',
                        'passenger_arrival_window_s':[arrival,arrival+arr_unc],
                        'post_observation_passenger_arrival_window_s':[arrival,arrival+arr_unc],
                        'passenger_wait_remaining_s':wait,
                        'boarding_duration_s':boarding,
                        'vehicle_stop_windows_s':[[vehicle_start,vehicle_start+dwell]],
                        'evidence_validity_windows_s':[[0,expiry]],
                        'interface_windows_s':[[0,horizon]],
                        'world_handoff_open_windows_s':{'declared_B_available':[[0,horizon]]}
                      }]}
                result=compare_case(case,check_independent_grid=True)
                status=result['exact_restricted_interval_status']
                disagree=not (result['grid_interval_agree'] and result['safe_fixed_action_sets_agree'])
                if disagree:dissent.append({'case_id':case['case_id'],'source':source_key})
                accepted=bool(result['joint_safe_fixed_actions']['safe_actions'])
                counts['safe' if accepted else 'not_safe']+=1
                counts['disagreements']+=int(disagree)
                scope=str(C)+str(E)+str(V)
                contexts[(source_key,actionable)].append((scope,accepted))
                dst.write(json.dumps({
                    'case_id':case['case_id'],'episode_id':packet['episode_id'],
                    'a_anchor':packet['a_anchor'],'b_anchor':packet['b_anchor'],
                    'notice_issued_s':info['notice_issued_s'],
                    'factors':{'capability_budget_level':C,'evidence_expiry_level':E,
                               'vehicle_arrival_level':V},
                    'synthetic_window_source':cfg['truth_claim_scope'],
                    'distance_needed_m':round(needed,4),
                    'budget_m':round(available_budget,4),
                    'evidence_expiry_at_s':expiry,
                    'vehicle_window_s':[vehicle_start,vehicle_start+dwell],
                    'boarding_duration_s':boarding,'arrival_window_s':[arrival,arrival+arr_unc],
                    'robust_fixed_B_safe':accepted,
                    'marginal_endpoint_accept':bool(result['marginal_accepting_actions']),
                    'independent_reference_agrees':not disagree,
                    'joint_status':status,
                    'has_physical_vehicle_window_evidence':False,
                    'natural_incidence_estimable':False},ensure_ascii=False)+'\n')
                rows+=1
    for vals in contexts.values():
        if len(vals)==8:
            outcomes={key:val for key,val in vals}
            flips['paired_groups']+=1
            flips['has_some_safe_and_unsafe']+=int(len(set(outcomes.values()))==2)
            for i in (0,1):
                for k,baseline in outcomes.items():
                    if k[i]=='0' and baseline and not outcomes[k[:i]+'1'+k[i+1:]]:
                        monotonicity_counterexamples.append({'group':str(vals[:2]),'factor':('capability','evidence')[i], 'factor_key':k})
            for i,name in enumerate(('capability','evidence','vehicle')):
                witness=any(outcomes[k]!=outcomes[k[:i]+('1' if k[i]=='0' else '0')+k[i+1:]]
                            for k in outcomes)
                flips[name+'_sensitivity']+=int(witness)
    summary={'schema':'capplan_handoff_controlled_factorial_v1',
            'factor_count':3,'pairs_per_notice':8,'factorial_rows':rows,
            'packets_used':packets_used,'result_counts':dict(counts),
            'paired_flip_counts':dict(flips),'reference_disagreements':dissent[:20],
            'reference_disagreement_count':len(dissent),
            'monotonicity_violations':len(monotonicity_counterexamples),
            'monotonicity_violation_examples':monotonicity_counterexamples[:15],
            'temporal_assumptions_sha256':_digest_file(Path(assumptions)),
            'geometry_packets_sha256':_digest_file(Path(packets)),
            'scope':'CONTROLLED_SYNTHETIC_CLOCKS_ON_ENCODED_GIS_GEOMETRY_NOT_REAL_EVENT_FREQUENCY',
            'no_vehicle_motion_verification':True,
            'not_published_shield_baseline':True}
    _atomic_json(output.with_suffix('.summary.json'),summary)
    return summary


def audit_dataset_sources(*, dataset_root: Path, output: Path, splits=('train','val','test'),
                          max_pudo_rows_per_split: int = 0) -> dict:
    """Exact streaming provenance census unless max_pudo_rows_per_split > 0.

    Reads PUDO JSONL only: it does NOT read massive pedestrian edge tables and
    it does NOT establish ground-truth legality or capability of a human.
    """
    if max_pudo_rows_per_split < 0:raise ValueError('max rows cannot be negative')
    root=Path(dataset_root)
    kind=defaultdict(Counter);fields=defaultdict(Counter);city=defaultdict(Counter)
    site_splits=defaultdict(set);site_rows=Counter();evidence=Counter()
    out={};provenance_fields=('curb_height_m','sidewalk_width_m','deployment_clearance_m',
                            'legal_stop','curb_ramp','blockage_risk','side')
    for split in splits:
        name='abilitybench_av_hybrid_'+split
        folder=root/name
        path=folder/'pudo_anchors.jsonl'
        if not path.is_file():
            out[split]={'error':'DATASET_OR_PUDO_FILE_NOT_FOUND','path':str(path)}
            continue
        n=0;unknown_city=0;joined_city=0
        city_by_episode={}
        episode_file=folder/'episodes.jsonl'
        if episode_file.is_file():
            for e in _read_jsonl(episode_file):
                location=e.get('map_name') or e.get('city') or e.get('location')
                if location is None and isinstance(e.get('metadata'),dict):
                    location=e['metadata'].get('map_name') or e['metadata'].get('location')
                city_by_episode[str(e.get('episode_id'))]=_city(location)
        for row in _read_jsonl(path):
            n+=1
            origin=row.get('field_provenance') or {}
            source=_physical_site(row)
            if source:
                site_rows[source]+=1;site_splits[source].add(split)
            else:evidence['no_physical_site_identity']+=1
            if row.get('adjacent_ped_node_id') is None:
                evidence['no_pedestrian_node_binding']+=1
            if row.get('stop_pose') is None:evidence['no_stop_pose']+=1
            if row.get('curb_pose') is None:evidence['no_curb_pose']+=1
            for field in provenance_fields:
                kind[field][_known_provenance(origin.get(field))]+=1
                if row.get(field) is None:fields[field]['missing_value']+=1
                else:fields[field]['present_value']+=1
            mc=row.get('map_name') or row.get('city')
            ci=_city(mc)
            if ci=='unknown':
                ci=city_by_episode.get(str(row.get('episode_id')),'unknown')
                if ci!='unknown':joined_city+=1
            if ci=='unknown':unknown_city+=1
            else:city[split][ci]+=1
            if max_pudo_rows_per_split and n>=max_pudo_rows_per_split:
                break
        out[split]={'scanned_pudo_rows':n,'prefix_truncated':bool(max_pudo_rows_per_split and n>=max_pudo_rows_per_split),
                    'city_known_rows':dict(city[split]),'city_unknown_rows':unknown_city,
                    'city_joined_from_episodes_rows':joined_city,
                    'path':str(path),'file_bytes':path.stat().st_size}
    shared=sum(len(s)>1 for s in site_splits.values())
    result={'schema':'capplan_handoff_hybrid_evidence_census_v1',
            'dataset_root':str(root),'split_summary':out,
            'core_field_provenance_counts':{k:dict(v) for k,v in kind.items()},
            'core_field_presence_counts':{k:dict(v) for k,v in fields.items()},
            'structural_issues':dict(evidence),
            'unique_physical_site_keys_in_scan':len(site_splits),
            'physical_site_keys_across_splits_in_scan':shared,
            'sampled_or_prefix_counts_must_not_be_presented_as_city_population_statistics':bool(max_pudo_rows_per_split),
            'does_not_certify_real_field_truth':True,
            'decision':'KEEP_ORIGINAL_DATASETS; ADD_TEMPORAL_SIDECARS_AND_SMALL_INDEPENDENT_AUDITS'}
    _atomic_json(Path(output),result)
    return result
