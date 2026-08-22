#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import os
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

try:
    from tqdm.auto import tqdm  # type: ignore
except Exception:  # pragma: no cover - tqdm is optional in bare environments
    def tqdm(iterable=None, **kwargs):  # type: ignore
        return iterable if iterable is not None else []

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capplan.data.external_validation import inspect_source, validate_external_config
from capplan.utils.serialization import dump_json, iter_jsonl
from capplan.utils.build_fingerprint import file_inventory_fingerprint


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_config(path: str | Path) -> Dict[str, Any]:
    if yaml is None:
        raise RuntimeError("pyyaml is required for YAML configs; run `pip install -r requirements.txt` first")
    with Path(path).open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _path(value: str | None, base: Path = PROJECT_ROOT) -> Path | None:
    if value in (None, ""):
        return None
    p = Path(os.path.expandvars(str(value).format(project_root=str(PROJECT_ROOT)))).expanduser()
    return p if p.is_absolute() else base / p


def _split_csv(value: str | Iterable[str] | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return "+".join(str(x) for x in value if str(x))


def _run(cmd: List[str], dry_run: bool) -> None:
    rendered = " ".join(shlex.quote(x) for x in cmd)
    print(rendered, flush=True)
    if dry_run:
        return
    started = time.perf_counter()
    try:
        subprocess.check_call(cmd, cwd=PROJECT_ROOT)
    finally:
        elapsed = time.perf_counter() - started
        tool = Path(cmd[1] if len(cmd) > 1 and Path(cmd[0]).name.startswith("python") else cmd[0]).name
        print(f"[CAPPLAN_TIMING] tool={tool} elapsed_s={elapsed:.3f}", flush=True)


def _progress(items: Iterable[str], desc: str, disable: bool = False):
    seq = list(items)
    return seq if disable else tqdm(seq, desc=desc, unit="city")


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


def _download_overpass(query_file: Path, output_file: Path, endpoint: str, dry_run: bool, timeout_s: int) -> None:
    """Download atomically and reject HTTP/HTML/empty error payloads.

    This remains a small-area bootstrap convenience.  Publication-scale runs
    should use a local PBF plus scripts/prepare_osm_from_pbf.py.
    """
    output_file.parent.mkdir(parents=True, exist_ok=True)
    part = output_file.with_suffix(output_file.suffix + ".part")
    _run(
        [
            "curl",
            "--fail-with-body",
            "--retry",
            "4",
            "--retry-all-errors",
            "--connect-timeout",
            "30",
            "--max-time",
            str(max(timeout_s + 60, 120)),
            "-L",
            "-X",
            "POST",
            "-H",
            "Content-Type: application/x-www-form-urlencoded",
            "--data-urlencode",
            f"data@{query_file}",
            endpoint,
            "-o",
            str(part),
        ],
        dry_run,
    )
    if not dry_run:
        report = inspect_source(part, role="osm")
        if not report.valid:
            part.unlink(missing_ok=True)
            raise RuntimeError(f"Overpass returned unusable data for {output_file}: {report.errors}")
        part.replace(output_file)


def _concat_jsonl(inputs: Iterable[Path], output: Path) -> None:
    """Stream-concatenate city PUDO files without materializing all rows."""
    output.parent.mkdir(parents=True, exist_ok=True)
    part = output.with_suffix(output.suffix + ".part")
    part.unlink(missing_ok=True)
    count = 0
    with part.open("w", encoding="utf-8") as dst:
        for p in inputs:
            for row in iter_jsonl(p):
                dst.write(json.dumps(row, sort_keys=True) + "\n")
                count += 1
    part.replace(output)
    print(f"[CAPPLAN_PROGRESS] concatenated_pudo_rows={count} output={output}", flush=True)


def _resolve_city_db_dirs(split_cfg: Dict[str, Any], city_cfg: Dict[str, Any], city: str) -> List[str]:
    """Resolve the minimum DB-directory set needed for one city.

    Full train data is stored in four city-specific directories.  The previous
    pipeline passed all four directories to *every* city extraction and then
    filtered by map name, causing the complete train DB inventory to be scanned
    four times.  Prefer an explicit db_dirs_by_city mapping; otherwise infer a
    unique city-named directory for backward compatibility.
    """
    by_city = split_cfg.get("db_dirs_by_city") or {}
    if isinstance(by_city, dict) and by_city.get(city):
        value = by_city[city]
        return [x for x in (_split_csv(value) or "").split("+") if x]
    if city_cfg.get("db_dirs"):
        return [x for x in (_split_csv(city_cfg.get("db_dirs")) or "").split("+") if x]
    split_dirs = [x for x in (_split_csv(split_cfg.get("db_dirs")) or "").split("+") if x]
    if len(split_dirs) <= 1:
        return split_dirs
    aliases = {
        "boston": ("boston",),
        "pittsburgh": ("pittsburgh",),
        "vegas": ("vegas", "las_vegas", "las-vegas"),
        "singapore": ("singapore",),
    }.get(city, (city,))
    matches = [d for d in split_dirs if any(alias in Path(d).name.lower() for alias in aliases)]
    return matches if len(matches) == 1 else split_dirs


def _collect_split_db_files(db_root: Path, db_dirs: Iterable[str]) -> List[Path]:
    """List configured DB files without opening SQLite databases."""
    out: List[Path] = []
    for token in db_dirs:
        p = Path(token)
        candidate = p if p.is_absolute() else db_root / p
        if candidate.is_file() and candidate.suffix.lower() == ".db":
            out.append(candidate)
        elif candidate.is_dir():
            out.extend(x for x in candidate.rglob("*.db") if x.is_file())
    return sorted(set(out))


def _trusted_city_db_files_from_inspection(
    *, split_name: str, city: str, split_cfg: Dict[str, Any], db_root: Path,
    external_root: Path,
) -> tuple[List[str] | None, str]:
    """Reuse the audited city mapping for a mixed val/test DB directory.

    The city inspection opens every DB once and records its mapped city.  For a
    shared val/test directory, passing all DBs to every city's nuPlan builder
    repeats SQLite discovery/loading four times.  Reusing the inspection is
    lossless when the current file inventory fingerprint matches exactly.

    Returns ``(None, reason)`` when the report is absent/stale/ambiguous so the
    caller safely falls back to the original map-name filtering behavior.
    """
    split_dirs = [x for x in (_split_csv(split_cfg.get("db_dirs")) or "").split("+") if x]
    # This optimization is only needed when several cities share one or more
    # unspecialized DB directories. City-specific train dirs are already cheap.
    if len(split_cfg.get("cities") or []) <= 1 or not split_dirs:
        return None, "split_not_shared"
    if split_cfg.get("db_dirs_by_city"):
        return None, "city_specific_dirs_configured"

    report_path = external_root / "reports" / f"nuplan_db_cities.{split_name}.json"
    if not report_path.exists():
        return None, f"inspection_report_missing:{report_path}"
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return None, f"inspection_report_invalid:{type(exc).__name__}"
    if report.get("status") != "PASS" or report.get("split") != split_name:
        return None, "inspection_report_not_pass"
    if [str(x) for x in (report.get("db_dirs") or [])] != [str(x) for x in split_dirs]:
        return None, "inspection_db_dirs_mismatch"

    current = _collect_split_db_files(db_root, split_dirs)
    expected_fp = str(report.get("db_inventory_fingerprint") or "")
    if not expected_fp:
        return None, "inspection_report_has_no_inventory_fingerprint"
    if file_inventory_fingerprint(current) != expected_fp:
        return None, "inspection_inventory_changed"

    selected: List[str] = []
    ambiguous = 0
    for row in report.get("dbs") or []:
        mapped = [str(x) for x in (row.get("mapped_cities") or [])]
        if len(mapped) != 1:
            ambiguous += 1
            continue
        if mapped[0] == city:
            db = Path(str(row.get("db") or ""))
            if not db.exists():
                return None, f"inspection_db_missing:{db}"
            selected.append(str(db.resolve()))
    if ambiguous:
        return None, f"inspection_contains_{ambiguous}_ambiguous_db_rows"
    if not selected:
        return None, f"inspection_has_no_db_for_city:{city}"
    return sorted(set(selected)), "inspection_inventory_match"


def _write_db_manifest(path: Path, db_files: Iterable[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_suffix(path.suffix + ".part")
    lines = [str(Path(x).resolve()) for x in db_files]
    part.write_text("".join(x + "\n" for x in lines), encoding="utf-8")
    part.replace(path)


def _city_source(city: str, city_cfg: Dict[str, Any], key: str, default_root: Path, default_name: str) -> Path:
    explicit = city_cfg.get(key)
    if explicit:
        p = _path(explicit)
        assert p is not None
        return p
    return default_root / default_name


def _add_source_arg(cmd: List[str], flag: str, path: Path | None, dry_run: bool, required: bool = False, missing: List[str] | None = None) -> None:
    if path is None:
        if required:
            msg = f"missing required source {flag}=None"
            if missing is not None:
                missing.append(msg)
            else:
                raise RuntimeError(msg)
        return
    if dry_run or path.exists():
        cmd.extend([flag, str(path)])
    elif required:
        msg = f"missing required source {flag}={path}"
        if missing is not None:
            missing.append(msg)
        else:
            raise RuntimeError(msg)
    else:
        print(f"skip missing optional source {flag}={path}")


def _source_policy(config: Dict[str, Any], override: str | None = None) -> str:
    pol = str(override or config.get("quality", {}).get("source_policy", config.get("source_policy", "bootstrap"))).lower()
    if pol not in {"bootstrap", "hybrid", "paper"}:
        raise RuntimeError(f"unsupported source_policy={pol}; expected bootstrap, hybrid, or paper")
    return pol


def _write_source_preflight_report(
    config: Dict[str, Any],
    cities: Iterable[str],
    prepared_root: Path,
    policy: str,
    dry_run: bool,
) -> Dict[str, Any]:
    report = validate_external_config(
        config,
        list(cities),
        policy=policy,
        project_root=PROJECT_ROOT,
    )
    if not dry_run:
        prepared_root.mkdir(parents=True, exist_ok=True)
        dump_json(prepared_root / "external_source_preflight.json", report)
    if report.get("blockers") and not dry_run:
        raise RuntimeError(
            f"{policy} source preflight failed; fix these evidence categories before continuing: "
            + "; ".join(report["blockers"][:30])
        )
    return report


def _require_artifact(path: Path, label: str, dry_run: bool) -> None:
    if dry_run:
        return
    report = inspect_source(path)
    if not report.valid:
        raise RuntimeError(f"{label} is missing or invalid: {path}; errors={report.errors}")


def build_pipeline(config: Dict[str, Any], split_name: str, stages: set[str], dry_run: bool, disable_tqdm: bool = False, source_policy_override: str | None = None, cities_override: str | None = None, max_scenarios_override: int | None = None, episode_allowlist_override: str | None = None, skip_preflight: bool = False, skip_pudo_concat: bool = False) -> None:
    nuplan = config["nuplan"]
    external_root = _path(config.get("external_root", "{project_root}/data/external"))
    outputs_root = _path(config.get("outputs_root", "{project_root}/data/outputs"))
    assert external_root is not None and outputs_root is not None
    prepared_root = outputs_root / "prepared" / split_name
    # Keep compact, reviewable diagnostics under external/reports so a user can
    # package only reports instead of the entire prepared/dataset tree.
    reports_root = external_root / "reports" / "build" / split_name
    if not dry_run:
        reports_root.mkdir(parents=True, exist_ok=True)
    split_cfg = config["splits"][split_name]
    cities = split_cfg.get("cities") or list(config["cities"])
    if cities_override:
        wanted = [c.strip() for c in cities_override.replace(",", "+").split("+") if c.strip()]
        unknown = [c for c in wanted if c not in config["cities"]]
        if unknown:
            raise RuntimeError(f"unknown city override(s): {unknown}")
        cities = wanted
    max_per_city = int(split_cfg.get("max_scenarios_per_city", 100) if max_scenarios_override is None else max_scenarios_override)
    source_policy = _source_policy(config, source_policy_override)
    episode_allowlist = _path(episode_allowlist_override) if episode_allowlist_override else None
    if episode_allowlist is not None and not dry_run and not episode_allowlist.exists():
        raise FileNotFoundError(episode_allowlist)
    num_workers = int(os.environ.get("CAP_NUM_WORKERS", config.get("num_workers", 0)))
    graph_num_workers = int(os.environ.get("CAP_GRAPH_NUM_WORKERS", num_workers))
    seed = int(config.get("seed", 13))
    scene_selection_cfg = config.get("scene_selection", {}) or {}
    timestamp_threshold_s = split_cfg.get("timestamp_threshold_s", scene_selection_cfg.get("timestamp_threshold_s"))
    ego_displacement_minimum_m = split_cfg.get("ego_displacement_minimum_m", scene_selection_cfg.get("ego_displacement_minimum_m"))
    extract_checkpoint_interval = int(os.environ.get("CAP_EXTRACT_CHECKPOINT_INTERVAL", scene_selection_cfg.get("checkpoint_interval", 1000)))
    adopt_extract_partial = str(os.environ.get("CAP_ADOPT_EXTRACT_PARTIAL", "0")).strip().lower() in {"1", "true", "yes", "on"}
    min_nodes = int(config.get("quality", {}).get("min_graph_nodes", 100))
    min_edges = int(config.get("quality", {}).get("min_graph_edges", 150))
    min_paper_eligible = int(config.get("quality", {}).get("min_paper_eligible_pudos_per_episode", 2))
    min_hybrid_eligible = int(config.get("quality", {}).get("min_hybrid_eligible_pudos_per_episode", 2))
    min_episode_pudo_coverage = float(config.get("quality", {}).get("min_episode_pudo_coverage_rate", 0.80))
    endpoint = str(config.get("overpass", {}).get("endpoint", "https://overpass-api.de/api/interpreter"))
    timeout_s = int(config.get("overpass", {}).get("timeout_s", 180))

    scene_dirs: Dict[str, Path] = {}
    base_graph_dir = prepared_root / "accessibility_graphs"
    graph_dir = prepared_root / ("accessibility_graphs_hybrid" if source_policy == "hybrid" else "accessibility_graphs")
    pudo_city_files: List[Path] = []
    dataset_city_dirs: List[Path] = []

    if "queries" in stages or "download" in stages:
        qdir = external_root / "raw" / "osm_overpass" / "queries"
        for city in _progress(cities, f"{split_name}: overpass queries", disable_tqdm):
            q = _write_overpass_query(city, config["cities"][city], qdir, timeout_s)
            print(f"wrote {q}")

    if "download" in stages:
        download_cities = list(_progress(cities, f"{split_name}: overpass download", disable_tqdm))
        for idx, city in enumerate(download_cities):
            q = external_root / "raw" / "osm_overpass" / "queries" / f"{city}_sidewalks.overpassql"
            raw_out = external_root / "raw" / "osm_overpass" / f"{city}_sidewalks.json"
            normalized_out = external_root / "normalized" / "osm" / f"{city}_sidewalks.geojson"
            _download_overpass(q, raw_out, endpoint, dry_run, timeout_s)
            _run(
                [
                    sys.executable,
                    "scripts/normalize_overpass_json.py",
                    "--input_json",
                    str(raw_out),
                    "--output_geojson",
                    str(normalized_out),
                    "--source_url",
                    endpoint,
                ],
                dry_run,
            )
            if not dry_run:
                normalized_report = inspect_source(normalized_out, role="osm")
                if not normalized_report.valid:
                    raise RuntimeError(f"normalized OSM GeoJSON is unusable for {city}: {normalized_report.errors}")
            if not dry_run and idx + 1 < len(download_cities):
                time.sleep(float(config.get("overpass", {}).get("sleep_s", 8)))

    if "map_crs" in stages or "all" in stages:
        _run(
            [
                sys.executable,
                "scripts/inspect_nuplan_map_crs.py",
                "--config",
                str(config.get("_config_path", "configs/abilitybench_nuplan_real.yaml")),
                "--cities",
                "+".join(cities),
                "--output_dir",
                str(external_root / "georeference"),
            ],
            dry_run,
        )

    if stages.intersection({"preflight", "graphs", "pudo", "dataset", "all"}) and not skip_preflight:
        preflight_report = _write_source_preflight_report(config, cities, prepared_root, source_policy, dry_run)
        if not dry_run:
            dump_json(reports_root / "external_source_preflight.json", preflight_report)
    if stages == {"preflight"}:
        return

    for city in _progress(cities, f"{split_name}: extract/graphs/pudo", disable_tqdm):
        city_cfg = config["cities"][city]
        scene_dir = prepared_root / "scene_contexts" / city
        scene_dirs[city] = scene_dir
        city_db_dirs = _resolve_city_db_dirs(split_cfg, city_cfg, city)
        city_map_names = _split_csv(city_cfg.get("map_names"))
        db_root_path = _path(nuplan.get("db_root", nuplan["data_root"]))
        assert db_root_path is not None
        inspected_city_db_files, db_selection_reason = _trusted_city_db_files_from_inspection(
            split_name=split_name, city=city, split_cfg=split_cfg, db_root=db_root_path,
            external_root=external_root,
        )

        if "index" in stages:
            index_dir = prepared_root / "scenario_index"
            index_out = index_dir / f"{city}.jsonl"
            index_manifest = reports_root / f"nuplan_scenario_index.{city}.json"
            cmd = [
                sys.executable, "scripts/index_nuplan_scenarios.py",
                "--nuplan_data_root", str(_path(nuplan["data_root"])),
                "--nuplan_map_root", str(_path(nuplan["map_root"])),
                "--nuplan_db_root", str(db_root_path),
                "--nuplan_map_version", str(nuplan["map_version"]),
                "--split", split_name, "--max_scenarios", "0",
                "--num_workers", str(num_workers),
                "--output_jsonl", str(index_out),
                "--manifest_json", str(index_manifest),
                "--resume",
            ]
            if inspected_city_db_files:
                db_manifest = reports_root / f"nuplan_db_inputs.{city}.txt"
                if not dry_run:
                    _write_db_manifest(db_manifest, inspected_city_db_files)
                cmd.extend(["--nuplan_db_manifest", str(db_manifest)])
                db_desc = f"inspection_manifest:{len(inspected_city_db_files)}db"
            else:
                cmd.extend(["--nuplan_db_dirs", *_split_csv(city_db_dirs).split("+")])
                db_desc = f"dirs:{city_db_dirs} fallback_reason={db_selection_reason}"
            if city_map_names:
                cmd.extend(["--nuplan_map_names", city_map_names])
            if disable_tqdm:
                cmd.append("--disable_tqdm")
            print(f"[CAPPLAN_PROGRESS] split={split_name} city={city} stage=index db_selection={db_desc} workers={num_workers}", flush=True)
            _run(cmd, dry_run)

        if "extract" in stages or "all" in stages:
            cmd = [
                sys.executable,
                "scripts/extract_nuplan_scenes.py",
                "--nuplan_data_root",
                str(_path(nuplan["data_root"])),
                "--nuplan_map_root",
                str(_path(nuplan["map_root"])),
                "--nuplan_db_root",
                str(db_root_path),
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
                "--resume",
                "--checkpoint_interval",
                str(extract_checkpoint_interval),
            ]
            if timestamp_threshold_s is not None:
                cmd.extend(["--timestamp_threshold_s", str(timestamp_threshold_s)])
            if ego_displacement_minimum_m is not None:
                cmd.extend(["--ego_displacement_minimum_m", str(ego_displacement_minimum_m)])
            if adopt_extract_partial:
                cmd.append("--adopt_existing_partial")
            if inspected_city_db_files:
                db_manifest = reports_root / f"nuplan_db_inputs.{city}.txt"
                if not dry_run:
                    _write_db_manifest(db_manifest, inspected_city_db_files)
                cmd.extend(["--nuplan_db_manifest", str(db_manifest)])
                db_desc = f"inspection_manifest:{len(inspected_city_db_files)}db"
            else:
                cmd.extend(["--nuplan_db_dirs", *_split_csv(city_db_dirs).split("+")])
                db_desc = f"dirs:{city_db_dirs} fallback_reason={db_selection_reason}"
            if city_map_names:
                cmd.extend(["--nuplan_map_names", city_map_names])
            if disable_tqdm:
                cmd.append("--disable_tqdm")
            print(f"[CAPPLAN_PROGRESS] split={split_name} city={city} stage=extract db_selection={db_desc} workers={num_workers}", flush=True)
            _run(cmd, dry_run)

        if "graphs" in stages or "all" in stages:
            _require_artifact(scene_dir / "scenes.jsonl", f"nuPlan scene contexts for {city}", dry_run)
            osm_source = _city_source(city, city_cfg, "osm_source", external_root / "normalized" / "osm", f"{city}_sidewalks.geojson")
            opensidewalks = _city_source(city, city_cfg, "opensidewalks_source", external_root / "normalized" / "opensidewalks", f"{city}.geojson")
            city_gis = _city_source(city, city_cfg, "city_gis_dir", external_root / "normalized" / "city_gis", city)
            # Configs expose curated curb evidence as `curb_inventory_jsonl`.
            # The old `curb_inventory_source` lookup left the graph builder
            # blind to real curb attributes, which then propagated as 100%
            # missing PUDO core fields.
            curb_inventory = _city_source(city, city_cfg, "curb_inventory_jsonl", external_root / "normalized" / "curb_inventory", f"{city}.jsonl")
            entrances = _city_source(city, city_cfg, "entrance_source", external_root / "normalized" / "entrances", f"{city}.geojson")
            elevation = _city_source(city, city_cfg, "elevation_source", external_root / "normalized" / "dem", f"{city}.jsonl")
            georef = _path(city_cfg["georeference_json"])
            cmd = [
                sys.executable,
                "scripts/build_accessibility_graphs.py",
                "--scene_dataset_dir",
                str(scene_dir),
                "--georeference_json",
                str(georef),
                "--output_graph_dir",
                str(base_graph_dir),
                "--min_nodes_per_episode",
                str(min_nodes),
                "--min_edges_per_episode",
                str(min_edges),
                "--source_name",
                f"{city}_fused_external_accessibility",
                "--fail_on_synthetic",
                "--diagnostic_report_json",
                str(reports_root / f"graph_spatial_diagnostics.{city}.json"),
                "--source_report_json",
                str(reports_root / f"graph_source.{city}.json"),
                "--quality_report_json",
                str(reports_root / f"graph_quality.{city}.json"),
                "--timing_report_json",
                str(reports_root / f"graph_timing.{city}.json"),
                "--compact_storage",
                "--resume",
                "--num_workers",
                str(graph_num_workers),
            ]
            if disable_tqdm:
                cmd.append("--disable_tqdm")
            missing_graph_sources: List[str] = []
            _add_source_arg(cmd, "--osm_source", osm_source, dry_run, required=False, missing=missing_graph_sources)
            _add_source_arg(cmd, "--opensidewalks_source", opensidewalks, dry_run, required=False, missing=missing_graph_sources)
            _add_source_arg(cmd, "--city_gis_dir", city_gis, dry_run, required=False, missing=missing_graph_sources)
            _add_source_arg(cmd, "--curb_inventory_source", curb_inventory, dry_run, required=False, missing=missing_graph_sources)
            _add_source_arg(cmd, "--entrance_source", entrances, dry_run, required=False, missing=missing_graph_sources)
            _add_source_arg(cmd, "--elevation_source", elevation, dry_run, required=False, missing=missing_graph_sources)
            if missing_graph_sources:
                raise RuntimeError("missing graph sources: " + "; ".join(missing_graph_sources))
            _run(cmd, dry_run)

        if "pudo" in stages or "all" in stages:
            _require_artifact(scene_dir / "scenes.jsonl", f"nuPlan scene contexts for {city}", dry_run)
            _require_artifact(graph_dir, "accessibility graphs", dry_run)
            city_pudo = prepared_root / "pudo" / f"{city}.jsonl"
            pudo_city_files.append(city_pudo)
            city_curb_reg = _city_source(city, city_cfg, "curb_regulation_jsonl", external_root / "normalized" / "curb_regulations", f"{city}.jsonl")
            city_curb_inventory = _city_source(city, city_cfg, "curb_inventory_jsonl", external_root / "normalized" / "curb_inventory", f"{city}.jsonl")
            pudo_cmd = [
                sys.executable,
                "scripts/build_pudo_evidence.py",
                "--scene_dataset_dir",
                str(scene_dir),
                "--accessibility_graph_dir",
                str(base_graph_dir),
                "--output_pudo_evidence_jsonl",
                str(city_pudo),
                "--candidate_radius_m",
                str(config.get("pudo", {}).get("candidate_radius_m", 120)),
                "--source_name",
                f"{city}_city_curb_regulation_inventory" if source_policy == "paper" else f"{city}_bootstrap_osm_pudo_candidates",
                "--report_json",
                str(reports_root / f"pudo.{city}.json"),
                "--timing_report_json",
                str(reports_root / f"pudo_timing.{city}.json"),
                "--max_fallback_graph_candidates_per_episode",
                str(config.get("pudo", {}).get("max_fallback_graph_candidates_per_episode", 128)),
                "--fallback_candidate_spacing_m",
                str(config.get("pudo", {}).get("fallback_candidate_spacing_m", 20.0)),
                "--resume",
            ]
            missing_pudo_sources: List[str] = []
            pudo_cmd += ["--georeference_json", str(_path(city_cfg["georeference_json"]))]
            _add_source_arg(pudo_cmd, "--curb_inventory_jsonl", city_curb_inventory, dry_run, required=(source_policy == "paper"), missing=missing_pudo_sources)
            _add_source_arg(pudo_cmd, "--curb_regulation_jsonl", city_curb_reg, dry_run, required=(source_policy == "paper"), missing=missing_pudo_sources)
            for candidate_source in (city_cfg.get("pudo_candidate_sources") or []):
                candidate_path = _path(candidate_source)
                _add_source_arg(pudo_cmd, "--pudo_candidate_source", candidate_path, dry_run, required=False, missing=missing_pudo_sources)
            if missing_pudo_sources:
                raise RuntimeError("missing PUDO paper sources: " + "; ".join(missing_pudo_sources))
            if disable_tqdm:
                pudo_cmd.append("--disable_tqdm")
            print(f"[CAPPLAN_PROGRESS] split={split_name} city={city} stage=pudo", flush=True)
            _run(pudo_cmd, dry_run)

    combined_pudo = prepared_root / ("pudo_hybrid_evidence.jsonl" if source_policy == "hybrid" else "pudo_evidence.jsonl")
    if ("pudo" in stages or "all" in stages) and not dry_run and not skip_pudo_concat:
        if source_policy == "hybrid":
            raise RuntimeError(
                "hybrid policy consumes prebuilt pudo_hybrid_evidence.jsonl; "
                "build base PUDOs under bootstrap/paper first, then run build_hybrid_pudo_evidence.py"
            )
        _concat_jsonl(pudo_city_files, combined_pudo)
        print(f"wrote {combined_pudo}")

    service_requests = prepared_root / "service_requests.validated.jsonl"
    capability_profiles = prepared_root / "capability_profiles.generated.jsonl"
    fleet_jsonl = _path(config["fleet_jsonl"])
    if "service" in stages or "all" in stages:
        _require_artifact(graph_dir, "accessibility graphs", dry_run)
        if not dry_run and not fleet_jsonl.exists():
            raise RuntimeError(
                f"missing fleet interface file: {fleet_jsonl}. For bootstrap diagnostics you may copy "
                "configs/fleet.abilitybench.example.jsonl to that path. Paper results require measured/verified fleet interface metadata."
            )
        service_cfg = config.get("service", {}) or {}
        profile_source = _path(service_cfg["capability_profiles"]) if service_cfg.get("capability_profiles") else None
        trusted_entrance_sources = [str(x) for x in (service_cfg.get("trusted_entrance_sources") or [])]
        demand_cfg = _path(service_cfg["demand_sources_config"]) if service_cfg.get("demand_sources_config") else None
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
                *( ["--capability_profiles_jsonl", str(profile_source)] if profile_source else [] ),
                *( ["--demand_sources_config", str(demand_cfg)] if demand_cfg else [] ),
                "--num_requests_per_episode",
                str(service_cfg.get("num_requests_per_episode", 3)),
                "--source_name",
                ("abilitybench_calibrated_od" if source_policy == "paper" else
                 "abilitybench_hybrid_request_od" if source_policy == "hybrid" else
                 "abilitybench_bootstrap_od_not_for_paper"),
                "--report_json",
                str(reports_root / "service_layer.json"),
                "--seed",
                str(seed),
                *( ["--episode_allowlist", str(episode_allowlist)] if episode_allowlist else [] ),
                *( ["--require_trusted_entrances"] if source_policy == "paper" else [] ),
                *( sum((["--trusted_entrance_source", src] for src in trusted_entrance_sources), []) if source_policy == "paper" else [] ),
                *( ["--allow_non_entrance_od"] if source_policy in {"bootstrap", "hybrid"} else [] ),
            ],
            dry_run,
        )

    for city in _progress(cities, f"{split_name}: dataset build", disable_tqdm):
        city_cfg = config["cities"][city]
        city_db_dirs = _resolve_city_db_dirs(split_cfg, city_cfg, city)
        city_map_names = _split_csv(city_cfg.get("map_names"))
        city_dataset_name = f"abilitybench_av_hybrid_{split_name}_{city}" if source_policy == "hybrid" else f"abilitybench_av_{split_name}_{city}"
        city_dataset = outputs_root / "datasets" / city_dataset_name
        dataset_city_dirs.append(city_dataset)
        if "dataset" in stages or "all" in stages:
            _require_artifact(graph_dir, "accessibility graphs", dry_run)
            _require_artifact(combined_pudo, "combined PUDO evidence", dry_run)
            _require_artifact(service_requests, "service requests", dry_run)
            _require_artifact(capability_profiles, "capability profiles", dry_run)
            cmd = [
                sys.executable,
                "scripts/build_dataset.py",
                *( ["--paper_mode", "--require_validated_georeference"] if source_policy == "paper" else [] ),
                "--source_policy",
                source_policy,
                "--external_source_preflight_json",
                str(prepared_root / "external_source_preflight.json"),
                "--scene_source",
                "nuplan",
                "--nuplan_data_root",
                str(_path(nuplan["data_root"])),
                "--nuplan_map_root",
                str(_path(nuplan["map_root"])),
                "--nuplan_db_root",
                str(db_root_path),
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
                *( ["--reject_synthetic_accessibility", "--reject_proxy_entrances"] if source_policy == "paper" else ["--allow_bootstrap_service_nodes"] ),
                *( sum((["--trusted_entrance_source", src] for src in [str(x) for x in ((config.get("service", {}) or {}).get("trusted_entrance_sources") or [])]), []) if source_policy == "paper" else [] ),
                "--min_graph_nodes",
                str(min_nodes),
                "--min_graph_edges",
                str(min_edges),
                "--min_paper_eligible_pudos_per_episode",
                str(min_paper_eligible),
                "--min_hybrid_eligible_pudos_per_episode",
                str(min_hybrid_eligible),
                "--output_dir",
                str(city_dataset),
                "--strict",
            ]
            if disable_tqdm:
                cmd.append("--disable_tqdm")
            if city_map_names:
                cmd.extend(["--nuplan_map_names", city_map_names])
            _run(cmd, dry_run)
            if not dry_run:
                audit_cmd = [
                    sys.executable,
                    "scripts/audit_dataset_quality.py",
                    "--dataset_dir",
                    str(city_dataset),
                    *( ["--paper_mode", "--fail_if_not_publication_ready"] if source_policy == "paper" else [] ),
                    "--min_graph_nodes",
                    str(min_nodes),
                    "--min_graph_edges",
                    str(min_edges),
                    "--min_paper_eligible_pudos_per_episode",
                    str(min_paper_eligible),
                    "--min_episode_pudo_coverage_rate",
                    str(min_episode_pudo_coverage),
                    "--output",
                    str(reports_root / f"dataset_quality.{city}.json"),
                ]
                _run(audit_cmd, dry_run)
                _run([
                    sys.executable,
                    "scripts/diagnose_capplan_outputs.py",
                    "--dataset_dir", str(city_dataset),
                    "--accessibility_graph_dir", str(graph_dir),
                    "--service_requests_jsonl", str(service_requests),
                    "--pudo_evidence_jsonl", str(combined_pudo),
                    "--output", str(reports_root / f"dataset_diagnostics.{city}.json"),
                ], dry_run)

    merged_dataset_name = f"abilitybench_av_hybrid_{split_name}" if source_policy == "hybrid" else f"abilitybench_av_{split_name}"
    merged_dataset = outputs_root / "datasets" / merged_dataset_name
    if "merge" in stages or "all" in stages:
        for city_dataset in dataset_city_dirs:
            _require_artifact(city_dataset / "dataset_manifest.json", f"city dataset manifest {city_dataset.name}", dry_run)
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
    p.add_argument("--split", choices=["train", "val", "test"], default="train")
    p.add_argument("--stages", default="all", help="Comma list: queries,download,map_crs,preflight,extract,graphs,pudo,service,dataset,merge,all. Online download is never implied by all.")
    p.add_argument("--dry_run", action="store_true", help="Print commands without executing them.")
    p.add_argument("--disable_tqdm", action="store_true", help="Disable city/stage and dataset progress bars.")
    p.add_argument("--source_policy", choices=["bootstrap", "hybrid", "paper"], default=None, help="bootstrap=source-only fail-closed bring-up; hybrid=real geometry/topology plus explicitly simulated missing benchmark truth; paper=audited city evidence only.")
    p.add_argument("--cities", default=None, help="Optional comma/plus-separated city subset for fast diagnostics, e.g. boston or boston+vegas.")
    p.add_argument("--max_scenarios_per_city", type=int, default=None, help="Override config split max_scenarios_per_city. For real nuPlan data, 0 means all matching scenarios.")
    p.add_argument("--episode_allowlist", default=None, help="Optional split-level text/JSON episode allowlist produced from audited paper evidence. Applied to service and dataset stages; candidate extraction/graphs/PUDO remain complete.")
    p.add_argument("--skip_preflight", action="store_true", help="Internal/advanced: skip automatic external-source preflight when a parent orchestration already ran it.")
    p.add_argument("--skip_pudo_concat", action="store_true", help="Internal/advanced: leave per-city PUDO files unmerged for parallel city orchestration.")
    args = p.parse_args()
    stages = {x.strip() for x in args.stages.split(",") if x.strip()}
    config = _load_config(args.config)
    config["_config_path"] = args.config
    build_pipeline(config, args.split, stages, args.dry_run, args.disable_tqdm, args.source_policy, args.cities, args.max_scenarios_per_city, args.episode_allowlist, args.skip_preflight, args.skip_pudo_concat)


if __name__ == "__main__":
    main()
