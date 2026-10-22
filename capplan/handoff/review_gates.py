"""Fail-closed research gate audit over small CapPlan study bundles.

It checks the uploaded *metadata and hashes*, not raw city graphs, physical
curb claims or CARLA service completion. This script never upgrades missing
physical evidence or a timeout into a scientific negative result.
"""
from __future__ import annotations
from pathlib import Path
import json
import hashlib


def _json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def review(folder: Path) -> dict:
    folder=Path(folder)
    manifest=folder/'bundle_manifest.json'
    schema_errors=[]
    files={}
    if manifest.is_file():
        meta=_json(manifest)
        for name, info in meta.get('files',{}).items():
            file=folder/name
            if not file.is_file():
                schema_errors.append(f'{name}:NOT_PRESENT')
                continue
            if file.stat().st_size != info['bytes']:
                schema_errors.append(f'{name}:SIZE_MISMATCH')
            if hashlib.sha256(file.read_bytes()).hexdigest()!=info['sha256']:
                schema_errors.append(f'{name}:SHA256_MISMATCH')
    else:
        schema_errors.append('MANIFEST_NOT_PRESENT')
    targets=['directed_remine.summary.json','carla_triplets.json',
             'site_evidence.json','observation_benchmark.json',
             'temporal.summary.json']
    for name in targets:
        f=folder/name
        files[name]=_json(f) if f.is_file() else None
    remine=files['directed_remine.summary.json'] or {}
    carla=files['carla_triplets.json'] or {}
    site=files['site_evidence.json'] or {}
    obs=files['observation_benchmark.json'] or {}
    temporal=files['temporal.summary.json'] or {}
    cc=carla.get('counts',{})
    statuses=remine.get('counts',{})
    n_selected=remine.get('original_selected_pairs')
    n_written=remine.get('direction_evaluation_rows_written')
    # Prior releases had no explicit record total; reconstruct the number
    # of successful rows ONLY and keep the missing/error range explicit.
    if n_written is None:n_written=sum(statuses.values())
    error_examples=remine.get('errors',[])
    result={
       'scope':'REVIEW_METADATA_ONLY_NOT_CITY_REAL_WORLD_ATTESTATION',
       'manifest_errors':schema_errors,
       'H1': {'stage':'NOT_VERIFIED',
              'independent_real_handoff_count':site.get('verified_real_handoff_count',0),
              'site_record_count':site.get('records',None),
              'CARLA_clear_stop_proxy':cc.get('clear_A_stop_proxy'),
              'CARLA_blocked_stop_proxy':cc.get('blocked_A_stop_proxy'),
              'CARLA_valid_matched_controls':cc.get('matched_control_configuration'),
              'CARLA_redirect_B_stop_proxy':cc.get('redirect_B_stop_proxy'),
              'interpretation':'CARLA TOWN STOP SURROGATE; NOT LEGAL CURB/BOARDING PROOF'},
       'H2': {'stage':'GRAPH_TREND_ONLY_WITH_UNVERIFIED_ARC_DIRECTION',
              'screened_pairs':n_selected,
              'evaluation_records_written':n_written,
              'expected_records':2*n_selected if isinstance(n_selected,int) else None,
              'path_as_encoded_asymmetry':statuses.get('AS_ENCODED::DIRECTED_TERMINAL_AB_ASYMMETRY_EXISTS'),
              'pair_error_examples':error_examples[:8],
              'error_count_minimum':len(error_examples),
              'incidence_estimable':False},
       'H3':{'stage':'NOT_TESTED',
             'published_shield_implementation_reproduced':False,
             'recommendation':'Compare equal state/actions/observation/model first'},
       'H4':{'stage':'ACTION_PROTOCOL_SENSITIVITY_NOT_PHYSICAL_THREE_WAY_PROOF'},
       'H5':{'stage':'RESTRICTED_MODEL_AGREEMENT_ONLY',
             'instances':obs.get('parameters',{}).get('replicates'),
             'disagreements':obs.get('mismatch_count'),
             'speedup':obs.get('speedup_on_this_restricted_model'),
             'not_published_shield':obs.get('no_published_shield_reproduction',True),
             'temporal_case_disagreements':temporal.get('mismatch_count')},
       'next_experiment_decision':[
           'RE_RUN_DIRECTED_REMINE_WITH_FIXED_DENOMINATOR',
           'CALIBRATE_REDIR_B_AND_CERTIFY_PHYSICAL_STOP_WINDOW',
           'RUN_GENERIC_EXACT_ACTION_SET_BASELINE',
           'ONLY_THEN_REPRODUCE_PUBLISHED_SHIELDS'],
       'no_claim_all_existing_algorithms_fail':True,
    }
    return result
