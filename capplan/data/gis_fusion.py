"""GIS fusion utilities for AbilityBench-AV accessibility construction.

The implementation is intentionally file-first and deterministic: OSM / OpenSidewalks
/ city GIS layers are read from user-supplied Overpass JSON, GeoJSON, JSONL, or
JSON exports.  No network calls are made inside the builder.  Coordinate conversion
uses an explicit georeference configuration so local nuPlan-map coordinates and
WGS84 GIS coordinates are never silently mixed.
"""
from __future__ import annotations

import csv
import json
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

try:  # Optional: exact projected CRS transforms when pyproj is installed.
    from pyproj import Transformer as _PyprojTransformer  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    _PyprojTransformer = None

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - yaml is project dependency but keep robust
    yaml = None

from capplan.data.schemas import AccessibilityEdge, AccessibilityGraph, AccessibilityNode, Pose2D, to_dict
from capplan.utils.serialization import iter_jsonl, load_json, read_jsonl

EARTH_RADIUS_M = 6_378_137.0


def _parse_epsg_utm(crs: Any) -> tuple[int, bool] | None:
    """Return (zone, northern_hemisphere) for EPSG:326xx/327xx CRS strings.

    pyproj is the preferred backend, but publication-scale dataset builds should
    not silently fall back to a local tangent plane when pyproj is absent.  This
    lightweight UTM path covers the nuPlan city configs used by AbilityBench and
    keeps WGS84<->projected map-frame alignment deterministic in minimal envs.
    """
    if crs is None:
        return None
    m = re.search(r"epsg\s*:\s*(326|327)(\d{2})", str(crs).lower())
    if not m:
        m = re.search(r"\b(326|327)(\d{2})\b", str(crs).lower())
    if not m:
        return None
    zone = int(m.group(2))
    if not (1 <= zone <= 60):
        return None
    return zone, m.group(1) == "326"


def _utm_forward(lon_deg: float, lat_deg: float, zone: int, northern: bool) -> tuple[float, float]:
    """Pure-Python WGS84 -> UTM easting/northing fallback.

    Accuracy is comfortably below centimetres for the city-scale extents used
    here, which is much smaller than graph snapping/cropping tolerances.
    """
    a = 6378137.0
    f = 1 / 298.257223563
    k0 = 0.9996
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    lon0 = math.radians((zone - 1) * 6 - 180 + 3)
    n = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
    t = math.tan(lat) ** 2
    c = ep2 * math.cos(lat) ** 2
    A = math.cos(lat) * (lon - lon0)
    m = a * ((1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256) * lat
             - (3 * e2 / 8 + 3 * e2 ** 2 / 32 + 45 * e2 ** 3 / 1024) * math.sin(2 * lat)
             + (15 * e2 ** 2 / 256 + 45 * e2 ** 3 / 1024) * math.sin(4 * lat)
             - (35 * e2 ** 3 / 3072) * math.sin(6 * lat))
    easting = k0 * n * (A + (1 - t + c) * A ** 3 / 6 + (5 - 18 * t + t ** 2 + 72 * c - 58 * ep2) * A ** 5 / 120) + 500000.0
    northing = k0 * (m + n * math.tan(lat) * (A ** 2 / 2 + (5 - t + 9 * c + 4 * c ** 2) * A ** 4 / 24 + (61 - 58 * t + t ** 2 + 600 * c - 330 * ep2) * A ** 6 / 720))
    if not northern:
        northing += 10000000.0
    return float(easting), float(northing)


def _utm_inverse(easting: float, northing: float, zone: int, northern: bool) -> tuple[float, float]:
    """Pure-Python UTM easting/northing -> WGS84 fallback."""
    a = 6378137.0
    f = 1 / 298.257223563
    k0 = 0.9996
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)
    x = float(easting) - 500000.0
    y = float(northing)
    if not northern:
        y -= 10000000.0
    lon0 = math.radians((zone - 1) * 6 - 180 + 3)
    m = y / k0
    mu = m / (a * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256))
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    j1 = 3 * e1 / 2 - 27 * e1 ** 3 / 32
    j2 = 21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32
    j3 = 151 * e1 ** 3 / 96
    j4 = 1097 * e1 ** 4 / 512
    fp = mu + j1 * math.sin(2 * mu) + j2 * math.sin(4 * mu) + j3 * math.sin(6 * mu) + j4 * math.sin(8 * mu)
    c1 = ep2 * math.cos(fp) ** 2
    t1 = math.tan(fp) ** 2
    n1 = a / math.sqrt(1 - e2 * math.sin(fp) ** 2)
    r1 = a * (1 - e2) / (1 - e2 * math.sin(fp) ** 2) ** 1.5
    d = x / (n1 * k0)
    lat = fp - (n1 * math.tan(fp) / r1) * (d ** 2 / 2 - (5 + 3 * t1 + 10 * c1 - 4 * c1 ** 2 - 9 * ep2) * d ** 4 / 24 + (61 + 90 * t1 + 298 * c1 + 45 * t1 ** 2 - 252 * ep2 - 3 * c1 ** 2) * d ** 6 / 720)
    lon = lon0 + (d - (1 + 2 * t1 + c1) * d ** 3 / 6 + (5 - 2 * c1 + 28 * t1 - 3 * c1 ** 2 + 8 * ep2 + 24 * t1 ** 2) * d ** 5 / 120) / math.cos(fp)
    return float(math.degrees(lon)), float(math.degrees(lat))


@dataclass(frozen=True)
class SceneContext:
    episode_id: str
    map_name: Optional[str] = None
    route_polyline: List[List[float]] = field(default_factory=list)
    bbox: Optional[Tuple[float, float, float, float]] = None
    # Intended Euclidean buffer around the route corridor. ``bbox`` remains a
    # conservative envelope for diagnostics/backward compatibility, while the
    # graph builder can use chunked route envelopes that are a strict superset
    # of this corridor but much tighter than one global rectangle.
    corridor_radius_m: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class GISFeature:
    feature_id: str
    kind: str
    geometry: List[List[float]]
    tags: Dict[str, Any] = field(default_factory=dict)
    source: str = "unknown"
    confidence: float = 1.0
    wgs84_geometry: List[List[float]] = field(default_factory=list)

    @property
    def is_point(self) -> bool:
        return len(self.geometry) == 1


class CoordinateTransformer:
    """Explicit WGS84 <-> local nuPlan-map transformer.

    Supported config forms:
    - `{origin_lat, origin_lon, origin_heading_deg}` for local ENU tangent plane.
    - `{source_crs, local_crs}` or `{wgs84_crs, local_crs}` when pyproj is installed.
    - `{local_crs, projected_map_frame: true}` when the nuPlan map frame is already
      the projected CRS, e.g. Boston scenes stored as UTM easting/northing metres.

    The tangent-plane mode is sufficient for scenario-sized local ENU bboxes.  For
    nuPlan DB-set builds, prefer an explicit projected CRS and set
    `projected_map_frame=true` when scene poses are projected absolute metres.
    """

    def __init__(self, config: Dict[str, Any] | None = None) -> None:
        self.config = dict(config or {})
        self.origin_lat = float(self.config.get("origin_lat", self.config.get("lat0", 0.0)) or 0.0)
        self.origin_lon = float(self.config.get("origin_lon", self.config.get("lon0", 0.0)) or 0.0)
        self.origin_x = float(self.config.get("origin_x", 0.0) or 0.0)
        self.origin_y = float(self.config.get("origin_y", 0.0) or 0.0)
        self.heading = math.radians(float(self.config.get("origin_heading_deg", self.config.get("heading_deg", 0.0)) or 0.0))
        self._to_local = None
        self._to_wgs84 = None
        self._utm_zone: int | None = None
        self._utm_northern: bool = True
        self.transform_backend = "tangent_plane"
        self._projected_origin_x = 0.0
        self._projected_origin_y = 0.0
        local_crs = self.config.get("local_crs") or self.config.get("projected_crs") or self.config.get("target_crs")
        wgs84_crs = self.config.get("wgs84_crs") or self.config.get("source_crs") or "EPSG:4326"
        mode = str(self.config.get("map_frame") or self.config.get("transform_mode") or "").lower()
        self.projected_map_frame = bool(self.config.get("projected_map_frame", False)) or mode in {"projected", "utm", "projected_absolute", "nuplan_projected"}
        utm = _parse_epsg_utm(local_crs)
        if local_crs and _PyprojTransformer is not None:
            self._to_local = _PyprojTransformer.from_crs(wgs84_crs, local_crs, always_xy=True)
            self._to_wgs84 = _PyprojTransformer.from_crs(local_crs, wgs84_crs, always_xy=True)
            self.transform_backend = "pyproj"
        elif local_crs and utm is not None:
            self._utm_zone, self._utm_northern = utm
            self.transform_backend = "utm_fallback"
        elif local_crs:
            raise RuntimeError(f"georeference local_crs={local_crs!r} requires pyproj or a supported EPSG:326xx/327xx UTM CRS; refusing to silently use tangent-plane coordinates")

        if local_crs:
            # pyproj/UTM returns coordinates in the projected CRS.  Some nuPlan maps
            # store scene poses directly in that projected CRS (Boston looks like
            # UTM 19N: ~330k, ~4.69M).  In that case we must NOT subtract a city
            # origin, otherwise all GIS features become local ENU values around
            # zero and episode crops become empty.  Use an affine layer only when
            # the config explicitly describes a local map frame.
            if self.projected_map_frame:
                self._projected_origin_x = 0.0
                self._projected_origin_y = 0.0
                self.origin_x = 0.0
                self.origin_y = 0.0
                self.heading = 0.0
            elif self.config.get("projected_origin_x") is not None and self.config.get("projected_origin_y") is not None:
                self._projected_origin_x = float(self.config.get("projected_origin_x") or 0.0)
                self._projected_origin_y = float(self.config.get("projected_origin_y") or 0.0)
            elif self.config.get("origin_lat") is not None and self.config.get("origin_lon") is not None:
                if self._to_local is not None:
                    ox, oy = self._to_local.transform(self.origin_lon, self.origin_lat)
                elif self._utm_zone is not None:
                    ox, oy = _utm_forward(self.origin_lon, self.origin_lat, self._utm_zone, self._utm_northern)
                else:  # defensive; local_crs branch guarantees a backend
                    ox, oy = 0.0, 0.0
                self._projected_origin_x = float(ox)
                self._projected_origin_y = float(oy)

    @classmethod
    def from_file(cls, path: str | Path | None) -> "CoordinateTransformer":
        if not path:
            return cls({})
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(p)
        if p.suffix.lower() in {".yaml", ".yml"}:
            if yaml is None:
                raise RuntimeError("pyyaml is required to read YAML georeference configs")
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        else:
            data = json.loads(p.read_text(encoding="utf-8"))
        return cls(data)

    def _projected_to_map(self, px: float, py: float) -> Tuple[float, float]:
        c, s = math.cos(self.heading), math.sin(self.heading)
        dx = float(px) - self._projected_origin_x
        dy = float(py) - self._projected_origin_y
        return self.origin_x + c * dx + s * dy, self.origin_y - s * dx + c * dy

    def _map_to_projected(self, x: float, y: float) -> Tuple[float, float]:
        c, s = math.cos(self.heading), math.sin(self.heading)
        dx = c * (float(x) - self.origin_x) - s * (float(y) - self.origin_y)
        dy = s * (float(x) - self.origin_x) + c * (float(y) - self.origin_y)
        return self._projected_origin_x + dx, self._projected_origin_y + dy

    def wgs84_to_local(self, lon: float, lat: float) -> Tuple[float, float]:
        if self._to_local is not None:
            px, py = self._to_local.transform(lon, lat)
            return self._projected_to_map(float(px), float(py))
        if self._utm_zone is not None:
            px, py = _utm_forward(lon, lat, self._utm_zone, self._utm_northern)
            return self._projected_to_map(float(px), float(py))
        lat0 = math.radians(self.origin_lat)
        dx = math.radians(lon - self.origin_lon) * EARTH_RADIUS_M * math.cos(lat0)
        dy = math.radians(lat - self.origin_lat) * EARTH_RADIUS_M
        # Rotate into the local map frame if a map heading is supplied.
        c, s = math.cos(self.heading), math.sin(self.heading)
        x = self.origin_x + c * dx + s * dy
        y = self.origin_y - s * dx + c * dy
        return x, y

    def local_to_wgs84(self, x: float, y: float) -> Tuple[float, float]:
        if self._to_wgs84 is not None:
            px, py = self._map_to_projected(x, y)
            lon, lat = self._to_wgs84.transform(px, py)
            return float(lon), float(lat)
        if self._utm_zone is not None:
            px, py = self._map_to_projected(x, y)
            return _utm_inverse(px, py, self._utm_zone, self._utm_northern)
        c, s = math.cos(self.heading), math.sin(self.heading)
        dx = c * (x - self.origin_x) - s * (y - self.origin_y)
        dy = s * (x - self.origin_x) + c * (y - self.origin_y)
        lat = self.origin_lat + math.degrees(dy / EARTH_RADIUS_M)
        lon = self.origin_lon + math.degrees(dx / (EARTH_RADIUS_M * max(math.cos(math.radians(self.origin_lat)), 1e-9)))
        return lon, lat


def _as_float(v: Any) -> Optional[float]:
    if v in (None, "", "unknown", "none"):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().lower().replace("meters", "m").replace("metres", "m")
    if s in {"yes", "true"}:
        return 1.0
    if s in {"no", "false"}:
        return 0.0
    if s.endswith("%"):
        try:
            return float(s[:-1]) / 100.0
        except ValueError:
            return None
    m = re.match(r"^([-+]?\d+(?:\.\d+)?)\s*(m|meter|metre)?$", s)
    if m:
        return float(m.group(1))
    return None


def _boolish(v: Any) -> Optional[bool]:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in {"yes", "true", "1", "y", "lowered", "flush", "rolled", "present"}:
        return True
    if s in {"no", "false", "0", "n", "absent", "none", "raised"}:
        return False
    return None


def _warn_skip_external(path: Path, reason: str) -> None:
    print(f"[warn] skipping external GIS source {path}: {reason}", file=sys.stderr)


def _read_any(path: str | Path | None) -> List[Dict[str, Any]]:
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)
    if p.is_dir():
        rows: List[Dict[str, Any]] = []
        for child in sorted(p.rglob("*")):
            name = child.name.lower()
            if name.endswith((".report.json", ".provenance.json", ".manifest.json")):
                continue
            if child.is_file() and child.suffix.lower() in {".json", ".geojson", ".jsonl", ".ndjson", ".geojsonl", ".yaml", ".yml", ".csv"}:
                rows.extend(_read_any(child))
        return rows
    if p.stat().st_size == 0:
        _warn_skip_external(p, "empty file")
        return []
    if p.suffix.lower() in {".jsonl", ".ndjson", ".geojsonl"}:
        try:
            return [dict(x) for x in read_jsonl(p)]
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            _warn_skip_external(p, f"invalid JSONL ({exc})")
            return []
    if p.suffix.lower() == ".csv":
        with p.open("r", encoding="utf-8", newline="") as f:
            return [dict(r) for r in csv.DictReader(f)]
    if p.suffix.lower() in {".yaml", ".yml"}:
        if yaml is None:
            raise RuntimeError("pyyaml is required to read YAML files")
        try:
            payload = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except (UnicodeDecodeError, yaml.YAMLError) as exc:
            _warn_skip_external(p, f"invalid YAML ({exc})")
            return []
    else:
        try:
            payload = load_json(p)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            # Public-data portals sometimes return HTML/403/429 pages or leave
            # zero-byte placeholders at a .geojson/.json path. These sources are
            # optional in bootstrap mode, so skip them and let downstream quality
            # reports expose the missing evidence instead of crashing early.
            _warn_skip_external(p, f"invalid JSON ({exc})")
            return []
    if isinstance(payload, dict):
        if payload.get("type") == "FeatureCollection" and isinstance(payload.get("features"), list):
            return [dict(x) for x in payload["features"]]
        if "elements" in payload and isinstance(payload["elements"], list):  # Overpass JSON
            return [{"_overpass_payload": payload}]
        for key in ["features", "nodes", "edges", "records", "candidates", "entrances", "curbs", "sidewalks"]:
            if isinstance(payload.get(key), list):
                return [dict(x) for x in payload[key]]
        return [payload]
    if isinstance(payload, list):
        return [dict(x) for x in payload]
    return []


def _coords_from_geojson_geometry(geom: Dict[str, Any]) -> List[List[float]]:
    typ = geom.get("type")
    coords = geom.get("coordinates")
    if typ == "Point" and isinstance(coords, list) and len(coords) >= 2:
        return [[float(coords[0]), float(coords[1])]]
    if typ == "LineString" and isinstance(coords, list):
        return [[float(c[0]), float(c[1])] for c in coords if isinstance(c, list) and len(c) >= 2]
    if typ == "MultiLineString" and isinstance(coords, list):
        out: List[List[float]] = []
        for line in coords:
            out.extend([[float(c[0]), float(c[1])] for c in line if isinstance(c, list) and len(c) >= 2])
        return out
    if typ == "Polygon" and isinstance(coords, list) and coords:
        ring = coords[0]
        return [[float(c[0]), float(c[1])] for c in ring if isinstance(c, list) and len(c) >= 2]
    return []


def _feature_tags(row: Dict[str, Any]) -> Dict[str, Any]:
    tags: Dict[str, Any] = {}
    if isinstance(row.get("properties"), dict):
        tags.update(row["properties"])
    if isinstance(row.get("tags"), dict):
        tags.update(row["tags"])
    for k, v in row.items():
        if k not in {"geometry", "properties", "tags", "type", "coordinates", "_overpass_payload"} and not k.startswith("_"):
            tags.setdefault(k, v)
    return tags


def _looks_wgs84(points: Sequence[Sequence[float]], tags: Dict[str, Any]) -> bool:
    frame = str(tags.get("frame") or tags.get("crs") or tags.get("coordinate_frame") or "").lower()
    if "wgs" in frame or "epsg:4326" in frame or frame in {"lonlat", "longlat", "latlon"}:
        return True
    if frame in {"map", "local", "nuplan", "projected", "utm"} or frame.startswith("epsg:"):
        return False
    if not points:
        return False
    lonlat_like = all(abs(float(p[0])) <= 180 and abs(float(p[1])) <= 90 for p in points[:5])
    if not lonlat_like:
        return False
    if bool(tags.get("lon") or tags.get("lat") or tags.get("longitude") or tags.get("latitude")):
        return True
    # OpenSidewalks / municipal JSON exports often provide raw coordinate lists
    # without per-feature CRS tags. Treat real-world lon/lat-looking values as
    # WGS84 unless the feature explicitly declares a local/projected frame.
    return any(abs(float(p[0])) > 30 or abs(float(p[1])) > 30 for p in points[:5])


def _classify_kind(tags: Dict[str, Any], geometry: List[List[float]]) -> str:
    t = {str(k).lower(): str(v).lower() for k, v in tags.items() if v is not None}
    if "entrance" in t or t.get("building") == "entrance" or t.get("door") in {"yes", "main", "service"}:
        return "entrance"
    if t.get("highway") == "crossing" or t.get("footway") == "crossing" or t.get("crossing") not in {None, "no"}:
        return "crossing"
    if t.get("kerb") in {"lowered", "flush", "rolled"} or t.get("curb_ramp") in {"yes", "true"}:
        return "curb_ramp"
    if t.get("barrier") == "kerb" or t.get("kerb") in {"raised", "regular", "yes"}:
        return "curb"
    if t.get("highway") in {"footway", "path", "pedestrian", "steps"} or t.get("footway") == "sidewalk":
        return "sidewalk"
    # OSM sidewalk=* on a carriageway is only an attribute saying a sidewalk
    # exists alongside the road. It does not make the road centerline a
    # pedestrian edge. Preserve it as non-routable evidence.
    if t.get("sidewalk") in {"yes", "both", "left", "right", "separate"}:
        return "road_with_sidewalk_tag"
    if t.get("osw:node:type") or t.get("osw:edge:type"):
        return t.get("osw:node:type") or t.get("osw:edge:type") or "sidewalk"
    return "unknown_linear" if len(geometry) > 1 else "poi"


def _normalize_feature(row: Dict[str, Any], transformer: CoordinateTransformer, default_source: str) -> List[GISFeature]:
    if "_overpass_payload" in row:
        return _overpass_features(row["_overpass_payload"], transformer, default_source)
    tags = _feature_tags(row)
    source = str(tags.get("source") or tags.get("data_source") or default_source)
    fid = str(tags.get("feature_id") or tags.get("id") or row.get("id") or row.get("node_id") or row.get("edge_id") or f"feature_{abs(hash(json.dumps(tags, sort_keys=True, default=str))) % 10**10}")
    geom: List[List[float]] = []
    if isinstance(row.get("geometry"), dict):
        geom = _coords_from_geojson_geometry(row["geometry"])
        wgs84 = list(geom)
        local = [list(transformer.wgs84_to_local(float(x), float(y))) for x, y in geom]
    elif isinstance(row.get("geometry"), list):
        raw = row["geometry"]
        if raw and isinstance(raw[0], dict):
            geom = [[float(p.get("x", p.get("lon", p.get("longitude", 0.0)))), float(p.get("y", p.get("lat", p.get("latitude", 0.0))))] for p in raw]
        elif raw and isinstance(raw[0], (list, tuple)):
            geom = [[float(p[0]), float(p[1])] for p in raw if len(p) >= 2]
        elif len(raw) >= 2 and isinstance(raw[0], (int, float)):
            geom = [[float(raw[0]), float(raw[1])]]
        wgs84_flag = _looks_wgs84(geom, tags)
        wgs84 = list(geom) if wgs84_flag else [list(transformer.local_to_wgs84(x, y)) for x, y in geom]
        local = [list(transformer.wgs84_to_local(x, y)) for x, y in geom] if wgs84_flag else geom
    elif row.get("lon") is not None and row.get("lat") is not None:
        lon, lat = float(row["lon"]), float(row["lat"])
        local = [list(transformer.wgs84_to_local(lon, lat))]
        wgs84 = [[lon, lat]]
    elif row.get("longitude") is not None and row.get("latitude") is not None:
        lon, lat = float(row["longitude"]), float(row["latitude"])
        local = [list(transformer.wgs84_to_local(lon, lat))]
        wgs84 = [[lon, lat]]
    elif row.get("x") is not None and row.get("y") is not None:
        x, y = float(row["x"]), float(row["y"])
        local = [[x, y]]
        wgs84 = [list(transformer.local_to_wgs84(x, y))]
    else:
        return []
    if not local:
        return []
    kind = str(tags.get("kind") or tags.get("node_type") or tags.get("edge_type") or _classify_kind(tags, local))
    conf = float(tags.get("confidence", tags.get("map_confidence", 1.0)) or 1.0)
    return [GISFeature(fid, kind, local, tags, source, conf, wgs84)]


def _overpass_features(payload: Dict[str, Any], transformer: CoordinateTransformer, default_source: str) -> List[GISFeature]:
    elements = payload.get("elements") or []
    nodes: Dict[int, Tuple[float, float]] = {}
    out: List[GISFeature] = []
    for el in elements:
        if el.get("type") == "node" and el.get("lat") is not None and el.get("lon") is not None:
            nodes[int(el["id"])] = (float(el["lon"]), float(el["lat"]))
    for el in elements:
        tags = dict(el.get("tags") or {})
        tags.setdefault("source", default_source)
        typ = el.get("type")
        fid = f"osm_{typ}_{el.get('id')}"
        wgs: List[List[float]] = []
        if typ == "node" and el.get("lat") is not None and el.get("lon") is not None:
            if not tags or _classify_kind(tags, [[0, 0]]) == "poi":
                # Keep only pedestrian-relevant untagged/POI nodes out of the graph.
                if not any(k in tags for k in ["entrance", "kerb", "curb_ramp", "highway", "crossing"]):
                    continue
            wgs = [[float(el["lon"]), float(el["lat"])]]
        elif typ == "way":
            if isinstance(el.get("geometry"), list):
                wgs = [[float(p["lon"]), float(p["lat"])] for p in el["geometry"] if "lon" in p and "lat" in p]
            elif isinstance(el.get("nodes"), list):
                wgs = [[nodes[n][0], nodes[n][1]] for n in el["nodes"] if n in nodes]
        else:
            continue
        if not wgs:
            continue
        local = [list(transformer.wgs84_to_local(lon, lat)) for lon, lat in wgs]
        kind = _classify_kind(tags, local)
        out.append(GISFeature(fid, kind, local, tags, str(tags.get("source") or default_source), float(tags.get("confidence", 0.85) or 0.85), wgs))
    return out


def load_gis_features(paths: Sequence[str | Path | None], transformer: CoordinateTransformer, default_source: str) -> List[GISFeature]:
    feats: List[GISFeature] = []
    for path in paths:
        for row in _read_any(path):
            feats.extend(_normalize_feature(row, transformer, default_source))
    return feats


def _dist_point_segment(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    vx, vy = bx - ax, by - ay
    wx, wy = px - ax, py - ay
    denom = vx * vx + vy * vy
    if denom <= 1e-12:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, (wx * vx + wy * vy) / denom))
    cx, cy = ax + t * vx, ay + t * vy
    return math.hypot(px - cx, py - cy)



def _geometry_intersects_bbox(geometry: Sequence[Sequence[float]], bbox: Tuple[float, float, float, float]) -> bool:
    """Return whether a point/polyline geometry intersects an axis-aligned box.

    The previous crop accepted a line only when one of its vertices was inside
    the scene box, which could drop a long sidewalk segment that crosses the box
    with both endpoints outside.  This exact segment/rectangle test fixes that
    correctness issue while the feature-bounds index avoids scanning every city
    feature for every episode.
    """
    if not geometry:
        return False
    xmin, ymin, xmax, ymax = bbox
    for p in geometry:
        if xmin <= float(p[0]) <= xmax and ymin <= float(p[1]) <= ymax:
            return True
    # Liang-Barsky clipping for each polyline segment.
    for a, b in zip(geometry[:-1], geometry[1:]):
        x0, y0 = float(a[0]), float(a[1])
        x1, y1 = float(b[0]), float(b[1])
        dx, dy = x1 - x0, y1 - y0
        pvals = (-dx, dx, -dy, dy)
        qvals = (x0 - xmin, xmax - x0, y0 - ymin, ymax - y0)
        u1, u2 = 0.0, 1.0
        ok = True
        for pp, qq in zip(pvals, qvals):
            if abs(pp) <= 1e-15:
                if qq < 0:
                    ok = False; break
                continue
            t = qq / pp
            if pp < 0:
                if t > u2: ok = False; break
                u1 = max(u1, t)
            else:
                if t < u1: ok = False; break
                u2 = min(u2, t)
        if ok and u1 <= u2:
            return True
    return False


class GISFeatureSpatialIndex:
    """Vectorized feature-bounds index for repeated per-scene GIS crops."""

    def __init__(self, features: Sequence[GISFeature]) -> None:
        import numpy as np
        self.features = features
        bounds = np.empty((len(features), 4), dtype=np.float64)
        for i, f in enumerate(features):
            if not f.geometry:
                bounds[i] = (np.inf, np.inf, -np.inf, -np.inf)
                continue
            xs = [float(p[0]) for p in f.geometry]
            ys = [float(p[1]) for p in f.geometry]
            bounds[i] = (min(xs), min(ys), max(xs), max(ys))
        self.bounds = bounds

    def query(self, bbox: Tuple[float, float, float, float]) -> List[GISFeature]:
        import numpy as np
        if len(self.features) == 0:
            return []
        xmin, ymin, xmax, ymax = bbox
        b = self.bounds
        mask = (b[:, 0] <= xmax) & (b[:, 2] >= xmin) & (b[:, 1] <= ymax) & (b[:, 3] >= ymin)
        idxs = np.flatnonzero(mask)
        if idxs.size == 0:
            return []

        # Any feature whose complete AABB is contained by the query box is
        # guaranteed to intersect it, so avoid walking all geometry vertices for
        # the overwhelmingly common interior case.  Only boundary-straddling
        # candidates need the exact line/rectangle test.
        bb = b[idxs]
        contained = (bb[:, 0] >= xmin) & (bb[:, 2] <= xmax) & (bb[:, 1] >= ymin) & (bb[:, 3] <= ymax)
        out: List[GISFeature] = [self.features[int(i)] for i in idxs[contained]]
        for i in idxs[~contained]:
            feature = self.features[int(i)]
            if _geometry_intersects_bbox(feature.geometry, bbox):
                out.append(feature)
        return out

    def query_many(self, boxes: Sequence[Tuple[float, float, float, float]]) -> List[GISFeature]:
        """Return features intersecting the union of axis-aligned boxes.

        This is used for a *lossless conservative* route-corridor prefilter.
        Each chunk box is the bbox of a short route fragment expanded by the
        requested corridor radius, so the union contains every point in the
        true Euclidean route buffer.  It therefore removes only features that
        could not belong to the configured corridor, while avoiding the huge
        empty corners of one rectangle around an entire diagonal/curved route.
        """
        import numpy as np
        if len(self.features) == 0 or not boxes:
            return []
        b = self.bounds
        mask = np.zeros(len(self.features), dtype=bool)
        # The number of boxes is intentionally small (route is chunked at
        # kilometre scale), so repeated vectorized comparisons are much cheaper
        # than Python geometry checks over millions of city features.
        for xmin, ymin, xmax, ymax in boxes:
            mask |= (b[:, 0] <= xmax) & (b[:, 2] >= xmin) & (b[:, 1] <= ymax) & (b[:, 3] >= ymin)
        idxs = np.flatnonzero(mask)
        if idxs.size == 0:
            return []

        # Accept features whose complete AABB is contained in any route box
        # entirely with NumPy. This is the common interior case and avoids a
        # Python ``feature x boxes`` loop for tens of thousands of features per
        # episode. Only AABBs that straddle a box boundary need exact geometry
        # intersection checks.
        accepted = np.zeros(len(self.features), dtype=bool)
        for xmin, ymin, xmax, ymax in boxes:
            contained = (b[:, 0] >= xmin) & (b[:, 2] <= xmax) & (b[:, 1] >= ymin) & (b[:, 3] <= ymax)
            accepted |= contained

        unresolved = idxs[~accepted[idxs]]
        for i in unresolved:
            ii = int(i); fb = b[ii]; feature = self.features[ii]
            for box in boxes:
                xmin, ymin, xmax, ymax = box
                if fb[0] > xmax or fb[2] < xmin or fb[1] > ymax or fb[3] < ymin:
                    continue
                if _geometry_intersects_bbox(feature.geometry, box):
                    accepted[ii] = True
                    break
        return [self.features[int(i)] for i in np.flatnonzero(accepted & mask)]


def distance_to_polyline(point: Sequence[float], polyline: Sequence[Sequence[float]]) -> float:
    if not polyline:
        return float("inf")
    if len(polyline) == 1:
        return math.hypot(float(point[0]) - float(polyline[0][0]), float(point[1]) - float(polyline[0][1]))
    return min(_dist_point_segment(float(point[0]), float(point[1]), float(a[0]), float(a[1]), float(b[0]), float(b[1])) for a, b in zip(polyline[:-1], polyline[1:]))


def nearest_route_side(point: Sequence[float], polyline: Sequence[Sequence[float]]) -> str:
    if len(polyline) < 2:
        return "unknown"
    px, py = float(point[0]), float(point[1])
    best = None
    best_dist = float("inf")
    for a, b in zip(polyline[:-1], polyline[1:]):
        d = _dist_point_segment(px, py, float(a[0]), float(a[1]), float(b[0]), float(b[1]))
        if d < best_dist:
            best_dist = d
            best = (a, b)
    if best is None:
        return "unknown"
    a, b = best
    cross = (float(b[0]) - float(a[0])) * (py - float(a[1])) - (float(b[1]) - float(a[1])) * (px - float(a[0]))
    return "left" if cross > 0 else "right" if cross < 0 else "unknown"


def _bbox(points: Sequence[Sequence[float]], buffer_m: float = 0.0) -> Tuple[float, float, float, float]:
    xs = [float(p[0]) for p in points]
    ys = [float(p[1]) for p in points]
    return min(xs) - buffer_m, min(ys) - buffer_m, max(xs) + buffer_m, max(ys) + buffer_m


def route_corridor_boxes(
    points: Sequence[Sequence[float]],
    radius_m: float,
    chunk_length_m: float = 1000.0,
) -> List[Tuple[float, float, float, float]]:
    """Build a conservative union-of-boxes approximation of a route buffer.

    Consecutive route geometry is accumulated into chunks no longer than
    ``chunk_length_m`` (long source segments are split at the chunk boundary).
    Each chunk's *actual polyline* bbox is expanded by ``radius_m``.  Hence the
    union is a strict superset of the Euclidean route buffer: any feature that
    can lie within ``radius_m`` of the route remains eligible, while large
    corners of one global route bbox can be excluded safely.
    """
    pts = [(float(p[0]), float(p[1])) for p in points if len(p) >= 2]
    if not pts:
        return []
    radius = max(0.0, float(radius_m))
    chunk = max(1.0, float(chunk_length_m))
    if len(pts) == 1:
        x, y = pts[0]
        return [(x - radius, y - radius, x + radius, y + radius)]

    boxes: List[Tuple[float, float, float, float]] = []
    cur_xmin = cur_xmax = pts[0][0]
    cur_ymin = cur_ymax = pts[0][1]
    used = 0.0

    def include(x: float, y: float) -> None:
        nonlocal cur_xmin, cur_xmax, cur_ymin, cur_ymax
        cur_xmin = min(cur_xmin, x); cur_xmax = max(cur_xmax, x)
        cur_ymin = min(cur_ymin, y); cur_ymax = max(cur_ymax, y)

    def emit() -> None:
        boxes.append((cur_xmin - radius, cur_ymin - radius,
                      cur_xmax + radius, cur_ymax + radius))

    x0, y0 = pts[0]
    for x1, y1 in pts[1:]:
        sx, sy = x0, y0
        remaining_seg = math.hypot(x1 - sx, y1 - sy)
        if remaining_seg <= 1e-12:
            include(x1, y1)
            x0, y0 = x1, y1
            continue
        while remaining_seg > 1e-12:
            room = chunk - used
            if room <= 1e-9:
                emit()
                cur_xmin = cur_xmax = sx
                cur_ymin = cur_ymax = sy
                used = 0.0
                room = chunk
            take = min(room, remaining_seg)
            frac = take / remaining_seg
            ex = sx + (x1 - sx) * frac
            ey = sy + (y1 - sy) * frac
            include(ex, ey)
            used += take
            sx, sy = ex, ey
            remaining_seg = math.hypot(x1 - sx, y1 - sy)
            if used >= chunk - 1e-9:
                emit()
                cur_xmin = cur_xmax = sx
                cur_ymin = cur_ymax = sy
                used = 0.0
        x0, y0 = x1, y1

    if used > 1e-9 or not boxes:
        emit()

    # Stable de-duplication handles repeated route points/chunk boundaries.
    seen: set[Tuple[float, float, float, float]] = set()
    out: List[Tuple[float, float, float, float]] = []
    for box in boxes:
        key = tuple(round(float(v), 6) for v in box)
        if key not in seen:
            out.append(box)
            seen.add(key)
    return out


def _in_bbox(pt: Sequence[float], bbox: Tuple[float, float, float, float]) -> bool:
    return bbox[0] <= float(pt[0]) <= bbox[2] and bbox[1] <= float(pt[1]) <= bbox[3]


def iter_scene_contexts(scene_dataset_dir: str | Path | None, episode_ids: Sequence[str], buffer_m: float) -> Iterator[SceneContext]:
    """Stream scene contexts instead of loading full nuPlan scene JSONL into RAM."""
    wanted = set(episode_ids) if episode_ids and set(episode_ids) != {"shared"} else None
    emitted = 0
    if scene_dataset_dir:
        root = Path(scene_dataset_dir)
        for file in [root / "scenes.jsonl", root / "scenes.json", root / "episodes.jsonl"]:
            if not file.exists():
                continue
            rows = iter_jsonl(file) if file.suffix == ".jsonl" else iter(_read_any(file))
            for row in rows:
                eid = str(row.get("episode_id") or row.get("scenario_id") or "shared")
                if wanted is not None and eid not in wanted:
                    continue
                rc = row.get("route_corridor") or row.get("metadata", {}).get("route_corridor") or {}
                poly = rc.get("polyline") or row.get("route_polyline") or []
                if not poly:
                    p0 = row.get("initial_ego_pose") or {}
                    pg = row.get("mission_goal") or {}
                    if p0 and pg:
                        poly = [[p0.get("x", 0.0), p0.get("y", 0.0)], [pg.get("x", 0.0), pg.get("y", 0.0)]]
                poly = [[float(p[0]), float(p[1])] for p in poly if isinstance(p, (list, tuple)) and len(p) >= 2]
                emitted += 1
                yield SceneContext(
                    episode_id=eid,
                    map_name=row.get("map_name"),
                    route_polyline=poly,
                    bbox=_bbox(poly, buffer_m) if poly else None,
                    corridor_radius_m=float(buffer_m),
                    metadata=row,
                )
            break
    if emitted:
        return
    if wanted:
        for eid in sorted(wanted):
            yield SceneContext(eid)
    elif not scene_dataset_dir:
        yield SceneContext("shared")


def read_scene_contexts(scene_dataset_dir: str | Path | None, episode_ids: Sequence[str], buffer_m: float) -> List[SceneContext]:
    return list(iter_scene_contexts(scene_dataset_dir, episode_ids, buffer_m))


def scene_context_count(scene_dataset_dir: str | Path | None) -> Optional[int]:
    """Return a cheap expected scene count from the extraction manifest."""
    if not scene_dataset_dir:
        return None
    manifest = Path(scene_dataset_dir) / "scene_context_manifest.json"
    if not manifest.exists():
        return None
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        return int(payload.get("num_scenes")) if payload.get("num_scenes") is not None else None
    except Exception:
        return None


def _node_attrs_from_feature(f: GISFeature) -> Dict[str, Any]:
    t = f.tags
    return {
        "width_m": _as_float(t.get("width_m") or t.get("width") or t.get("sidewalk_width_m") or t.get("sidewalk:width")),
        "slope": _as_float(t.get("slope") or t.get("running_slope") or t.get("incline")),
        "cross_slope": _as_float(t.get("cross_slope") or t.get("crossfall")),
        "curb_ramp": _boolish(t.get("curb_ramp") or t.get("kerb")),
        "step_free": _boolish(t.get("step_free") or t.get("wheelchair")),
        "surface": t.get("surface") or t.get("material"),
        "lighting": "lit" if str(t.get("lit", "")).lower() == "yes" else t.get("lighting"),
        "shelter": _boolish(t.get("shelter")),
        "curb_height_m": _as_float(t.get("curb_height_m") or t.get("kerb:height") or t.get("curb_height")),
        "deployment_clearance_m": _as_float(t.get("deployment_clearance_m") or t.get("clear_width_m") or t.get("landing_width_m")),
        "elevation_m": _as_float(t.get("elevation_m") or t.get("ele") or t.get("z")),
    }


def _edge_attrs_from_feature(f: GISFeature) -> Dict[str, Any]:
    a = _node_attrs_from_feature(f)
    t = f.tags
    return {
        "width_m": a["width_m"],
        "slope": a["slope"],
        "cross_slope": a["cross_slope"],
        "surface": a["surface"],
        "curb_ramp": a["curb_ramp"] if a["curb_ramp"] is not None else (f.kind == "curb_ramp"),
        "step_free": a["step_free"] if a["step_free"] is not None else (False if f.kind == "steps" or str(t.get("highway", "")).lower() == "steps" else None),
        "obstacle": bool(_boolish(t.get("obstacle") or t.get("blocked")) or str(t.get("obstacle_state", "")).lower() == "blocked"),
        "lighting": a["lighting"],
        "shelter": a["shelter"],
        "crossing_type": t.get("crossing_type") or t.get("crossing") or ("crossing" if f.kind == "crossing" else f.kind),
        "obstacle_state": t.get("obstacle_state"),
    }


class AccessibilityFusionBuilder:
    """Build per-scenario accessibility graphs from OSM/OpenSidewalks/city GIS."""

    def __init__(
        self,
        transformer: CoordinateTransformer,
        snap_tolerance_m: float = 3.0,
        source_name: str = "nuplan_osm_opensidewalks_citygis",
        corridor_chunk_length_m: float = 1000.0,
    ) -> None:
        self.transformer = transformer
        self.snap_tolerance_m = float(snap_tolerance_m)
        self.source_name = source_name
        self.corridor_chunk_length_m = max(1.0, float(corridor_chunk_length_m))
        self._feature_index: GISFeatureSpatialIndex | None = None
        self._feature_index_source_id: int | None = None

    def build_for_scene(self, scene: SceneContext, features: List[GISFeature], min_nodes: int = 0, min_edges: int = 0, add_bidirectional: bool = True, pudo_connector_radius_m: float = 75.0) -> AccessibilityGraph:
        import time
        timing: Dict[str, float] = {}
        t_stage = time.perf_counter()
        feats = self._crop(features, scene)
        timing["crop_s"] = time.perf_counter() - t_stage
        nodes: Dict[str, AccessibilityNode] = {}
        node_extra: Dict[str, Dict[str, Any]] = {}
        edges: List[AccessibilityEdge] = []

        def node_id(x: float, y: float, kind: str, source: str, fid: str) -> str:
            qx = round(x / max(self.snap_tolerance_m, 0.01))
            qy = round(y / max(self.snap_tolerance_m, 0.01))
            return f"{kind}:{qx}:{qy}"

        def add_node(x: float, y: float, kind: str, source: str, conf: float, fid: str, attrs: Dict[str, Any] | None = None) -> str:
            nid = node_id(x, y, kind if kind in {"entrance", "curb", "curb_ramp", "transit_stop"} else "ped", source, fid)
            if nid not in nodes:
                nodes[nid] = AccessibilityNode(nid, x, y, kind, conf, None, source, Pose2D(x, y, 0.0, "map"))
                node_extra[nid] = dict(attrs or {})
            else:
                # Keep the more specific kind/source and higher confidence.
                n = nodes[nid]
                if n.kind == "sidewalk" and kind in {"entrance", "curb", "curb_ramp", "crossing", "transit_stop"}:
                    n.kind = kind
                n.confidence = max(float(n.confidence), float(conf))
                if source not in str(n.source):
                    n.source = f"{n.source}+{source}"
                node_extra[nid].update({k: v for k, v in (attrs or {}).items() if v is not None})
            return nid

        routable_linear_kinds = {"sidewalk", "crossing", "path", "steps"}
        point_kinds = {"entrance", "entrance_proxy", "curb", "curb_ramp", "transit_stop", "poi"}
        skipped_linear_kinds: Dict[str, int] = {}
        t_stage = time.perf_counter()
        for f in feats:
            if f.is_point:
                attrs = _node_attrs_from_feature(f)
                # Entrance proxies are deliberately kept distinct from verified entrances.
                # They may be useful as candidate OD anchors but are not promoted to
                # authoritative entrance evidence by the graph builder.
                add_node(f.geometry[0][0], f.geometry[0][1], f.kind, f.source, f.confidence, f.feature_id, attrs)
                continue
            if f.kind not in routable_linear_kinds:
                skipped_linear_kinds[f.kind] = skipped_linear_kinds.get(f.kind, 0) + 1
                continue
            attrs = _node_attrs_from_feature(f)
            # Edge attributes are feature-level.  Recomputing/parsing them for
            # every vertex pair of the same polyline was pure repeated work.
            edge_attrs_base = _edge_attrs_from_feature(f)
            previous: Optional[str] = None
            for i, (x, y) in enumerate(f.geometry):
                kind = "crossing" if f.kind == "crossing" else "sidewalk"
                nid = add_node(x, y, kind, f.source, f.confidence, f.feature_id, attrs)
                if previous is not None and previous != nid:
                    a, b = nodes[previous], nodes[nid]
                    geom = [[a.x, a.y], [b.x, b.y]]
                    length = math.hypot(b.x - a.x, b.y - a.y)
                    ea = edge_attrs_base
                    if ea.get("slope") is None and node_extra.get(previous, {}).get("elevation_m") is not None and node_extra.get(nid, {}).get("elevation_m") is not None:
                        ea = dict(edge_attrs_base)
                        ea["slope"] = abs(float(node_extra[nid]["elevation_m"]) - float(node_extra[previous]["elevation_m"])) / max(length, 0.001)
                    eid = f"{f.feature_id}:{i-1}:{i}"
                    edges.append(AccessibilityEdge(eid, previous, nid, max(0.001, length), confidence=f.confidence, geometry=geom, source=f.source, **ea))
                    if add_bidirectional and str(f.tags.get("oneway", "")).lower() not in {"yes", "true", "1"}:
                        edges.append(AccessibilityEdge(eid + ":rev", nid, previous, max(0.001, length), confidence=f.confidence, geometry=list(reversed(geom)), source=f.source, **ea))
                previous = nid
        timing["topology_s"] = time.perf_counter() - t_stage

        t_stage = time.perf_counter()
        self._snap_point_nodes(nodes, edges, target_kinds={"entrance"}, edge_kind="entrance_connector")
        self._snap_point_nodes(nodes, edges, target_kinds={"curb", "curb_ramp"}, edge_kind="curb_connector")
        timing["snap_s"] = time.perf_counter() - t_stage
        t_stage = time.perf_counter()
        if scene.route_polyline:
            self._add_pudo_connector_metadata(nodes, node_extra, scene.route_polyline, pudo_connector_radius_m)
        timing["pudo_connector_s"] = time.perf_counter() - t_stage
        t_stage = time.perf_counter()
        edges = self._dedupe_edges(edges, nodes)
        timing["dedupe_s"] = time.perf_counter() - t_stage
        graph = AccessibilityGraph(scene.episode_id, list(nodes.values()), edges, {
            "source": self.source_name,
            "builder": "AccessibilityFusionBuilder",
            "map_name": scene.map_name,
            "route_bbox": scene.bbox,
            "snap_tolerance_m": self.snap_tolerance_m,
            "pudo_connector_radius_m": pudo_connector_radius_m,
            "node_attributes": node_extra,
            "features_cropped": len(feats),
            "skipped_non_routable_linear_features": skipped_linear_kinds,
            "crop_mode": "lossless_chunked_route_envelope" if scene.route_polyline and scene.corridor_radius_m > 0 else "bbox",
            "corridor_radius_m": float(scene.corridor_radius_m or 0.0),
            "corridor_chunk_length_m": float(self.corridor_chunk_length_m),
            "build_timing_s": timing,
        })
        if len(graph.nodes) < min_nodes or len(graph.edges) < min_edges:
            raise RuntimeError(f"accessibility graph too small for {scene.episode_id}: {len(graph.nodes)} nodes/{len(graph.edges)} edges; required {min_nodes}/{min_edges}")
        return graph

    def _crop(self, features: List[GISFeature], scene: SceneContext) -> List[GISFeature]:
        if scene.bbox is None:
            return features
        if self._feature_index is None or self._feature_index_source_id != id(features):
            self._feature_index = GISFeatureSpatialIndex(features)
            self._feature_index_source_id = id(features)
        if scene.route_polyline and scene.corridor_radius_m > 0:
            boxes = route_corridor_boxes(
                scene.route_polyline,
                scene.corridor_radius_m,
                self.corridor_chunk_length_m,
            )
            if boxes:
                return self._feature_index.query_many(boxes)
        return self._feature_index.query(scene.bbox)

    def _nearest_ped_node(self, nid: str, nodes: Dict[str, AccessibilityNode], exclude_kinds: set[str]) -> Tuple[Optional[str], float]:
        n = nodes[nid]
        best, best_d = None, float("inf")
        for oid, o in nodes.items():
            if oid == nid or o.kind in exclude_kinds:
                continue
            d = math.hypot(n.x - o.x, n.y - o.y)
            if d < best_d:
                best, best_d = oid, d
        return best, best_d

    def _snap_point_nodes(self, nodes: Dict[str, AccessibilityNode], edges: List[AccessibilityEdge], target_kinds: set[str], edge_kind: str) -> None:
        # Point-to-pedestrian snapping only needs neighbors inside the connector
        # threshold.  A uniform metric grid preserves the exact nearest choice
        # within that radius while avoiding O(num_target * num_nodes) scans.
        max_d = max(25.0, self.snap_tolerance_m * 4)
        cell = max_d
        exclude = target_kinds | {"poi"}
        grid: Dict[Tuple[int, int], List[str]] = {}
        for oid, o in nodes.items():
            if o.kind in exclude:
                continue
            key = (math.floor(o.x / cell), math.floor(o.y / cell))
            grid.setdefault(key, []).append(oid)
        for nid, n in list(nodes.items()):
            if n.kind not in target_kinds:
                continue
            cx, cy = math.floor(n.x / cell), math.floor(n.y / cell)
            other, d = None, float("inf")
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for oid in grid.get((cx + dx, cy + dy), []):
                        o = nodes[oid]
                        dd = math.hypot(n.x - o.x, n.y - o.y)
                        if dd < d:
                            other, d = oid, dd
            if other and d <= max_d:
                o = nodes[other]
                eid = f"{edge_kind}:{nid}:{other}"
                geom = [[n.x, n.y], [o.x, o.y]]
                attrs = {
                    "width_m": None,
                    "slope": None,
                    "cross_slope": None,
                    "surface": None,
                    "curb_ramp": n.kind == "curb_ramp" or None,
                    "step_free": True if n.kind == "curb_ramp" else None,
                    "obstacle": False,
                    "lighting": None,
                    "shelter": None,
                    "crossing_type": edge_kind,
                    "obstacle_state": None,
                }
                edges.append(AccessibilityEdge(eid, nid, other, max(0.001, d), confidence=min(n.confidence, o.confidence), geometry=geom, source=f"{n.source}+snap", **attrs))
                edges.append(AccessibilityEdge(eid + ":rev", other, nid, max(0.001, d), confidence=min(n.confidence, o.confidence), geometry=list(reversed(geom)), source=f"{n.source}+snap", **attrs))

    def _add_pudo_connector_metadata(self, nodes: Dict[str, AccessibilityNode], node_extra: Dict[str, Dict[str, Any]], route: List[List[float]], radius: float) -> None:
        for nid, n in nodes.items():
            if n.kind in {"curb", "curb_ramp"}:
                d = distance_to_polyline([n.x, n.y], route)
                if d <= radius:
                    node_extra.setdefault(nid, {})["pudo_connector_candidate"] = True
                    node_extra[nid]["distance_to_route_m"] = round(d, 3)
                    node_extra[nid]["route_side"] = nearest_route_side([n.x, n.y], route)

    def _dedupe_edges(self, edges: List[AccessibilityEdge], nodes: Dict[str, AccessibilityNode]) -> List[AccessibilityEdge]:
        best: Dict[Tuple[str, str, str], AccessibilityEdge] = {}
        for e in edges:
            if e.from_node not in nodes or e.to_node not in nodes or e.from_node == e.to_node:
                continue
            key = (e.from_node, e.to_node, str(e.crossing_type or ""))
            cur = best.get(key)
            if cur is None or (e.confidence, -e.length_m) > (cur.confidence, -cur.length_m):
                best[key] = e
        return list(best.values())
