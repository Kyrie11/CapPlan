"""Fail-closed H1-H5 review for either a packed ZIP or an unpacked study folder.

Metadata proves neither physical safety nor real-world PUDO failure. Integrity
verification succeeds only when an uploaded bundle manifest is present and all
checksums agree; previewing a working folder without a manifest is allowed,
but clearly marked UNPACKAGED and not treated as a scientific gate failure.
"""
from __future__ import annotations
from pathlib import Path
from zipfile import ZipFile
import json
import hashlib


def _reader(target: Path):
    target=Path(target)
    if target.is_file() and target.suffix.lower()=='.zip':
        with ZipFile(target) as z:
            files={name for name in z.namelist() if '/' not in name and not name.startswith('.')}
        def read(name):
            if name not in files:return None
            with ZipFile(target) as z:
                meta=z.getinfo(name)
                if meta.file_size>3_500_000:raise ValueError(f'{name} exceeds review file limit')
                return z.read(name)
        return read,'ZIP_UPLOAD'
    if target.is_dir():
        def read(name):
            path=target/name
            if not path.is_file():return None
            if path.stat().st_size>3_500_000:raise ValueError(f'{name} exceeds review file limit')
            return path.read_bytes()
        return read,'UNPACKED_DIRECTORY'
    raise FileNotFoundError(f'Not an existing review ZIP or directory: {target}')


def review(folder: Path) -> dict:
    read,kind=_reader(Path(folder))
    mb=read('bundle_manifest.json')
    errors=[]
    if mb is None:
        integrity='UNPACKAGED_PREVIEW_NO_MANIFEST'
        errors.append('MANIFEST_NOT_PRESENT')
    else:
        manifest=json.loads(mb)
        integrity='VERIFIED_UPLOAD_MANIFEST'
        for name,info in manifest.get('files',{}).items():
            if not name or name in ('.','..') or '/' in name or '\\' in name:
                errors.append('INVALID_MANIFEST_NAME');continue
            body=read(name)
            if body is None:errors.append(f'{name}:MISSING');continue
            if len(body)!=info.get('bytes'):errors.append(f'{name}:SIZE_MISMATCH')
            if hashlib.sha256(body).hexdigest()!=info.get('sha256'):
                errors.append(f'{name}:SHA256_MISMATCH')
        if errors:integrity='MANIFEST_INVALID'
    def load(name):
        body=read(name)
        return json.loads(body) if body is not None else {}
    remine=load('directed_remine.summary.json');carla=load('carla_triplets.json')
    five=load('carla_fiveway.json');site=load('site_evidence.json')
    obs=load('observation_benchmark.json');temporal=load('temporal.summary.json')
    base=load('baseline_comparison.summary.json')
    rc=remine.get('counts',{});cc=carla.get('counts',{});fc=five.get('counts',{})
    selected=remine.get('original_selected_pairs')
    result={
      'scope':'REVIEW_METADATA_ONLY_NOT_REAL_WORLD_ATTESTATION',
      'input_kind':kind,'bundle_integrity':integrity,'manifest_errors':errors,
      'H1':{'stage':'CONTROLLED_SIMULATOR_SENSITIVITY_NO_PHYSICAL_HANDOFF',
            'independently_verified_real_handoffs':site.get('verified_real_handoff_count',0),
            'independent_real_handoff_count':site.get('verified_real_handoff_count',0),
            'site_records':site.get('records'),
            'fiveway_complete':fc.get('complete_fiveway_controls'),
            'fiveway_matched':fc.get('matched_fiveway_controls'),
            'clear_A_stop_proxy':fc.get('clear_A_stop_proxy',cc.get('clear_A_stop_proxy')),
            'blocked_A_stop_proxy':fc.get('blocked_A_stop_proxy',cc.get('blocked_A_stop_proxy')),
            'clear_B_stop_proxy':fc.get('clear_B_stop_proxy'),
            'clear_A_redirect_B_stop_proxy':fc.get('clear_A_redirect_B_stop_proxy'),
            'blocked_A_redirect_B_stop_proxy':fc.get('blocked_A_redirect_B_stop_proxy'),
            'physical_boarding_not_verified':True},
      'H2':{'stage':'DIRECTION_ENCODED_GEOMETRY_ONLY',
            'selected_screened_pairs':selected,
            'directional_pairs':rc.get('AS_ENCODED::DIRECTED_TERMINAL_AB_ASYMMETRY_EXISTS',0),
            'no_direct_paths':rc.get('AS_ENCODED::NO_TWO_DIRECT_PATHS_UNDER_ENCODING',0),
            'not_initially_feasible':rc.get('AS_ENCODED::NOT_INITIAL_TWO_OPTION_FEASIBLE',0),
            'counted_rows':remine.get('direction_evaluation_rows_written'),
            'expected_rows':2*selected if isinstance(selected,int) else None,
            'arc_orientation_is_real_one_way_verified':False,
            'unbiased_incidence_estimable':False,'incidence_estimable':False},
      'H3':{'stage':'NOT_EVALUATED_AGAINST_PUBLISHED_SHIELD',
            'controlled_marginal_false_accept_cases':base.get('counts',{}).get('marginal_accept_no_robust_fixed'),
            'fair_published_shield_reproduced':False,
            'published_shield_implementation_reproduced':False},
      'H4_prime':{'stage':'ACTION_SET_PROTOCOL_SENSITIVITY_NEEDS_REAL_WINDOWS'},
      'H5':{'stage':'EXACT_RESTRICTED_ONE_OBSERVATION_ONLY',
            'instances':obs.get('parameters',{}).get('replicates'),
            'reference_disagreements':obs.get('mismatch_count'),
            'reported_speedup':obs.get('speedup_on_this_restricted_model'),
            'temporal_case_disagreements':temporal.get('mismatch_count'),
            'not_a_published_shield':True},
      'next':[
          'KEEP_INDEPENDENT_PHYSICAL_PUDO_EVIDENCE_AS_UNKNOWN_UNTIL_ATTESTED',
          'CHECK_CARLA_BLOCKER_GEOMETRY_TIMES_AND_MULTI_SEED_REPEATABILITY',
          'EXPORT_PROGRESS_PACKETS_AND_RUN_CONTROLLED_TIME_FACTORS',
          'COMPARE_ALL_SAFE_ACTIONS_TO_INDEPENDENT_REFERENCE',
          'ONLY_THEN_IMPLEMENT_EQUAL_INFORMATION_PUBLISHED_SHIELDS'],
      'no_claim_existing_exact_algorithms_cannot_solve':True
    }
    return result
