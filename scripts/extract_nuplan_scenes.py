#!/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capplan.data.nuplan_adapter import NuPlanAdapter
from capplan.data.schemas import to_dict
from capplan.utils.serialization import dump_json
from capplan.utils.build_fingerprint import fingerprint

# Semantic output version deliberately stays compatible with the 2026-08-20
# extractor.  The 2026-08-21 change only adds crash-safe I/O/checkpoint resume;
# completed v1 scene files therefore remain valid and reusable.
NUPLAN_EXTRACT_VERSION = "20260820_resumable_exact_v1"
NUPLAN_EXTRACT_RUNTIME_VERSION = "20260821_partial_checkpoint_v2"

try:
    from tqdm.auto import tqdm  # type: ignore
except Exception:  # pragma: no cover
    def tqdm(iterable=None, **kwargs):  # type: ignore
        return iterable if iterable is not None else []


def _split_cli(values: List[str] | str | None) -> List[str]:
    if values is None:
        return []
    raw = values if isinstance(values, list) else [values]
    out: List[str] = []
    for item in raw:
        for piece in str(item).replace(',', '+').split('+'):
            piece = piece.strip()
            if piece:
                out.append(piece)
    return out


def _resolve_db_inputs(args: argparse.Namespace) -> List[str] | str | None:
    root = Path(args.nuplan_db_root or args.nuplan_data_root or args.nuplan_root or '.')
    tokens: List[str] = []
    if getattr(args, "nuplan_db_manifest", None):
        manifest = Path(args.nuplan_db_manifest)
        if not manifest.exists():
            raise FileNotFoundError(f"nuPlan DB manifest does not exist: {manifest}")
        for line in manifest.read_text(encoding="utf-8").splitlines():
            token = line.strip()
            if token and not token.startswith("#"):
                p = Path(token)
                tokens.append(str(p if p.is_absolute() else root / p))
    for token in _split_cli(args.nuplan_db_files):
        p = Path(token)
        tokens.append(str(p if p.is_absolute() else root / p))
    for token in _split_cli(args.nuplan_db_dirs):
        p = Path(token)
        tokens.append(str(p if p.is_absolute() else root / p))
    return tokens or args.nuplan_db_files


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _scan_partial_pair(
    scenes_part: Path,
    episodes_part: Path,
    *,
    split: str,
    allowed_map_names: set[str],
) -> Dict[str, Any]:
    """Validate/adopt an older uncheckpointed .part pair exactly.

    We stream the two files in lock-step and require equal episode IDs, valid
    JSON, nuPlan source, requested split and requested map filter.  This is a
    one-time O(file-size) read for legacy partials; normal checkpoints avoid it.
    """
    if not scenes_part.exists() or not episodes_part.exists():
        raise RuntimeError("partial adoption requires both scenes.jsonl.part and episodes.jsonl.part")
    num = 0
    prefix_hasher = hashlib.sha256()
    map_counts: Dict[str, int] = {}
    type_counts: Dict[str, int] = {}
    last_eid: str | None = None
    with scenes_part.open("r", encoding="utf-8") as sf, episodes_part.open("r", encoding="utf-8") as ef:
        while True:
            sl = sf.readline()
            el = ef.readline()
            if not sl and not el:
                break
            if not sl or not el:
                raise RuntimeError("partial scene/episode files have different line counts")
            try:
                s = json.loads(sl)
                e = json.loads(el)
            except Exception as exc:
                raise RuntimeError(f"invalid JSON in partial extraction near row {num + 1}") from exc
            se = str(s.get("episode_id") or "")
            ee = str(e.get("episode_id") or "")
            if not se or se != ee:
                raise RuntimeError(f"partial scene/episode episode_id mismatch at row {num + 1}: {se!r} != {ee!r}")
            if str(s.get("source") or "") != "nuplan":
                raise RuntimeError(f"partial row {num + 1} is not a real nuPlan scene")
            if str(s.get("split") or "") != split:
                raise RuntimeError(f"partial row {num + 1} split mismatch: {s.get('split')!r} != {split!r}")
            map_name = str(s.get("map_name") or "")
            if allowed_map_names and map_name not in allowed_map_names:
                raise RuntimeError(f"partial row {num + 1} map_name {map_name!r} violates requested filter {sorted(allowed_map_names)}")
            st = str(s.get("scenario_type"))
            map_counts[map_name] = map_counts.get(map_name, 0) + 1
            type_counts[st] = type_counts.get(st, 0) + 1
            prefix_hasher.update(se.encode("utf-8")); prefix_hasher.update(b"\0")
            last_eid = se
            num += 1
    if num <= 0 or not last_eid:
        raise RuntimeError("partial files are empty; nothing can be adopted")
    return {
        "num_scenes": num,
        "last_episode_id": last_eid,
        "episode_id_prefix_sha256": prefix_hasher.hexdigest(),
        "map_counts": map_counts,
        "scenario_type_counts": type_counts,
        "scenes_bytes": scenes_part.stat().st_size,
        "episodes_bytes": episodes_part.stat().st_size,
    }


def _load_checkpoint(path: Path, extract_fp: str, scenes_part: Path, episodes_part: Path) -> Dict[str, Any] | None:
    if not path.exists() or not scenes_part.exists() or not episodes_part.exists():
        return None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if state.get("status") != "PARTIAL" or state.get("extract_fingerprint") != extract_fp:
        return None
    sb = int(state.get("scenes_bytes", -1))
    eb = int(state.get("episodes_bytes", -1))
    if sb < 0 or eb < 0 or scenes_part.stat().st_size < sb or episodes_part.stat().st_size < eb:
        return None
    # A crash can happen after data flush but before the next checkpoint.  Drop
    # that uncommitted tail so the checkpoint remains the exact atomic prefix.
    if scenes_part.stat().st_size != sb:
        with scenes_part.open("r+b") as f:
            f.truncate(sb)
    if episodes_part.stat().st_size != eb:
        with episodes_part.open("r+b") as f:
            f.truncate(eb)
    return state


def _commit_checkpoint(
    state_path: Path,
    *,
    extract_fp: str,
    num_scenes: int,
    last_episode_id: str,
    map_counts: Dict[str, int],
    type_counts: Dict[str, int],
    scenes_file,
    episodes_file,
    scenes_part: Path,
    episodes_part: Path,
    adopted_legacy_partial: bool = False,
) -> None:
    scenes_file.flush(); episodes_file.flush()
    os.fsync(scenes_file.fileno()); os.fsync(episodes_file.fileno())
    state = {
        "status": "PARTIAL",
        "semantic_version": NUPLAN_EXTRACT_VERSION,
        "runtime_version": NUPLAN_EXTRACT_RUNTIME_VERSION,
        "extract_fingerprint": extract_fp,
        "num_scenes": int(num_scenes),
        "last_episode_id": str(last_episode_id),
        "map_counts": dict(map_counts),
        "scenario_type_counts": dict(type_counts),
        "scenes_bytes": scenes_part.stat().st_size,
        "episodes_bytes": episodes_part.stat().st_size,
        "adopted_legacy_partial": bool(adopted_legacy_partial),
        "updated_at": _now(),
    }
    dump_json(state_path, state)


def main() -> None:
    p = argparse.ArgumentParser(description='Extract nuPlan scene contexts before GIS fusion. This writes scenes/episodes with route corridors but no passenger labels.')
    p.add_argument('--nuplan_data_root', required=True)
    p.add_argument('--nuplan_map_root', required=True)
    p.add_argument('--nuplan_sensor_root', default=None)
    p.add_argument('--nuplan_db_files', default=None)
    p.add_argument('--nuplan_db_manifest', default=None, help='Text file containing one concrete .db path per line. Used to avoid rescanning mixed-city split directories.')
    p.add_argument('--nuplan_db_root', default=None)
    p.add_argument('--nuplan_db_dirs', nargs='*', default=None)
    p.add_argument('--nuplan_map_version', required=True)
    p.add_argument('--nuplan_map_names', default=None, help='Optional comma/plus-separated nuPlan map_name filter, e.g. us-ma-boston.')
    p.add_argument('--nuplan_scenario_types', default=None)
    p.add_argument('--nuplan_log_names', default=None)
    p.add_argument('--nuplan_root', default=None)
    p.add_argument('--split', default='train')
    p.add_argument('--max_scenarios', type=int, default=50, help='Maximum matching scenarios; for real nuPlan data, 0 means all.')
    p.add_argument('--num_workers', type=int, default=0, help='nuPlan DB discovery/filter worker count. Per-scene history extraction remains deterministic/sequential.')
    p.add_argument('--timestamp_threshold_s', type=float, default=None, help='Optional official nuPlan temporal de-clustering filter. Disabled by default to preserve existing output semantics.')
    p.add_argument('--ego_displacement_minimum_m', type=float, default=None, help='Optional official nuPlan non-stationary filter. Disabled by default.')
    p.add_argument('--seed', type=int, default=13)
    p.add_argument('--output_dir', required=True)
    p.add_argument('--disable_tqdm', action='store_true', help='Disable streaming extraction progress.')
    p.add_argument('--resume', action='store_true', help='Reuse a completed extraction or a compatible checkpointed prefix.')
    p.add_argument('--adopt_existing_partial', action='store_true', help='One-time: validate and adopt legacy .part files created before partial checkpoints existed. Use only after stopping the old extractor.')
    p.add_argument('--checkpoint_interval', type=int, default=1000, help='Commit a crash-safe prefix every N extracted scenes.')
    p.add_argument('--force_rebuild', action='store_true', help='Ignore completed/partial caches and rebuild scene contexts.')
    args = p.parse_args()

    if args.checkpoint_interval <= 0:
        raise ValueError('--checkpoint_interval must be > 0')

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    resolved = _resolve_db_inputs(args)
    fp_payload = {
        "version": NUPLAN_EXTRACT_VERSION,
        "split": args.split,
        "max_scenarios": int(args.max_scenarios),
        "seed": int(args.seed),
        "map_version": args.nuplan_map_version,
        "map_names": args.nuplan_map_names,
        "scenario_types": args.nuplan_scenario_types,
        "log_names": args.nuplan_log_names,
    }
    # Preserve the exact 2026-08-20 completed-output fingerprint when these new
    # optional filters are disabled.  If either is enabled it becomes part of
    # the semantic selection identity and therefore invalidates old output.
    if args.timestamp_threshold_s is not None:
        fp_payload["timestamp_threshold_s"] = args.timestamp_threshold_s
    if args.ego_displacement_minimum_m is not None:
        fp_payload["ego_displacement_minimum_m"] = args.ego_displacement_minimum_m
    extract_fp = fingerprint(fp_payload, [*(resolved if isinstance(resolved, list) else ([resolved] if resolved else [])), args.nuplan_map_root])

    manifest_path = out / 'scene_context_manifest.json'
    scenes_path = out / 'scenes.jsonl'
    episodes_path = out / 'episodes.jsonl'
    scenes_part = scenes_path.with_suffix(scenes_path.suffix + '.part')
    episodes_part = episodes_path.with_suffix(episodes_path.suffix + '.part')
    partial_state_path = out / 'scene_context_partial_state.json'

    if args.resume and not args.force_rebuild and manifest_path.exists() and scenes_path.exists() and episodes_path.exists():
        try:
            old_manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        except Exception:
            old_manifest = {}
        if old_manifest.get('status') == 'PASS' and old_manifest.get('extract_fingerprint') == extract_fp:
            print(json.dumps({
                'status': 'PASS', 'resumed': True, 'split': args.split,
                'num_scenes': old_manifest.get('num_scenes'),
                'extract_fingerprint': extract_fp, 'output_dir': str(out),
            }, indent=2, sort_keys=True))
            print('NUPLAN_SCENE_EXTRACT_CHECK=PASS')
            return

    if args.force_rebuild:
        for pth in [scenes_part, episodes_part, partial_state_path]:
            pth.unlink(missing_ok=True)

    resume_state = None
    if args.resume and not args.force_rebuild:
        resume_state = _load_checkpoint(partial_state_path, extract_fp, scenes_part, episodes_part)

    adopted_legacy_partial = False
    if resume_state is None and args.resume and args.adopt_existing_partial and not args.force_rebuild and scenes_part.exists() and episodes_part.exists():
        print('[CAPPLAN_PROGRESS] validating legacy extraction .part files for exact prefix adoption', flush=True)
        adopted = _scan_partial_pair(
            scenes_part, episodes_part,
            split=args.split,
            allowed_map_names=set(_split_cli(args.nuplan_map_names)),
        )
        resume_state = {
            "status": "PARTIAL",
            "extract_fingerprint": extract_fp,
            **adopted,
        }
        # We can only write an atomic state after the pair has been fully
        # validated.  The first append below will immediately fsync it again.
        dump_json(partial_state_path, {
            **resume_state,
            "semantic_version": NUPLAN_EXTRACT_VERSION,
            "runtime_version": NUPLAN_EXTRACT_RUNTIME_VERSION,
            "adopted_legacy_partial": True,
            "updated_at": _now(),
        })
        adopted_legacy_partial = True

    if resume_state is None:
        # Never silently append to unknown legacy partials.  Keep them only when
        # the operator explicitly requests adoption; otherwise start clean.
        scenes_part.unlink(missing_ok=True)
        episodes_part.unlink(missing_ok=True)
        partial_state_path.unlink(missing_ok=True)
        num_scenes = 0
        map_counts: Dict[str, int] = {}
        type_counts: Dict[str, int] = {}
        last_episode_id = None
        mode = 'w'
    else:
        num_scenes = int(resume_state.get('num_scenes', 0) or 0)
        map_counts = {str(k): int(v) for k, v in (resume_state.get('map_counts') or {}).items()}
        type_counts = {str(k): int(v) for k, v in (resume_state.get('scenario_type_counts') or {}).items()}
        last_episode_id = str(resume_state.get('last_episode_id') or '') or None
        if num_scenes <= 0 or not last_episode_id:
            raise RuntimeError('partial checkpoint is missing num_scenes/last_episode_id')
        mode = 'a'
        print(json.dumps({
            'status': 'RESUMING_PARTIAL', 'split': args.split, 'committed_scenes': num_scenes,
            'last_episode_id': last_episode_id, 'adopted_legacy_partial': adopted_legacy_partial,
        }, indent=2, sort_keys=True), flush=True)

    adapter = NuPlanAdapter(
        scene_source='nuplan',
        data_root=args.nuplan_data_root or args.nuplan_root,
        map_root=args.nuplan_map_root,
        sensor_root=args.nuplan_sensor_root,
        db_files=resolved,
        map_version=args.nuplan_map_version,
        split=args.split,
        seed=args.seed,
        num_workers=args.num_workers,
        scenario_types=args.nuplan_scenario_types,
        map_names=args.nuplan_map_names,
        log_names=args.nuplan_log_names,
        timestamp_threshold_s=args.timestamp_threshold_s,
        ego_displacement_minimum_m=args.ego_displacement_minimum_m,
    )

    if adopted_legacy_partial:
        # Legacy .part files predate fingerprints.  Prove that every adopted
        # episode ID is exactly the prefix of the *current* ScenarioFilter output
        # before skipping any expensive history extraction.  A last-ID-only check
        # is not strong enough for an un-fingerprinted artifact.
        expected_prefix_hash = str(resume_state.get("episode_id_prefix_sha256") or "")
        if not expected_prefix_hash:
            raise RuntimeError("legacy partial adoption is missing prefix identity hash")
        prefix_hasher = hashlib.sha256()
        prefix_count = 0
        for identity in adapter.iter_scenario_index(max_scenarios=args.max_scenarios):
            if prefix_count >= num_scenes:
                break
            eid = str(identity.get("episode_id") or "")
            if not eid:
                raise RuntimeError(f"current scenario index row {prefix_count + 1} has no episode_id")
            prefix_hasher.update(eid.encode("utf-8")); prefix_hasher.update(b"\0")
            prefix_count += 1
        if prefix_count != num_scenes or prefix_hasher.hexdigest() != expected_prefix_hash:
            raise RuntimeError(
                "legacy .part files are not the exact prefix of the current nuPlan selection; "
                "refusing adoption. Preserve the old .part files separately or rebuild this selection. "
                f"partial_rows={num_scenes} current_prefix_rows={prefix_count}"
            )
        print(json.dumps({
            "status": "LEGACY_PARTIAL_PREFIX_VERIFIED",
            "rows": prefix_count,
            "episode_id_prefix_sha256": expected_prefix_hash,
        }, indent=2, sort_keys=True), flush=True)

    started = time.perf_counter()
    new_scenes = 0
    total = args.max_scenarios if args.max_scenarios > 0 else None
    iterator = adapter.iter_scenarios(
        args.max_scenarios,
        skip_scenarios=num_scenes,
        expected_last_skipped_episode_id=last_episode_id,
    )
    if not args.disable_tqdm:
        remaining_total = max(0, total - num_scenes) if total is not None else None
        iterator = tqdm(iterator, total=remaining_total, initial=0, desc=f'{args.split}: nuPlan scene extract', unit='scene', mininterval=1.0, dynamic_ncols=True)

    last_committed = num_scenes
    try:
        with scenes_part.open(mode, encoding='utf-8') as sf, episodes_part.open(mode, encoding='utf-8') as ef:
            for rec in iterator:
                s = to_dict(rec.scene)
                e = to_dict(rec.episode)
                s_line = json.dumps(s, sort_keys=True, separators=(',', ':')) + '\n'
                e_line = json.dumps(e, sort_keys=True, separators=(',', ':')) + '\n'
                sf.write(s_line); ef.write(e_line)
                num_scenes += 1; new_scenes += 1
                last_episode_id = str(s.get('episode_id') or '')
                map_counts[str(s.get('map_name'))] = map_counts.get(str(s.get('map_name')), 0) + 1
                type_counts[str(s.get('scenario_type'))] = type_counts.get(str(s.get('scenario_type')), 0) + 1
                if num_scenes - last_committed >= args.checkpoint_interval:
                    _commit_checkpoint(
                        partial_state_path, extract_fp=extract_fp, num_scenes=num_scenes,
                        last_episode_id=last_episode_id, map_counts=map_counts, type_counts=type_counts,
                        scenes_file=sf, episodes_file=ef, scenes_part=scenes_part, episodes_part=episodes_part,
                        adopted_legacy_partial=adopted_legacy_partial,
                    )
                    last_committed = num_scenes
            if num_scenes > last_committed and last_episode_id:
                _commit_checkpoint(
                    partial_state_path, extract_fp=extract_fp, num_scenes=num_scenes,
                    last_episode_id=last_episode_id, map_counts=map_counts, type_counts=type_counts,
                    scenes_file=sf, episodes_file=ef, scenes_part=scenes_part, episodes_part=episodes_part,
                    adopted_legacy_partial=adopted_legacy_partial,
                )
                last_committed = num_scenes
    except Exception:
        # .part + committed state deliberately survive.  Any data written after
        # the last state is truncated on the next resume.
        raise

    if num_scenes == 0:
        raise RuntimeError('no nuPlan scenes were extracted; check DB folders, map filters, and scenario filters')

    # Final hashes are computed over the exact committed files.  This second
    # sequential read is cheap compared with DB/map extraction and makes resume
    # independent of serializing hashlib internal state.
    scenes_sha256 = _sha256_file(scenes_part)
    episodes_sha256 = _sha256_file(episodes_part)
    scenes_part.replace(scenes_path)
    episodes_part.replace(episodes_path)
    partial_state_path.unlink(missing_ok=True)

    elapsed = time.perf_counter() - started
    manifest = {
        'mode': 'nuplan_scene_context_extract',
        'status': 'PASS',
        'split': args.split,
        'extract_version': NUPLAN_EXTRACT_VERSION,
        'extract_runtime_version': NUPLAN_EXTRACT_RUNTIME_VERSION,
        'extract_fingerprint': extract_fp,
        'scenes_sha256': scenes_sha256,
        'episodes_sha256': episodes_sha256,
        'num_scenes': num_scenes,
        'new_scenes_this_invocation': new_scenes,
        'partial_resume_supported': True,
        'max_scenarios_requested': args.max_scenarios,
        'timestamp_threshold_s': args.timestamp_threshold_s,
        'ego_displacement_minimum_m': args.ego_displacement_minimum_m,
        'elapsed_s_this_invocation': elapsed,
        'scenes_per_s_this_invocation': new_scenes / max(elapsed, 1e-9),
        'map_counts': map_counts,
        'scenario_type_counts': type_counts,
        'nuplan': {
            'data_root': args.nuplan_data_root or args.nuplan_root,
            'map_root': args.nuplan_map_root,
            'db_files_requested': resolved,
            'db_files_expanded': adapter.db_files,
            'db_files_expanded_count': len(adapter.db_files) if isinstance(adapter.db_files, list) else None,
            'map_version': args.nuplan_map_version,
            'map_names_filter': args.nuplan_map_names,
            'scenario_types_filter': args.nuplan_scenario_types,
            'log_names_filter': args.nuplan_log_names,
            'num_workers': args.num_workers,
            'route_geometry_cache': adapter.route_geometry_cache_stats,
        },
    }
    dump_json(manifest_path, manifest)
    compact = {
        'status': 'PASS', 'split': args.split, 'num_scenes': num_scenes,
        'new_scenes_this_invocation': new_scenes,
        'elapsed_s_this_invocation': round(elapsed, 3),
        'scenes_per_s_this_invocation': round(new_scenes / max(elapsed, 1e-9), 3),
        'map_counts': map_counts, 'scenario_type_counts': type_counts,
        'db_files_expanded_count': manifest['nuplan']['db_files_expanded_count'],
        'map_names_filter': args.nuplan_map_names,
        'output_dir': str(out),
    }
    print(json.dumps(compact, indent=2, sort_keys=True))
    print('NUPLAN_SCENE_EXTRACT_CHECK=PASS')


if __name__ == '__main__':
    main()
