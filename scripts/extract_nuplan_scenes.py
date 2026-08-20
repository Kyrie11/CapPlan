#!/usr/bin/env python
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capplan.data.nuplan_adapter import NuPlanAdapter
from capplan.data.schemas import to_dict
from capplan.utils.serialization import dump_json
from capplan.utils.build_fingerprint import fingerprint

NUPLAN_EXTRACT_VERSION = "20260820_resumable_exact_v1"

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
    p.add_argument('--num_workers', type=int, default=0)
    p.add_argument('--seed', type=int, default=13)
    p.add_argument('--output_dir', required=True)
    p.add_argument('--disable_tqdm', action='store_true', help='Disable streaming extraction progress.')
    p.add_argument('--resume', action='store_true', help='Reuse a completed extraction when its input/config fingerprint matches exactly.')
    p.add_argument('--force_rebuild', action='store_true', help='Ignore a matching extraction cache and rebuild scene contexts.')
    args = p.parse_args()

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    resolved = _resolve_db_inputs(args)
    extract_fp = fingerprint({
        "version": NUPLAN_EXTRACT_VERSION,
        "split": args.split,
        "max_scenarios": int(args.max_scenarios),
        "seed": int(args.seed),
        "map_version": args.nuplan_map_version,
        "map_names": args.nuplan_map_names,
        "scenario_types": args.nuplan_scenario_types,
        "log_names": args.nuplan_log_names,
    }, [*(resolved if isinstance(resolved, list) else ([resolved] if resolved else [])), args.nuplan_map_root])

    manifest_path = out / 'scene_context_manifest.json'
    scenes_path = out / 'scenes.jsonl'
    episodes_path = out / 'episodes.jsonl'
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
    )

    scenes_part = scenes_path.with_suffix(scenes_path.suffix + '.part')
    episodes_part = episodes_path.with_suffix(episodes_path.suffix + '.part')
    scenes_part.unlink(missing_ok=True)
    episodes_part.unlink(missing_ok=True)

    map_counts = {}
    type_counts = {}
    num_scenes = 0
    started = time.perf_counter()
    total = args.max_scenarios if args.max_scenarios > 0 else None
    iterator = adapter.iter_scenarios(args.max_scenarios)
    if not args.disable_tqdm:
        iterator = tqdm(iterator, total=total, desc=f'{args.split}: nuPlan scene extract', unit='scene', mininterval=1.0, dynamic_ncols=True)

    scenes_hasher = hashlib.sha256()
    episodes_hasher = hashlib.sha256()
    try:
        with scenes_part.open('w', encoding='utf-8') as sf, episodes_part.open('w', encoding='utf-8') as ef:
            for rec in iterator:
                s = to_dict(rec.scene)
                e = to_dict(rec.episode)
                s_line = json.dumps(s, sort_keys=True, separators=(',', ':')) + '\n'
                e_line = json.dumps(e, sort_keys=True, separators=(',', ':')) + '\n'
                sf.write(s_line); ef.write(e_line)
                scenes_hasher.update(s_line.encode('utf-8')); episodes_hasher.update(e_line.encode('utf-8'))
                num_scenes += 1
                map_counts[str(s.get('map_name'))] = map_counts.get(str(s.get('map_name')), 0) + 1
                type_counts[str(s.get('scenario_type'))] = type_counts.get(str(s.get('scenario_type')), 0) + 1
                if num_scenes % 100 == 0:
                    sf.flush(); ef.flush()
    except Exception:
        # Keep .part files for diagnosis but never let downstream stages mistake
        # them for a complete extraction.
        raise

    if num_scenes == 0:
        raise RuntimeError('no nuPlan scenes were extracted; check DB folders, map filters, and scenario filters')
    scenes_part.replace(scenes_path)
    episodes_part.replace(episodes_path)

    elapsed = time.perf_counter() - started
    manifest = {
        'mode': 'nuplan_scene_context_extract',
        'status': 'PASS',
        'split': args.split,
        'extract_version': NUPLAN_EXTRACT_VERSION,
        'extract_fingerprint': extract_fp,
        'scenes_sha256': scenes_hasher.hexdigest(),
        'episodes_sha256': episodes_hasher.hexdigest(),
        'num_scenes': num_scenes,
        'max_scenarios_requested': args.max_scenarios,
        'elapsed_s': elapsed,
        'scenes_per_s': num_scenes / max(elapsed, 1e-9),
        'map_counts': map_counts,
        'scenario_type_counts': type_counts,
        'nuplan': {
            'data_root': args.nuplan_data_root or args.nuplan_root,
            'map_root': args.nuplan_map_root,
            'db_files_requested': resolved,
            # The full list is recorded once in the manifest, never once per scene.
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
        'elapsed_s': round(elapsed, 3), 'scenes_per_s': round(num_scenes / max(elapsed, 1e-9), 3),
        'map_counts': map_counts, 'scenario_type_counts': type_counts,
        'db_files_expanded_count': manifest['nuplan']['db_files_expanded_count'],
        'map_names_filter': args.nuplan_map_names,
        'output_dir': str(out),
    }
    print(json.dumps(compact, indent=2, sort_keys=True))
    print('NUPLAN_SCENE_EXTRACT_CHECK=PASS')


if __name__ == '__main__':
    main()
