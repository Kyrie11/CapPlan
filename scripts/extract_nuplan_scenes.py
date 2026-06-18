#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capplan.data.nuplan_adapter import NuPlanAdapter
from capplan.data.schemas import to_dict
from capplan.utils.serialization import dump_json, write_jsonl


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
    p.add_argument('--max_scenarios', type=int, default=50)
    p.add_argument('--num_workers', type=int, default=0)
    p.add_argument('--seed', type=int, default=13)
    p.add_argument('--output_dir', required=True)
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
    scenes = []
    episodes = []
    map_counts = {}
    type_counts = {}
    for rec in adapter.iter_scenarios(args.max_scenarios):
        s = to_dict(rec.scene)
        e = to_dict(rec.episode)
        scenes.append(s)
        episodes.append(e)
        map_counts[str(s.get('map_name'))] = map_counts.get(str(s.get('map_name')), 0) + 1
        type_counts[str(s.get('scenario_type'))] = type_counts.get(str(s.get('scenario_type')), 0) + 1
    if not scenes:
        raise RuntimeError('no nuPlan scenes were extracted; check DB folders, map filters, and scenario filters')
    write_jsonl(out / 'scenes.jsonl', scenes)
    write_jsonl(out / 'episodes.jsonl', episodes)
    manifest = {
        'mode': 'nuplan_scene_context_extract',
        'split': args.split,
        'num_scenes': len(scenes),
        'map_counts': map_counts,
        'scenario_type_counts': type_counts,
        'nuplan': {
            'data_root': args.nuplan_data_root or args.nuplan_root,
            'map_root': args.nuplan_map_root,
            'db_files_requested': resolved,
            'db_files_expanded': adapter.db_files,
            'map_version': args.nuplan_map_version,
            'map_names_filter': args.nuplan_map_names,
            'scenario_types_filter': args.nuplan_scenario_types,
            'log_names_filter': args.nuplan_log_names,
        },
    }
    dump_json(out / 'scene_context_manifest.json', manifest)
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
