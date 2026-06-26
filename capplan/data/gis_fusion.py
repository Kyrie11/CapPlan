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
from capplan.utils.serialization import load_json, read_jsonl

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
        for child in sorted(p.glob("*")):
            if child.suffix.lower() in {".json", ".geojson", ".jsonl", ".yaml", ".yml", ".csv"}:
                rows.extend(_read_any(child))
        return rows
    if p.stat().st_size == 0:
        _warn_skip_external(p, "empty file")
        return []
    if p.suffix.lower() == ".jsonl":
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
    if t.get("highway") in {"footway", "path", "pedestrian", "steps"} or t.get("footway") == "sidewalk" or t.get("sidewalk") in {"yes", "both", "left", "right"}:
        return "sidewalk"
    if t.get("osw:node:type") or t.get("osw:edge:type"):
        return t.get("osw:node:type") or t.get("osw:edge:type") or "sidewalk"
    return "sidewalk" if len(geometry) > 1 else "poi"


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
        if kind == "poi" and len(local) > 1:
            kind = "sidewalk"
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


def _in_bbox(pt: Sequence[float], bbox: Tuple[float, float, float, float]) -> bool:
    return bbox[0] <= float(pt[0]) <= bbox[2] and bbox[1] <= float(pt[1]) <= bbox[3]


def read_scene_contexts(scene_dataset_dir: str | Path | None, episode_ids: Sequence[str], buffer_m: float) -> List[SceneContext]:
    contexts: List[SceneContext] = []
    if scene_dataset_dir:
        root = Path(scene_dataset_dir)
        for file in [root / "scenes.jsonl", root / "scenes.json", root / "episodes.jsonl"]:
            if file.exists():
                rows = read_jsonl(file) if file.suffix == ".jsonl" else _read_any(file)
                for row in rows:
                    eid = str(row.get("episode_id") or row.get("scenario_id") or "shared")
                    rc = row.get("route_corridor") or row.get("metadata", {}).get("route_corridor") or {}
                    poly = rc.get("polyline") or row.get("route_polyline") or []
                    if not poly:
                        p0 = row.get("initial_ego_pose") or {}
                        pg = row.get("mission_goal") or {}
                        if p0 and pg:
                            poly = [[p0.get("x", 0.0), p0.get("y", 0.0)], [pg.get("x", 0.0), pg.get("y", 0.0)]]
                    poly = [[float(p[0]), float(p[1])] for p in poly if isinstance(p, (list, tuple)) and len(p) >= 2]
                    contexts.append(SceneContext(eid, row.get("map_name"), poly, _bbox(poly, buffer_m) if poly else None, row))
                break
    if contexts:
        wanted = set(episode_ids) if episode_ids else None
        return [c for c in contexts if wanted is None or c.episode_id in wanted] or contexts
    ids = list(episode_ids) or ["shared"]
    return [SceneContext(eid) for eid in ids]


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
        "step_free": a["step_free"] if a["step_free"] is not None else (False if str(t.get("highway", "")).lower() == "steps" else None),
        "obstacle": bool(_boolish(t.get("obstacle") or t.get("blocked")) or str(t.get("obstacle_state", "")).lower() == "blocked"),
        "lighting": a["lighting"],
        "shelter": a["shelter"],
        "crossing_type": t.get("crossing_type") or t.get("crossing") or ("crossing" if f.kind == "crossing" else f.kind),
        "obstacle_state": t.get("obstacle_state"),
    }


class AccessibilityFusionBuilder:
    """Build per-scenario accessibility graphs from OSM/OpenSidewalks/city GIS."""

    def __init__(self, transformer: CoordinateTransformer, snap_tolerance_m: float = 3.0, source_name: str = "nuplan_osm_opensidewalks_citygis") -> None:
        self.transformer = transformer
        self.snap_tolerance_m = float(snap_tolerance_m)
        self.source_name = source_name

    def build_for_scene(self, scene: SceneContext, features: List[GISFeature], min_nodes: int = 0, min_edges: int = 0, add_bidirectional: bool = True, pudo_connector_radius_m: float = 75.0) -> AccessibilityGraph:
        feats = self._crop(features, scene)
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

        for f in feats:
            attrs = _node_attrs_from_feature(f)
            if f.is_point:
                x, y = f.geometry[0]
                add_node(x, y, f.kind, f.source, f.confidence, f.feature_id, attrs)
                continue
            previous: Optional[str] = None
            for i, (x, y) in enumerate(f.geometry):
                kind = "crossing" if f.kind == "crossing" else "sidewalk"
                nid = add_node(x, y, kind, f.source, f.confidence, f.feature_id, attrs)
                if previous is not None and previous != nid:
                    a, b = nodes[previous], nodes[nid]
                    geom = [[a.x, a.y], [b.x, b.y]]
                    length = math.hypot(b.x - a.x, b.y - a.y)
                    ea = _edge_attrs_from_feature(f)
                    if ea.get("slope") is None and node_extra.get(previous, {}).get("elevation_m") is not None and node_extra.get(nid, {}).get("elevation_m") is not None:
                        ea["slope"] = abs(float(node_extra[nid]["elevation_m"]) - float(node_extra[previous]["elevation_m"])) / max(length, 0.001)
                    eid = f"{f.feature_id}:{i-1}:{i}"
                    edges.append(AccessibilityEdge(eid, previous, nid, max(0.001, length), confidence=f.confidence, geometry=geom, source=f.source, **ea))
                    if add_bidirectional and str(f.tags.get("oneway", "")).lower() not in {"yes", "true", "1"}:
                        edges.append(AccessibilityEdge(eid + ":rev", nid, previous, max(0.001, length), confidence=f.confidence, geometry=list(reversed(geom)), source=f.source, **ea))
                previous = nid

        self._snap_point_nodes(nodes, edges, target_kinds={"entrance"}, edge_kind="entrance_connector")
        self._snap_point_nodes(nodes, edges, target_kinds={"curb", "curb_ramp"}, edge_kind="curb_connector")
        if scene.route_polyline:
            self._add_pudo_connector_metadata(nodes, node_extra, scene.route_polyline, pudo_connector_radius_m)
        edges = self._dedupe_edges(edges, nodes)
        graph = AccessibilityGraph(scene.episode_id, list(nodes.values()), edges, {
            "source": self.source_name,
            "builder": "AccessibilityFusionBuilder",
            "map_name": scene.map_name,
            "route_bbox": scene.bbox,
            "snap_tolerance_m": self.snap_tolerance_m,
            "pudo_connector_radius_m": pudo_connector_radius_m,
            "node_attributes": node_extra,
        })
        if len(graph.nodes) < min_nodes or len(graph.edges) < min_edges:
            raise RuntimeError(f"accessibility graph too small for {scene.episode_id}: {len(graph.nodes)} nodes/{len(graph.edges)} edges; required {min_nodes}/{min_edges}")
        return graph

    def _crop(self, features: List[GISFeature], scene: SceneContext) -> List[GISFeature]:
        if scene.bbox is None:
            return features
        out = []
        for f in features:
            if any(_in_bbox(p, scene.bbox) for p in f.geometry):
                out.append(f)
        return out

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
        for nid, n in list(nodes.items()):
            if n.kind not in target_kinds:
                continue
            other, d = self._nearest_ped_node(nid, nodes, exclude_kinds=target_kinds | {"poi"})
            if other and d <= max(25.0, self.snap_tolerance_m * 4):
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
