#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capplan.data.nuplan_adapter import NuPlanAdapter
from capplan.data.schemas import to_dict
from capplan.utils.serialization import dump_json

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
    args = p.parse_args()

    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    resolved = _resolve_db_inputs(args)
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

    scenes_path = out / 'scenes.jsonl'
    episodes_path = out / 'episodes.jsonl'
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

    try:
        with scenes_part.open('w', encoding='utf-8') as sf, episodes_part.open('w', encoding='utf-8') as ef:
            for rec in iterator:
                s = to_dict(rec.scene)
                e = to_dict(rec.episode)
                sf.write(json.dumps(s, sort_keys=True) + '\n')
                ef.write(json.dumps(e, sort_keys=True) + '\n')
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
        },
    }
    dump_json(out / 'scene_context_manifest.json', manifest)
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
