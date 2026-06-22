#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - dependency is installed via requirements.txt
    yaml = None

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capplan.utils.serialization import read_jsonl, write_jsonl


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_config(path: str | Path) -> Dict[str, Any]:
    if yaml is None:
        raise RuntimeError("pyyaml is required for YAML configs; run `pip install -r requirements.txt` first")
    with Path(path).open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _path(value: str | None, base: Path = PROJECT_ROOT) -> Path | None:
    if value in (None, ""):
        return None
    p = Path(str(value).format(project_root=str(PROJECT_ROOT))).expanduser()
    return p if p.is_absolute() else base / p


def _split_csv(value: str | Iterable[str] | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return "+".join(str(x) for x in value if str(x))


def _run(cmd: List[str], dry_run: bool) -> None:
    rendered = " ".join(shlex.quote(x) for x in cmd)
    print(rendered)
    if not dry_run:
        subprocess.check_call(cmd, cwd=PROJECT_ROOT)


def _write_overpass_query(city: str, city_cfg: Dict[str, Any], out_dir: Path, timeout_s: int) -> Path:
    bbox = city_cfg.get("bbox")
    if not bbox or len(bbox) != 4:
        raise RuntimeError(f"city {city} requires bbox=[south, west, north, east] for Overpass query generation")
    south, west, north, east = bbox
    out_dir.mkdir(parents=True, exist_ok=True)
    q = out_dir / f"{city}_sidewalks.overpassql"
    q.write_text(
        f"""[out:json][timeout:{timeout_s}];
(
  way["highway"~"footway|path|pedestrian|steps"]({south},{west},{north},{east});
  way["footway"~"sidewalk|crossing"]({south},{west},{north},{east});
  way["sidewalk"~"yes|both|left|right|separate"]({south},{west},{north},{east});
  node["kerb"]({south},{west},{north},{east});
  node["curb_ramp"]({south},{west},{north},{east});
  node["entrance"]({south},{west},{north},{east});
  node["highway"="crossing"]({south},{west},{north},{east});
);
out body geom;
>;
out skel qt;
""",
        encoding="utf-8",
    )
    return q


def _download_overpass(query_file: Path, output_file: Path, endpoint: str, dry_run: bool) -> None:
    output_file.parent.mkdir(parents=True, exist_ok=True)
    _run(
        [
            "curl",
            "-L",
            "-X",
            "POST",
            "-H",
            "Content-Type: application/x-www-form-urlencoded",
            "--data-urlencode",
            f"data@{query_file}",
            endpoint,
            "-o",
            str(output_file),
        ],
        dry_run,
    )


def _concat_jsonl(inputs: Iterable[Path], output: Path) -> None:
    rows: List[Dict[str, Any]] = []
    for p in inputs:
        rows.extend(read_jsonl(p))
    write_jsonl(output, rows)


def _city_source(city: str, city_cfg: Dict[str, Any], key: str, default_root: Path, default_name: str) -> Path:
    explicit = city_cfg.get(key)
    if explicit:
        p = _path(explicit)
        assert p is not None
        return p
    return default_root / default_name


def _add_source_arg(cmd: List[str], flag: str, path: Path, dry_run: bool, required: bool = False) -> None:
    if dry_run or required or path.exists():
        cmd.extend([flag, str(path)])
    else:
        print(f"skip missing optional source {flag}={path}")


def build_pipeline(config: Dict[str, Any], split_name: str, stages: set[str], dry_run: bool) -> None:
    nuplan = config["nuplan"]
    external_root = _path(config.get("external_root", "/data0/senzeyu2/dataset/abilitybench_external"))
    outputs_root = _path(config.get("outputs_root", "outputs"))
    assert external_root is not None and outputs_root is not None
    prepared_root = outputs_root / "prepared" / split_name
    split_cfg = config["splits"][split_name]
    cities = split_cfg.get("cities") or list(config["cities"])
    max_per_city = int(split_cfg.get("max_scenarios_per_city", 100))
    num_workers = int(config.get("num_workers", 0))
    seed = int(config.get("seed", 13))
    min_nodes = int(config.get("quality", {}).get("min_graph_nodes", 100))
    min_edges = int(config.get("quality", {}).get("min_graph_edges", 150))
    max_missing = float(config.get("quality", {}).get("max_core_pudo_missing_rate", 0.05))
    endpoint = str(config.get("overpass", {}).get("endpoint", "https://overpass-api.de/api/interpreter"))
    timeout_s = int(config.get("overpass", {}).get("timeout_s", 180))

    scene_dirs: Dict[str, Path] = {}
    graph_dir = prepared_root / "accessibility_graphs"
    pudo_city_files: List[Path] = []
    dataset_city_dirs: List[Path] = []

    if "queries" in stages or "all" in stages or "download" in stages:
        qdir = external_root / "osm" / "queries"
        for city in cities:
            q = _write_overpass_query(city, config["cities"][city], qdir, timeout_s)
            print(f"wrote {q}")

    if "download" in stages or "all" in stages:
        for idx, city in enumerate(cities):
            q = external_root / "osm" / "queries" / f"{city}_sidewalks.overpassql"
            out = external_root / "osm" / f"{city}_sidewalks.json"
            _download_overpass(q, out, endpoint, dry_run)
            if not dry_run and idx + 1 < len(cities):
                time.sleep(float(config.get("overpass", {}).get("sleep_s", 8)))

    for city in cities:
        city_cfg = config["cities"][city]
        scene_dir = prepared_root / "scene_contexts" / city
        scene_dirs[city] = scene_dir
        city_db_dirs = split_cfg.get("db_dirs") or city_cfg.get("db_dirs")
        city_map_names = _split_csv(city_cfg.get("map_names"))

        if "extract" in stages or "all" in stages:
            cmd = [
                sys.executable,
                "scripts/extract_nuplan_scenes.py",
                "--nuplan_data_root",
                str(_path(nuplan["data_root"])),
                "--nuplan_map_root",
                str(_path(nuplan["map_root"])),
                "--nuplan_db_root",
                str(_path(nuplan.get("db_root", nuplan["data_root"]))),
                "--nuplan_db_dirs",
                *_split_csv(city_db_dirs).split("+"),
                "--nuplan_map_version",
                str(nuplan["map_version"]),
                "--split",
                split_name,
                "--max_scenarios",
                str(max_per_city),
                "--num_workers",
                str(num_workers),
                "--seed",
                str(seed),
                "--output_dir",
                str(scene_dir),
            ]
            if city_map_names:
                cmd.extend(["--nuplan_map_names", city_map_names])
            _run(cmd, dry_run)

        if "graphs" in stages or "all" in stages:
            osm_source = _city_source(city, city_cfg, "osm_source", external_root / "osm", f"{city}_sidewalks.json")
            opensidewalks = _city_source(city, city_cfg, "opensidewalks_source", external_root / "opensidewalks", f"{city}.geojson")
            city_gis = _city_source(city, city_cfg, "city_gis_dir", external_root / "city_gis", city)
            curb_inventory = _city_source(city, city_cfg, "curb_inventory_source", external_root / "curb_inventory", f"{city}.jsonl")
            entrances = _city_source(city, city_cfg, "entrance_source", external_root / "entrances", f"{city}.geojson")
            elevation = _city_source(city, city_cfg, "elevation_source", external_root / "dem", f"{city}.jsonl")
            georef = _path(city_cfg["georeference_json"])
            cmd = [
                sys.executable,
                "scripts/build_accessibility_graphs.py",
                "--scene_dataset_dir",
                str(scene_dir),
                "--georeference_json",
                str(georef),
                "--output_graph_dir",
                str(graph_dir),
                "--min_nodes_per_episode",
                str(min_nodes),
                "--min_edges_per_episode",
                str(min_edges),
                "--source_name",
                f"{city}_osm_opensidewalks_citygis_dem",
                "--fail_on_synthetic",
            ]
            _add_source_arg(cmd, "--osm_source", osm_source, dry_run, required=True)
            _add_source_arg(cmd, "--opensidewalks_source", opensidewalks, dry_run)
            _add_source_arg(cmd, "--city_gis_dir", city_gis, dry_run)
            _add_source_arg(cmd, "--curb_inventory_source", curb_inventory, dry_run)
            _add_source_arg(cmd, "--entrance_source", entrances, dry_run)
            _add_source_arg(cmd, "--elevation_source", elevation, dry_run)
            _run(cmd, dry_run)

        if "pudo" in stages or "all" in stages:
            city_pudo = prepared_root / "pudo" / f"{city}.jsonl"
            pudo_city_files.append(city_pudo)
            city_curb_reg = _city_source(city, city_cfg, "curb_regulation_jsonl", external_root / "curb_regulations", f"{city}.jsonl")
            city_curb_inventory = _city_source(city, city_cfg, "curb_inventory_jsonl", external_root / "curb_inventory", f"{city}.jsonl")
            _run(
                [
                    sys.executable,
                    "scripts/build_pudo_evidence.py",
                    "--scene_dataset_dir",
                    str(scene_dir),
                    "--accessibility_graph_dir",
                    str(graph_dir),
                    "--curb_inventory_jsonl",
                    str(city_curb_inventory),
                    "--curb_regulation_jsonl",
                    str(city_curb_reg),
                    "--output_pudo_evidence_jsonl",
                    str(city_pudo),
                    "--candidate_radius_m",
                    str(config.get("pudo", {}).get("candidate_radius_m", 120)),
                    "--max_core_missing_rate",
                    str(max_missing),
                    "--source_name",
                    f"{city}_city_curb_regulation_inventory",
                    "--fail_on_missing_core_evidence",
                ],
                dry_run,
            )

    combined_pudo = prepared_root / "pudo_evidence.jsonl"
    if ("pudo" in stages or "all" in stages) and not dry_run:
        _concat_jsonl(pudo_city_files, combined_pudo)
        print(f"wrote {combined_pudo}")

    service_requests = prepared_root / "service_requests.validated.jsonl"
    capability_profiles = prepared_root / "capability_profiles.generated.jsonl"
    fleet_jsonl = _path(config["fleet_jsonl"])
    if "service" in stages or "all" in stages:
        _run(
            [
                sys.executable,
                "scripts/build_service_layer.py",
                "--accessibility_graph_dir",
                str(graph_dir),
                "--fleet_jsonl",
                str(fleet_jsonl),
                "--output_service_requests_jsonl",
                str(service_requests),
                "--output_capability_profiles_jsonl",
                str(capability_profiles),
                "--num_requests_per_episode",
                str(config.get("service", {}).get("num_requests_per_episode", 3)),
                "--source_name",
                "abilitybench_calibrated_od",
                "--report_json",
                str(prepared_root / "service_layer_report.json"),
                "--seed",
                str(seed),
            ],
            dry_run,
        )

    for city in cities:
        city_cfg = config["cities"][city]
        city_db_dirs = split_cfg.get("db_dirs") or city_cfg.get("db_dirs")
        city_map_names = _split_csv(city_cfg.get("map_names"))
        city_dataset = outputs_root / "datasets" / f"abilitybench_av_{split_name}_{city}"
        dataset_city_dirs.append(city_dataset)
        if "dataset" in stages or "all" in stages:
            cmd = [
                sys.executable,
                "scripts/build_dataset.py",
                "--paper_mode",
                "--scene_source",
                "nuplan",
                "--nuplan_data_root",
                str(_path(nuplan["data_root"])),
                "--nuplan_map_root",
                str(_path(nuplan["map_root"])),
                "--nuplan_db_root",
                str(_path(nuplan.get("db_root", nuplan["data_root"]))),
                "--nuplan_db_dirs",
                *_split_csv(city_db_dirs).split("+"),
                "--nuplan_map_version",
                str(nuplan["map_version"]),
                "--split",
                split_name,
                "--max_scenarios",
                str(max_per_city),
                "--num_workers",
                str(num_workers),
                "--accessibility_source",
                "prepared_jsonl",
                "--accessibility_graph_dir",
                str(graph_dir),
                "--pudo_source",
                "evidence_jsonl",
                "--pudo_evidence_jsonl",
                str(combined_pudo),
                "--service_layer_source",
                "real_jsonl",
                "--service_requests_jsonl",
                str(service_requests),
                "--capability_profiles_jsonl",
                str(capability_profiles),
                "--fleet_jsonl",
                str(fleet_jsonl),
                "--reject_synthetic_accessibility",
                "--reject_proxy_entrances",
                "--min_graph_nodes",
                str(min_nodes),
                "--min_graph_edges",
                str(min_edges),
                "--max_core_pudo_missing_rate",
                str(max_missing),
                "--output_dir",
                str(city_dataset),
                "--strict",
                "--disable_tqdm",
            ]
            if city_map_names:
                cmd.extend(["--nuplan_map_names", city_map_names])
            _run(cmd, dry_run)

    merged_dataset = outputs_root / "datasets" / f"abilitybench_av_{split_name}"
    if "merge" in stages or "all" in stages:
        _run(
            [
                sys.executable,
                "scripts/merge_datasets.py",
                "--input_dirs",
                *[str(x) for x in dataset_city_dirs],
                "--output_dir",
                str(merged_dataset),
                "--strict",
            ],
            dry_run,
        )


def main() -> None:
    p = argparse.ArgumentParser(description="Prepare OSM/OpenSidewalks/city-GIS/curb/DEM inputs and build nuPlan-based AbilityBench datasets.")
    p.add_argument("--config", default="configs/abilitybench_nuplan_real.yaml")
    p.add_argument("--split", choices=["train", "val"], default="train")
    p.add_argument("--stages", default="all", help="Comma list: queries,download,extract,graphs,pudo,service,dataset,merge,all")
    p.add_argument("--dry_run", action="store_true", help="Print commands without executing them.")
    args = p.parse_args()
    stages = {x.strip() for x in args.stages.split(",") if x.strip()}
    build_pipeline(_load_config(args.config), args.split, stages, args.dry_run)


if __name__ == "__main__":
    main()
