from __future__ import annotations

import csv
import hashlib
import json
import os
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

try:
    from pyproj import CRS  # type: ignore
except Exception:  # pragma: no cover
    CRS = None  # type: ignore

SUPPORTED_SUFFIXES = {".json", ".geojson", ".jsonl", ".ndjson", ".geojsonl", ".csv", ".yaml", ".yml"}
HTML_PREFIXES = (b"<!doctype html", b"<html", b"<?xml")
ERROR_PREFIXES = (b"too many requests", b"rate limit", b"access denied", b"forbidden", b"service unavailable")


@dataclass
class SourceInspection:
    path: str
    exists: bool = False
    is_dir: bool = False
    bytes: int = 0
    records: int = 0
    files: int = 0
    valid: bool = False
    sha256: Optional[str] = None
    content_kind: Optional[str] = None
    schema_variant: Optional[str] = None
    evidence_tier: Optional[str] = None
    authoritative: bool = False
    role_stats: Dict[str, Any] = field(default_factory=dict)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "path": self.path,
            "exists": self.exists,
            "is_dir": self.is_dir,
            "bytes": self.bytes,
            "records": self.records,
            "files": self.files,
            "valid": self.valid,
            "sha256": self.sha256,
            "content_kind": self.content_kind,
            "schema_variant": self.schema_variant,
            "evidence_tier": self.evidence_tier,
            "authoritative": self.authoritative,
            "role_stats": dict(self.role_stats),
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _first_non_ws(path: Path, size: int = 1024) -> bytes:
    with path.open("rb") as f:
        data = f.read(size).lstrip().lower()
    return data


def _metadata_from_payload(payload: Any) -> Tuple[Optional[str], Optional[str], Optional[str], bool]:
    if not isinstance(payload, dict):
        return None, None, None, False
    props = payload.get("properties") if isinstance(payload.get("properties"), dict) else {}
    meta = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    schema = payload.get("schema_variant") or props.get("schema_variant") or meta.get("schema_variant")
    tier = payload.get("evidence_tier") or props.get("evidence_tier") or meta.get("evidence_tier")
    authority = payload.get("authoritative")
    if authority is None:
        authority = props.get("authoritative", meta.get("authoritative", False))
    kind = None
    if payload.get("type") == "FeatureCollection":
        kind = "geojson_feature_collection"
    elif isinstance(payload.get("elements"), list):
        kind = "overpass_json"
    elif any(isinstance(payload.get(k), list) for k in ("features", "records", "nodes", "edges", "curbs", "entrances")):
        kind = "json_records"
    else:
        kind = "json_object"
    return str(kind), str(schema) if schema is not None else None, str(tier) if tier is not None else None, bool(authority)


def _count_payload_records(payload: Any) -> int:
    if isinstance(payload, list):
        return len(payload)
    if not isinstance(payload, dict):
        return 0
    if payload.get("type") == "FeatureCollection" and isinstance(payload.get("features"), list):
        return len(payload["features"])
    for key in ("elements", "features", "records", "nodes", "edges", "curbs", "entrances", "sidewalks", "candidates"):
        if isinstance(payload.get(key), list):
            return len(payload[key])
    return 1 if payload else 0




def _record_view(payload: Any, *, limit: int = 500) -> List[Dict[str, Any]]:
    """Return a bounded, schema-agnostic list of records for semantic checks."""
    rows: List[Dict[str, Any]] = []
    if isinstance(payload, list):
        candidates = payload
    elif isinstance(payload, dict) and payload.get("type") == "FeatureCollection":
        candidates = payload.get("features") or []
    elif isinstance(payload, dict):
        candidates = []
        for key in ("records", "features", "elements", "nodes", "edges", "curbs", "entrances", "sources"):
            value = payload.get(key)
            if isinstance(value, list):
                candidates = value
                break
        if not candidates:
            candidates = [payload]
    else:
        candidates = []
    for item in candidates[:limit]:
        if not isinstance(item, dict):
            continue
        props = item.get("properties") if isinstance(item.get("properties"), dict) else {}
        row = {**props, **{k: v for k, v in item.items() if k != "properties"}}
        rows.append(row)
    return rows


def _is_number(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def _semantic_role_check(role: Optional[str], rows: Sequence[Mapping[str, Any]], out: SourceInspection) -> None:
    if not role or not rows:
        return
    keys = set().union(*(set(r.keys()) for r in rows))
    out.role_stats["sampled_records"] = len(rows)
    out.role_stats["sampled_fields"] = sorted(str(k) for k in keys)[:200]

    if role == "osm":
        pedestrian_lines = 0
        carriageway_sidewalk_mislabels = 0
        for r in rows:
            geom = r.get("geometry") if isinstance(r.get("geometry"), dict) else {}
            gtype = str(geom.get("type") or "")
            kind = str(r.get("kind") or "").lower()
            highway = str(r.get("highway") or "").lower()
            footway = str(r.get("footway") or "").lower()
            sidewalk = str(r.get("sidewalk") or "").lower()
            # Accept both our normalized ``kind`` and raw/filtered OSM tags.
            # A carriageway carrying only ``sidewalk=*`` is deliberately NOT
            # counted as pedestrian geometry; separate footway/path geometry is.
            osm_pedestrian = (
                highway in {"footway", "path", "pedestrian", "steps"}
                or footway in {"sidewalk", "crossing", "access_aisle"}
            )
            if gtype in {"LineString", "MultiLineString"} and (
                kind in {"sidewalk", "crossing", "path", "steps"} or osm_pedestrian
            ):
                pedestrian_lines += 1
            if (
                gtype in {"LineString", "MultiLineString"}
                and kind == "sidewalk"
                and sidewalk in {"yes", "both", "left", "right", "separate"}
                and highway not in {"footway", "path", "pedestrian", "steps"}
                and footway not in {"sidewalk", "crossing", "access_aisle"}
            ):
                carriageway_sidewalk_mislabels += 1
        out.role_stats["routable_pedestrian_lines"] = pedestrian_lines
        out.role_stats["carriageway_sidewalk_mislabels"] = carriageway_sidewalk_mislabels
        if pedestrian_lines <= 0:
            out.errors.append("osm_has_no_routable_pedestrian_line_geometry")
        if carriageway_sidewalk_mislabels > 0:
            out.errors.append("osm_carriageway_centerline_mislabeled_as_sidewalk")
    elif role == "elevation":
        usable = sum(
            1 for r in rows
            if any(_is_number(r.get(k)) for k in ("elevation_m", "elevation", "z", "height_m", "running_slope", "slope"))
        )
        out.role_stats["usable_elevation_or_slope_records"] = usable
        if usable <= 0:
            out.errors.append("elevation_source_has_no_numeric_elevation_or_slope")
    elif role == "curb_inventory":
        # A Boolean curb-ramp flag is useful topology, but it is not enough to
        # establish board/alight interface feasibility. Publication evidence
        # needs at least one dimensional or slope measurement per usable record.
        measured = ("curb_height_m", "sidewalk_width_m", "deployment_clearance_m", "running_slope", "cross_slope", "ramp_slope", "landing_slope")
        usable = sum(1 for r in rows if any(_is_number(r.get(k)) for k in measured))
        ramp_flags = sum(1 for r in rows if isinstance(r.get("curb_ramp"), bool))
        out.role_stats["usable_physical_curb_records"] = usable
        out.role_stats["curb_ramp_flag_records"] = ramp_flags
        out.role_stats["measured_fields_present"] = [k for k in measured if any(_is_number(r.get(k)) for r in rows)]
        if usable <= 0:
            out.errors.append("curb_inventory_has_no_dimensional_or_slope_measurements")
    elif role == "curb_regulations":
        usable = sum(1 for r in rows if isinstance(r.get("legal_stop"), bool) and bool(r.get("legal_basis") or r.get("source")))
        out.role_stats["usable_legality_records"] = usable
        if usable <= 0:
            out.errors.append("curb_regulation_has_no_boolean_legal_stop_with_basis")
    elif role == "entrances":
        def is_point(r: Mapping[str, Any]) -> bool:
            geom = r.get("geometry") if isinstance(r.get("geometry"), dict) else {}
            coords = geom.get("coordinates") if isinstance(geom, dict) else None
            return (
                (geom.get("type") == "Point" and isinstance(coords, list) and len(coords) >= 2)
                or (_is_number(r.get("lon")) and _is_number(r.get("lat")))
                or (_is_number(r.get("longitude")) and _is_number(r.get("latitude")))
            )
        usable = sum(1 for r in rows if is_point(r))
        verified = sum(1 for r in rows if is_point(r) and not bool(r.get("is_proxy")) and str(r.get("kind") or "").lower() != "entrance_proxy")
        proxies = sum(1 for r in rows if is_point(r) and (bool(r.get("is_proxy")) or str(r.get("kind") or "").lower() == "entrance_proxy"))
        out.role_stats["usable_entrance_points"] = usable
        out.role_stats["verified_nonproxy_entrance_points"] = verified
        out.role_stats["proxy_entrance_points"] = proxies
        if usable <= 0:
            out.errors.append("entrance_source_has_no_point_entrances")
        elif verified <= 0:
            out.errors.append("entrance_source_contains_only_proxy_points")
    elif role == "manual_audit":
        usable = sum(1 for r in rows if r.get("audit_id") and r.get("observed_at") and r.get("auditor_id"))
        out.role_stats["usable_audit_records"] = usable
        if usable <= 0:
            out.errors.append("manual_audit_manifest_missing_audit_id_time_or_auditor")
    elif role == "provenance":
        usable = 0
        for r in rows:
            files = r.get("files")
            has_files = isinstance(files, list) and len(files) > 0
            if r.get("source_url") and r.get("license") and (r.get("path") or has_files):
                usable += 1
        out.role_stats["usable_provenance_records"] = usable
        if usable <= 0:
            out.errors.append("provenance_manifest_missing_source_url_license_or_files")


def inspect_source(path: str | Path | None, *, role: Optional[str] = None) -> SourceInspection:
    if path in (None, ""):
        out = SourceInspection(path="")
        out.errors.append("path_not_configured")
        return out
    p = Path(path)
    out = SourceInspection(path=str(p), exists=p.exists(), is_dir=p.is_dir() if p.exists() else False)
    if not p.exists():
        out.errors.append("missing")
        return out
    if p.is_dir():
        children = sorted(
            x for x in p.rglob("*")
            if x.is_file()
            and x.suffix.lower() in SUPPORTED_SUFFIXES
            and not x.name.lower().endswith((".report.json", ".provenance.json", ".manifest.json"))
        )
        if not children:
            out.errors.append("directory_contains_no_supported_data_files")
            return out
        child_reports = [inspect_source(x, role=role) for x in children]
        out.files = len(child_reports)
        out.bytes = sum(x.bytes for x in child_reports)
        out.records = sum(x.records for x in child_reports)
        out.valid = any(x.valid for x in child_reports) and not any(x.errors and not x.valid for x in child_reports)
        out.authoritative = any(x.authoritative for x in child_reports)
        tiers = sorted({x.evidence_tier for x in child_reports if x.evidence_tier})
        out.evidence_tier = "+".join(tiers) if tiers else None
        out.content_kind = "directory"
        for child in child_reports:
            for err in child.errors:
                out.errors.append(f"{Path(child.path).name}:{err}")
            for warning in child.warnings:
                out.warnings.append(f"{Path(child.path).name}:{warning}")
        if out.records <= 0:
            out.valid = False
            out.errors.append("directory_has_zero_records")
        return out

    out.files = 1
    out.bytes = p.stat().st_size
    if out.bytes <= 0:
        out.errors.append("empty_file")
        return out
    prefix = _first_non_ws(p)
    if prefix.startswith(HTML_PREFIXES) or prefix.startswith(ERROR_PREFIXES):
        out.errors.append("downloaded_error_or_html_page_instead_of_data")
        return out
    out.sha256 = _sha256(p)
    suffix = p.suffix.lower()
    semantic_rows: List[Dict[str, Any]] = []
    try:
        if suffix in {".json", ".geojson"}:
            with p.open("r", encoding="utf-8") as f:
                payload = json.load(f)
            out.records = _count_payload_records(payload)
            out.content_kind, out.schema_variant, out.evidence_tier, out.authoritative = _metadata_from_payload(payload)
            semantic_rows = _record_view(payload)
        elif suffix in {".jsonl", ".ndjson", ".geojsonl"}:
            n = 0
            first_obj: Any = None
            sampled: List[Dict[str, Any]] = []
            with p.open("r", encoding="utf-8") as f:
                for line_no, line in enumerate(f, 1):
                    if not line.strip():
                        continue
                    obj = json.loads(line)
                    if first_obj is None:
                        first_obj = obj
                    if isinstance(obj, dict) and len(sampled) < 500:
                        sampled.append(obj)
                    n += 1
            out.records = n
            out.content_kind = "json_lines"
            _, out.schema_variant, out.evidence_tier, out.authoritative = _metadata_from_payload(first_obj)
            semantic_rows = sampled
        elif suffix == ".csv":
            with p.open("r", encoding="utf-8-sig", newline="", errors="strict") as f:
                reader = csv.DictReader(f)
                if not reader.fieldnames:
                    raise ValueError("missing CSV header")
                sampled_csv = []
                n_csv = 0
                for row in reader:
                    n_csv += 1
                    if len(sampled_csv) < 500:
                        sampled_csv.append(dict(row))
                out.records = n_csv
            semantic_rows = sampled_csv
            out.content_kind = "csv"
        elif suffix in {".yaml", ".yml"}:
            try:
                import yaml  # type: ignore
            except Exception as exc:  # pragma: no cover
                raise RuntimeError("pyyaml is required to validate YAML") from exc
            payload = yaml.safe_load(p.read_text(encoding="utf-8"))
            out.records = _count_payload_records(payload)
            out.content_kind, out.schema_variant, out.evidence_tier, out.authoritative = _metadata_from_payload(payload)
            semantic_rows = _record_view(payload)
        else:
            out.errors.append(f"unsupported_suffix:{suffix}")
            return out
    except (UnicodeDecodeError, json.JSONDecodeError, csv.Error, ValueError, TypeError) as exc:
        out.errors.append(f"parse_error:{type(exc).__name__}:{exc}")
        return out

    if out.records <= 0:
        out.errors.append("zero_records")
        return out
    _semantic_role_check(role, semantic_rows, out)
    if role == "opensidewalks":
        variant = (out.schema_variant or "").lower()
        if variant in {"osw_minimal_candidate", "osm_derived_candidate", "osm_candidate"}:
            out.warnings.append("osm_derived_candidate_is_not_validated_opensidewalks")
        elif not variant:
            out.warnings.append("opensidewalks_schema_variant_not_declared")
    out.valid = not out.errors
    return out


def is_validated_osw(report: SourceInspection) -> bool:
    if not report.valid:
        return False
    variant = (report.schema_variant or "").lower()
    if variant in {"osw_minimal_candidate", "osm_derived_candidate", "osm_candidate"}:
        return False
    # A real OSW export may not carry our metadata.  In that case it can be
    # accepted as topology, but not as authoritative physical-accessibility truth.
    return report.content_kind in {"geojson_feature_collection", "json_records", "directory"}


def validate_georeference(path: str | Path | None) -> SourceInspection:
    report = inspect_source(path, role="georeference")
    if not report.valid or not path:
        return report
    p = Path(path)
    try:
        payload = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return report
    has_transform = bool(payload.get("local_crs")) or all(k in payload for k in ("origin_lat", "origin_lon"))
    if not has_transform:
        report.valid = False
        report.errors.append("georeference_missing_local_crs_or_origin_control_point")
    if not bool(payload.get("validated", False)):
        report.warnings.append("georeference_not_marked_validated")
    if payload.get("local_crs"):
        if not str(payload["local_crs"]).upper().startswith("EPSG:"):
            report.warnings.append("local_crs_is_not_normalized_epsg_string")
        if CRS is not None:
            try:
                parsed = CRS.from_user_input(payload["local_crs"])
                if not parsed.is_projected:
                    report.valid = False
                    report.errors.append("local_crs_is_not_projected")
            except Exception as exc:
                report.valid = False
                report.errors.append(f"local_crs_parse_error:{type(exc).__name__}")
    if bool(payload.get("crs_metadata_validated")) and not bool(payload.get("spatial_alignment_validated")):
        report.warnings.append("crs_metadata_validated_but_spatial_overlap_not_yet_validated")
    return report


def _as_path(value: Any, project_root: Path) -> Optional[Path]:
    if value in (None, ""):
        return None
    expanded = os.path.expandvars(str(value).format(project_root=str(project_root)))
    p = Path(expanded).expanduser()
    return p if p.is_absolute() else project_root / p


def _source_paths(city: str, cfg: Mapping[str, Any], external_root: Path, project_root: Path) -> Dict[str, Optional[Path]]:
    defaults: Dict[str, Path] = {
        "osm_source": external_root / "normalized" / "osm" / f"{city}_sidewalks.geojson",
        "opensidewalks_source": external_root / "normalized" / "opensidewalks" / f"{city}.geojson",
        "city_gis_dir": external_root / "normalized" / "city_gis" / city,
        "curb_inventory_jsonl": external_root / "normalized" / "curb_inventory" / f"{city}.jsonl",
        "curb_regulation_jsonl": external_root / "normalized" / "curb_regulations" / f"{city}.jsonl",
        "entrance_source": external_root / "normalized" / "entrances" / f"{city}.geojson",
        "elevation_source": external_root / "normalized" / "dem" / f"{city}.jsonl",
        "georeference_json": external_root / "georeference" / f"{city}.json",
        "manual_audit_manifest": external_root / "audits" / city / "manual_audit_manifest.jsonl",
        "provenance_manifest": external_root / "manifests" / f"{city}.json",
    }
    return {key: _as_path(cfg.get(key), project_root) if cfg.get(key) not in (None, "") else default for key, default in defaults.items()}


def validate_city_sources(
    city: str,
    city_cfg: Mapping[str, Any],
    *,
    external_root: Path,
    project_root: Path,
    policy: str,
) -> Dict[str, Any]:
    paths = _source_paths(city, city_cfg, external_root, project_root)
    roles = {
        "osm_source": "osm",
        "opensidewalks_source": "opensidewalks",
        "city_gis_dir": "city_gis",
        "curb_inventory_jsonl": "curb_inventory",
        "curb_regulation_jsonl": "curb_regulations",
        "entrance_source": "entrances",
        "elevation_source": "elevation",
        "manual_audit_manifest": "manual_audit",
        "provenance_manifest": "provenance",
    }
    inspections: Dict[str, SourceInspection] = {}
    for key, role in roles.items():
        inspections[key] = inspect_source(paths[key], role=role)
    inspections["georeference_json"] = validate_georeference(paths["georeference_json"])

    topology_ok = (
        inspections["osm_source"].valid
        or is_validated_osw(inspections["opensidewalks_source"])
        or (inspections["city_gis_dir"].valid and bool(city_cfg.get("city_gis_has_pedestrian_topology", False)))
    )
    georef_payload: Dict[str, Any] = {}
    georef_path = paths["georeference_json"]
    if georef_path and georef_path.exists() and georef_path.is_file():
        try:
            georef_payload = json.loads(georef_path.read_text(encoding="utf-8"))
        except Exception:
            georef_payload = {}

    requirements = {
        "pedestrian_topology": topology_ok,
        "georeference_parseable": inspections["georeference_json"].valid,
        "georeference_validated": inspections["georeference_json"].valid and bool(georef_payload.get("validated", False)),
        "curb_physical_inventory": inspections["curb_inventory_jsonl"].valid,
        "curb_legality_or_regulation": inspections["curb_regulation_jsonl"].valid,
        "entrance_layer": inspections["entrance_source"].valid,
        "elevation_or_measured_slope": inspections["elevation_source"].valid or bool(city_cfg.get("all_slopes_measured", False)),
        "authoritative_accessibility_evidence": (
            (inspections["city_gis_dir"].valid and (inspections["city_gis_dir"].authoritative or bool(city_cfg.get("city_gis_authoritative", False))))
            or (inspections["curb_inventory_jsonl"].valid and (inspections["curb_inventory_jsonl"].authoritative or bool(city_cfg.get("curb_inventory_authoritative", False))))
            or inspections["manual_audit_manifest"].valid
        ),
        "source_provenance_and_license_manifest": inspections["provenance_manifest"].valid,
    }
    bootstrap_required = ["pedestrian_topology", "georeference_parseable"]
    paper_required = [
        "pedestrian_topology",
        "georeference_parseable",
        "georeference_validated",
        "curb_physical_inventory",
        "curb_legality_or_regulation",
        "entrance_layer",
        "elevation_or_measured_slope",
        "authoritative_accessibility_evidence",
        "source_provenance_and_license_manifest",
    ]
    required = paper_required if policy == "paper" else bootstrap_required
    blockers = [name for name in required if not requirements[name]]
    warnings: List[str] = []
    if inspections["opensidewalks_source"].valid and not is_validated_osw(inspections["opensidewalks_source"]):
        warnings.append("opensidewalks_source_is_only_an_osm_derived_candidate")
    if policy == "bootstrap":
        missing_paper = [name for name in paper_required if not requirements[name]]
        if missing_paper:
            warnings.append("bootstrap_dataset_is_not_publication_ready:" + ",".join(missing_paper))

    return {
        "city": city,
        "source_policy": policy,
        "sources": {key: report.to_dict() for key, report in inspections.items()},
        "requirements": requirements,
        "required_for_policy": required,
        "blockers": blockers,
        "warnings": warnings,
        "ready": not blockers,
        "publication_ready": all(requirements[x] for x in paper_required),
    }


def validate_external_config(
    config: Mapping[str, Any],
    cities: Sequence[str],
    *,
    policy: str,
    project_root: str | Path,
) -> Dict[str, Any]:
    project_root = Path(project_root)
    external_root = _as_path(config.get("external_root", "{project_root}/data/external"), project_root)
    assert external_root is not None
    city_reports = [
        validate_city_sources(
            city,
            config["cities"][city],
            external_root=external_root,
            project_root=project_root,
            policy=policy,
        )
        for city in cities
    ]
    blockers = [f"{r['city']}:{b}" for r in city_reports for b in r["blockers"]]
    return {
        "schema_version": "2.0",
        "source_policy": policy,
        "external_root": str(external_root),
        "cities": city_reports,
        "blockers": blockers,
        "ready_for_requested_policy": not blockers,
        "publication_ready": all(r["publication_ready"] for r in city_reports),
    }
