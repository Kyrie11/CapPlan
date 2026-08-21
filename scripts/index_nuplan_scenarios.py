#!/usr/bin/env python
"""Build a lightweight, immutable nuPlan scenario identity inventory.

Unlike ``extract_nuplan_scenes.py`` this command never loads 20-step ego/agent/
traffic-light histories or route geometry.  It is therefore suitable for
recording the complete official-split candidate population while the expensive
CapPlan accessibility/PUDO layers are materialized only for a paper-scale,
deterministically selected scene corpus.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path
from typing import Dict, List

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capplan.data.nuplan_adapter import NuPlanAdapter
from capplan.utils.build_fingerprint import fingerprint
from capplan.utils.serialization import dump_json

INDEX_VERSION = "20260821_nuplan_identity_index_v1"

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
            if piece.strip():
                out.append(piece.strip())
    return out


def _resolve_db_inputs(args: argparse.Namespace):
    root = Path(args.nuplan_db_root or args.nuplan_data_root or '.')
    tokens: List[str] = []
    if args.nuplan_db_manifest:
        p = Path(args.nuplan_db_manifest)
        if not p.exists():
            raise FileNotFoundError(p)
        for line in p.read_text(encoding='utf-8').splitlines():
            t = line.strip()
            if t and not t.startswith('#'):
                x = Path(t); tokens.append(str(x if x.is_absolute() else root / x))
    for t in _split_cli(args.nuplan_db_files):
        x = Path(t); tokens.append(str(x if x.is_absolute() else root / x))
    for t in _split_cli(args.nuplan_db_dirs):
        x = Path(t); tokens.append(str(x if x.is_absolute() else root / x))
    return tokens or args.nuplan_db_files


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--nuplan_data_root', required=True)
    p.add_argument('--nuplan_map_root', required=True)
    p.add_argument('--nuplan_sensor_root', default=None)
    p.add_argument('--nuplan_db_root', default=None)
    p.add_argument('--nuplan_db_manifest', default=None)
    p.add_argument('--nuplan_db_files', default=None)
    p.add_argument('--nuplan_db_dirs', nargs='*', default=None)
    p.add_argument('--nuplan_map_version', required=True)
    p.add_argument('--nuplan_map_names', default=None)
    p.add_argument('--nuplan_scenario_types', default=None)
    p.add_argument('--nuplan_log_names', default=None)
    p.add_argument('--split', required=True)
    p.add_argument('--max_scenarios', type=int, default=0, help='0 = complete matching population')
    p.add_argument('--num_workers', type=int, default=0)
    p.add_argument('--timestamp_threshold_s', type=float, default=None)
    p.add_argument('--ego_displacement_minimum_m', type=float, default=None)
    p.add_argument('--output_jsonl', required=True)
    p.add_argument('--manifest_json', required=True)
    p.add_argument('--resume', action='store_true')
    p.add_argument('--disable_tqdm', action='store_true')
    args = p.parse_args()

    resolved = _resolve_db_inputs(args)
    fp = fingerprint({
        'version': INDEX_VERSION,
        'split': args.split,
        'max_scenarios': args.max_scenarios,
        'map_version': args.nuplan_map_version,
        'map_names': args.nuplan_map_names,
        'scenario_types': args.nuplan_scenario_types,
        'log_names': args.nuplan_log_names,
        'timestamp_threshold_s': args.timestamp_threshold_s,
        'ego_displacement_minimum_m': args.ego_displacement_minimum_m,
    }, [*(resolved if isinstance(resolved, list) else ([resolved] if resolved else [])), args.nuplan_map_root])

    out = Path(args.output_jsonl); manifest = Path(args.manifest_json)
    out.parent.mkdir(parents=True, exist_ok=True); manifest.parent.mkdir(parents=True, exist_ok=True)
    if args.resume and out.exists() and manifest.exists():
        try:
            old = json.loads(manifest.read_text(encoding='utf-8'))
        except Exception:
            old = {}
        if old.get('status') == 'PASS' and old.get('index_fingerprint') == fp:
            print(json.dumps({'status':'PASS','resumed':True,'rows':old.get('rows'),'output_jsonl':str(out)}, indent=2))
            print('NUPLAN_SCENARIO_INDEX_CHECK=PASS')
            return

    adapter = NuPlanAdapter(
        scene_source='nuplan', data_root=args.nuplan_data_root, map_root=args.nuplan_map_root,
        sensor_root=args.nuplan_sensor_root, db_files=resolved, map_version=args.nuplan_map_version,
        split=args.split, num_workers=args.num_workers, scenario_types=args.nuplan_scenario_types,
        map_names=args.nuplan_map_names, log_names=args.nuplan_log_names,
        timestamp_threshold_s=args.timestamp_threshold_s,
        ego_displacement_minimum_m=args.ego_displacement_minimum_m,
    )
    rows = adapter.iter_scenario_index(args.max_scenarios)
    if not args.disable_tqdm:
        rows = tqdm(rows, total=(args.max_scenarios if args.max_scenarios > 0 else None), desc=f'{args.split}: nuPlan identity index', unit='scene', mininterval=1.0, dynamic_ncols=True)

    part = out.with_suffix(out.suffix + '.part')
    h = hashlib.sha256(); count = 0
    maps: Dict[str,int] = {}; types: Dict[str,int] = {}; logs: Dict[str,int] = {}
    started = time.perf_counter()
    with part.open('w', encoding='utf-8') as f:
        for row in rows:
            line = json.dumps(row, sort_keys=True, separators=(',', ':')) + '\n'
            f.write(line); h.update(line.encode('utf-8')); count += 1
            maps[str(row.get('map_name'))] = maps.get(str(row.get('map_name')),0)+1
            types[str(row.get('scenario_type'))] = types.get(str(row.get('scenario_type')),0)+1
            logs[str(row.get('log_name'))] = logs.get(str(row.get('log_name')),0)+1
    if count == 0:
        raise RuntimeError('nuPlan identity index is empty')
    part.replace(out)
    elapsed = time.perf_counter() - started
    rep = {
        'status':'PASS','version':INDEX_VERSION,'index_fingerprint':fp,'rows':count,
        'sha256':h.hexdigest(),'split':args.split,'map_counts':maps,'scenario_type_counts':types,
        'unique_logs':len(logs),'elapsed_s':elapsed,'rows_per_s':count/max(elapsed,1e-9),
        'db_files_expanded_count':len(adapter.db_files) if isinstance(adapter.db_files,list) else None,
        'output_jsonl':str(out),
        'interpretation':'Complete lightweight identity inventory only; not a passenger-complete training/evaluation sample.',
    }
    dump_json(manifest, rep)
    print(json.dumps(rep, indent=2, sort_keys=True))
    print('NUPLAN_SCENARIO_INDEX_CHECK=PASS')


if __name__ == '__main__':
    main()
