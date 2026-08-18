#!/usr/bin/env python
"""Conservatively prefill the PUDO/entrance audit CSV from normalized Tier-A evidence.

This script never converts a candidate layer into ground truth.  It spatially
matches only publication-grade official/audited records, copies each field with
its own provenance, and leaves unsupported fields blank.  The output therefore
serves as a machine-generated audit draft and residual-work manifest.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capplan.data.evidence_policy import (
    as_boolish,
    is_proxy_or_candidate,
    is_tier_a,
    legal_evidence_is_independent,
    physical_field_is_paper_grade,
    source_tier,
)

EARTH_R = 6371008.8
PHYSICAL = ["curb_height_m", "sidewalk_width_m", "deployment_clearance_m", "curb_ramp", "running_slope", "cross_slope", "surface"]
ENTRANCE_MAP = {
    "entrance_access_width_m": ["sidewalk_width_m", "width_m", "access_width_m"],
    "entrance_running_slope": ["running_slope", "slope"],
    "entrance_cross_slope": ["cross_slope"],
    "entrance_surface": ["surface"],
    "entrance_step_free": ["step_free"],
}
REQUIRED_PUDO = ["curb_height_m", "sidewalk_width_m", "deployment_clearance_m", "curb_ramp", "running_slope", "cross_slope", "surface", "legal_stop", "legal_basis"]
REQUIRED_ENTRANCE = ["entrance_id", "entrance_lon", "entrance_lat", *ENTRANCE_MAP.keys()]


def _haversine_m(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    lon1, lat1 = map(math.radians, a); lon2, lat2 = map(math.radians, b)
    dlat = lat2-lat1; dlon=lon2-lon1
    h=math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return 2*EARTH_R*math.asin(min(1.0, math.sqrt(h)))


def _local_xy_m(lon: float, lat: float, origin: Tuple[float, float]) -> Tuple[float, float]:
    """Small-area WGS84 projection centered on the query point.

    Audit matching radii are at most tens of metres, so an equirectangular
    tangent-plane approximation is more than adequate and avoids a heavy GIS
    dependency in this command-line audit helper.
    """
    lon0, lat0 = origin
    x = math.radians(lon - lon0) * EARTH_R * math.cos(math.radians(lat0))
    y = math.radians(lat - lat0) * EARTH_R
    return x, y


def _point_segment_distance_m(point: Tuple[float, float], a: Tuple[float, float], b: Tuple[float, float]) -> float:
    px, py = 0.0, 0.0
    ax, ay = _local_xy_m(a[0], a[1], point)
    bx, by = _local_xy_m(b[0], b[1], point)
    vx, vy = bx - ax, by - ay
    denom = vx * vx + vy * vy
    if denom <= 1e-12:
        return math.hypot(ax, ay)
    t = max(0.0, min(1.0, - (ax * vx + ay * vy) / denom))
    qx, qy = ax + t * vx, ay + t * vy
    return math.hypot(qx - px, qy - py)


def _geometry_lines(geometry: Any) -> List[List[Tuple[float, float]]]:
    if not isinstance(geometry, dict):
        return []
    typ = str(geometry.get("type") or "")
    c = geometry.get("coordinates")
    def line(coords: Any) -> List[Tuple[float, float]]:
        out=[]
        if isinstance(coords, list):
            for v in coords:
                if isinstance(v, (list, tuple)) and len(v) >= 2 and isinstance(v[0], (int, float)) and isinstance(v[1], (int, float)):
                    out.append((float(v[0]), float(v[1])))
        return out
    if typ == "Point" and isinstance(c, list) and len(c) >= 2:
        return [[(float(c[0]), float(c[1]))]]
    if typ == "MultiPoint":
        return [[p] for p in line(c)]
    if typ == "LineString":
        return [line(c)]
    if typ in {"MultiLineString", "Polygon"}:
        return [line(x) for x in (c or [])]
    if typ == "MultiPolygon":
        return [line(ring) for poly in (c or []) for ring in (poly or [])]
    return []


def _record_distance_m(row: Dict[str, Any], point: Tuple[float, float]) -> float:
    lines = _geometry_lines(row.get("geometry"))
    best = float("inf")
    for ln in lines:
        if len(ln) == 1:
            best = min(best, _haversine_m(point, ln[0]))
        for a, b in zip(ln, ln[1:]):
            best = min(best, _point_segment_distance_m(point, a, b))
    if best < float("inf"):
        return best
    p = _xy(row)
    return _haversine_m(point, p) if p is not None else float("inf")


def _props(row: Dict[str, Any]) -> Dict[str, Any]:
    p = row.get("properties") if isinstance(row.get("properties"), dict) else {}
    return {**p, **{k:v for k,v in row.items() if k not in {"properties", "geometry", "type"}}}


def _point_from_geometry(g: Any) -> Optional[Tuple[float, float]]:
    if not isinstance(g, dict): return None
    typ=g.get("type"); c=g.get("coordinates")
    if typ == "Point" and isinstance(c, list) and len(c)>=2:
        return float(c[0]), float(c[1])
    # For line/polygon evidence use a deterministic centroid of vertices.  This
    # is only for proximity matching; it is not emitted as an audited point.
    pts: List[Tuple[float,float]]=[]
    def walk(x: Any) -> None:
        if isinstance(x, list) and len(x)>=2 and isinstance(x[0], (int,float)) and isinstance(x[1], (int,float)):
            pts.append((float(x[0]),float(x[1])))
        elif isinstance(x, list):
            for y in x: walk(y)
    walk(c)
    if pts:
        return sum(x for x,_ in pts)/len(pts), sum(y for _,y in pts)/len(pts)
    return None


def _xy(row: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    d=_props(row)
    for a,b in [("lon","lat"),("longitude","latitude"),("lng","lat")]:
        if d.get(a) not in (None,"") and d.get(b) not in (None,""):
            try: return float(d[a]), float(d[b])
            except Exception: pass
    return _point_from_geometry(row.get("geometry"))


def _read(path: Path) -> List[Dict[str, Any]]:
    if not path.exists(): return []
    if path.suffix.lower() in {".geojson", ".json"}:
        obj=json.loads(path.read_text(encoding="utf-8"))
        if isinstance(obj, dict) and isinstance(obj.get("features"), list): return [dict(x) for x in obj["features"]]
        if isinstance(obj, list): return [dict(x) for x in obj]
        return [dict(obj)]
    if path.suffix.lower()==".csv":
        with path.open("r",encoding="utf-8-sig",newline="") as f: return [dict(x) for x in csv.DictReader(f)]
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def _nearest(records: Iterable[Dict[str, Any]], point: Tuple[float,float], max_m: float, pred=None) -> Tuple[Optional[Dict[str,Any]], float]:
    best=None; bd=float("inf")
    for r in records:
        d=_props(r)
        if pred is not None and not pred(d): continue
        dist=_record_distance_m(r, point)
        if not math.isfinite(dist): continue
        if dist < bd: best=r; bd=dist
    return (best,bd) if best is not None and bd <= max_m else (None,float("inf"))


def _field_value(d: Dict[str,Any], field: str) -> Any:
    aliases={
        "running_slope":["running_slope","slope","ramp_slope","landing_slope"],
        "sidewalk_width_m":["sidewalk_width_m","width_m","clear_width_m"],
        "deployment_clearance_m":["deployment_clearance_m","landing_width_m","clearance_m"],
        "curb_height_m":["curb_height_m"],
        "curb_ramp":["curb_ramp"],
        "cross_slope":["cross_slope"],
        "surface":["surface"],
    }
    for k in aliases.get(field,[field]):
        if d.get(k) not in (None,"","unknown","n/a"): return d[k]
    return None


def _copy_physical(row: Dict[str,str], records: List[Dict[str,Any]], point: Tuple[float,float], max_m: float) -> List[str]:
    notes=[]
    for field in PHYSICAL:
        if str(row.get(field) or "").strip(): continue
        candidates=[]
        for rec in records:
            d=_props(rec)
            if not is_tier_a(d) or is_proxy_or_candidate(d): continue
            value=_field_value(d,field)
            if value is None: continue
            # Apply field-grade policy to canonical value via a shallow canonical record.
            canon={**d, field:value}
            if not physical_field_is_paper_grade(canon,field): continue
            dist=_record_distance_m(rec, point)
            if dist <= max_m: candidates.append((dist,rec,value))
        if not candidates: continue
        candidates.sort(key=lambda x:x[0])
        dist,rec,value=candidates[0]; d=_props(rec)
        # If two nearly-equidistant Tier-A objects disagree, do not silently
        # choose one. The residual must be resolved by an object-id/field audit.
        if len(candidates) > 1 and candidates[1][0] <= dist + 3.0:
            v0 = str(value).strip().lower()
            v1 = str(candidates[1][2]).strip().lower()
            if v0 != v1:
                notes.append(f"AMBIGUOUS:{field}@{dist:.1f}/{candidates[1][0]:.1f}m")
                continue
        if field == "curb_ramp":
            b=as_boolish(value)
            if b is None: continue
            value="true" if b else "false"
        row[field]=str(value)
        row[f"{field}_source"]=str(d.get("source") or d.get("id") or "official_normalized")
        row[f"{field}_tier"]=source_tier(d)
        notes.append(f"{field}@{dist:.1f}m")
    return notes


def _copy_legality(row: Dict[str,str], records: List[Dict[str,Any]], point: Tuple[float,float], max_m: float) -> Optional[str]:
    if str(row.get("legal_stop") or "").strip() and str(row.get("legal_basis") or "").strip(): return None
    candidates=[]
    for rec in records:
        d=_props(rec)
        if not legal_evidence_is_independent(d):
            continue
        dist=_record_distance_m(rec, point)
        if dist <= max_m:
            candidates.append((dist,rec,as_boolish(d.get("legal_stop"))))
    if not candidates:
        return None
    candidates.sort(key=lambda x:x[0])
    best_dist,best_rec,best_legal=candidates[0]
    # Conflicting nearby regulations are an audit residual, not a majority vote.
    near=[x for x in candidates if x[0] <= best_dist + 3.0]
    states={x[2] for x in near}
    if len(states) > 1:
        return f"AMBIGUOUS_LEGALITY@{best_dist:.1f}m"
    d=_props(best_rec); legal=best_legal
    if legal is None: return None
    row["legal_stop"]="true" if legal else "false"
    row["legal_basis"]=str(d.get("legal_basis"))
    row["legal_stop_source"]=str(d.get("source") or d.get("regulation_id") or "official_regulation")
    row["legal_stop_tier"]=source_tier(d)
    row["time_window"]=str(d.get("time_window") or d.get("hours") or row.get("time_window") or "")
    return f"legality@{best_dist:.1f}m"


def _copy_entrance(row: Dict[str,str], records: List[Dict[str,Any]], point: Tuple[float,float], max_m: float) -> Optional[str]:
    if all(str(row.get(k) or "").strip() for k in ("entrance_id","entrance_lon","entrance_lat")):
        return None
    def good(d: Dict[str,Any]) -> bool:
        return is_tier_a(d) and not is_proxy_or_candidate(d) and str(d.get("kind") or "").lower() in {"entrance","building_entrance","station_entrance"}
    candidates=[]
    for rec in records:
        d=_props(rec)
        if not good(d): continue
        p=_xy(rec)
        if p is None: continue  # entrance truth must be an actual point, not a line centroid
        dist=_record_distance_m(rec, point)
        if dist <= max_m:
            candidates.append((dist,rec,p))
    if not candidates: return None
    candidates.sort(key=lambda x:x[0])
    dist,rec,p=candidates[0]
    if len(candidates) > 1 and candidates[1][0] <= dist + 3.0:
        id0=str(_props(rec).get("entrance_id") or _props(rec).get("feature_id") or _props(rec).get("id") or "")
        d1=_props(candidates[1][1]); id1=str(d1.get("entrance_id") or d1.get("feature_id") or d1.get("id") or "")
        if id0 != id1:
            return f"AMBIGUOUS_ENTRANCE@{dist:.1f}/{candidates[1][0]:.1f}m"
    d=_props(rec)
    row["entrance_id"]=str(d.get("entrance_id") or d.get("feature_id") or d.get("id") or "")
    row["entrance_lon"]=f"{p[0]:.8f}"; row["entrance_lat"]=f"{p[1]:.8f}"
    row["entrance_source"]=str(d.get("source") or "official_entrance")
    row["entrance_tier"]=source_tier(d)
    for out_key,aliases in ENTRANCE_MAP.items():
        value=None
        for k in aliases:
            if d.get(k) not in (None,"","unknown","n/a"):
                value=d[k]; break
        if value is None: continue
        if out_key == "entrance_step_free":
            b=as_boolish(value)
            if b is None: continue
            value="true" if b else "false"
        row[out_key]=str(value)
        row[f"{out_key}_source"]=str(d.get("source") or "official_entrance")
        row[f"{out_key}_tier"]=source_tier(d)
    return f"entrance@{dist:.1f}m"


def main() -> None:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--shortlist_csv",required=True)
    ap.add_argument("--city",required=True,choices=["boston","pittsburgh","vegas","singapore"])
    ap.add_argument("--external_root",required=True)
    ap.add_argument("--output_csv",required=True)
    ap.add_argument("--physical_match_m",type=float,default=15.0)
    ap.add_argument("--regulation_match_m",type=float,default=12.0)
    ap.add_argument("--entrance_match_m",type=float,default=75.0)
    ap.add_argument("--report_json",default=None)
    args=ap.parse_args()
    root=Path(args.external_root)
    with Path(args.shortlist_csv).open("r",encoding="utf-8-sig",newline="") as f:
        reader=csv.DictReader(f); rows=[dict(x) for x in reader]; fields=list(reader.fieldnames or [])
    for extra in [
        *[x for f in PHYSICAL for x in (f"{f}_source",f"{f}_tier")],
        "legal_stop_source","legal_stop_tier","entrance_source","entrance_tier",
        *[x for f in ENTRANCE_MAP for x in (f,f"{f}_source",f"{f}_tier")],
        "audit_method","manual_confirmed","auto_residual_fields",
    ]:
        if extra not in fields: fields.append(extra)
    physical=_read(root/"normalized"/"curb_inventory"/f"{args.city}.jsonl")
    regulations=_read(root/"normalized"/"curb_regulations"/f"{args.city}.jsonl")
    entrances=_read(root/"normalized"/"entrances"/f"{args.city}.geojson")
    stats={"rows":len(rows),"physical_prefilled":0,"legality_prefilled":0,"entrance_prefilled":0,"complete":0,"residual":0}
    for row in rows:
        try: point=(float(row["lon"]),float(row["lat"]))
        except Exception: raise RuntimeError(f"audit row {row.get('audit_id')} has invalid lon/lat")
        notes=[]
        pnotes=_copy_physical(row,physical,point,args.physical_match_m)
        if pnotes: stats["physical_prefilled"]+=1; notes += pnotes
        lnote=_copy_legality(row,regulations,point,args.regulation_match_m)
        if lnote: stats["legality_prefilled"]+=1; notes.append(lnote)
        enote=_copy_entrance(row,entrances,point,args.entrance_match_m)
        if enote: stats["entrance_prefilled"]+=1; notes.append(enote)
        residual=[k for k in [*REQUIRED_PUDO,*REQUIRED_ENTRANCE] if not str(row.get(k) or "").strip()]
        row["auto_residual_fields"]=";".join(residual)
        row["audit_method"]="official_tier_a_spatial_prefill"
        row["manual_confirmed"]="false"
        existing=str(row.get("notes") or "").strip()
        row["notes"]=(existing + (" | " if existing and notes else "") + "AUTO:" + ",".join(notes) if notes else existing)
        if residual: stats["residual"]+=1
        else: stats["complete"]+=1
    out=Path(args.output_csv); out.parent.mkdir(parents=True,exist_ok=True)
    with out.open("w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore"); w.writeheader(); w.writerows(rows)
    report={**stats,"status":"PASS","city":args.city,"input_csv":args.shortlist_csv,"output_csv":str(out),
            "tier_a_physical_records":sum(1 for r in physical if is_tier_a(_props(r)) and not is_proxy_or_candidate(_props(r))),
            "independent_legality_records":sum(1 for r in regulations if legal_evidence_is_independent(_props(r))),
            "tier_a_entrance_records":sum(1 for r in entrances if is_tier_a(_props(r)) and not is_proxy_or_candidate(_props(r))),
            "generated_at":datetime.now(timezone.utc).isoformat(),
            "interpretation":"Only independently auditable Tier-A fields were copied. auto_residual_fields must be resolved before paper build; candidate semantics were never promoted to truth."}
    if args.report_json:
        rp=Path(args.report_json); rp.parent.mkdir(parents=True,exist_ok=True); rp.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps(report,indent=2,sort_keys=True))

if __name__=="__main__": main()
