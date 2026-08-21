#!/usr/bin/env python
"""Render offline PUDO audit packets from the already-built accessibility graph.

The output is an HTML index plus one PNG per audit row.  It intentionally uses
only local project artifacts (no web tiles), so review is reproducible and does
not introduce hidden network/licensing dependencies.  The packet helps a human
check whether a candidate entrance/regulation/curb association is spatially
plausible; it is not itself a replacement for a field/photo audit when the
source lacks the required physical fact.
"""
from __future__ import annotations

import argparse
import csv
import html
import json
import math
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from capplan.data.gis_fusion import CoordinateTransformer


def _f(v: Any) -> Optional[float]:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _first_episode(row: Dict[str, Any]) -> Tuple[Optional[str], Optional[str]]:
    # Prefer test/val only for visualization stability; no split is rewritten.
    for split in ("test", "val", "train"):
        ids = [x.strip() for x in str(row.get(f"episode_ids_{split}") or "").split(";") if x.strip()]
        if ids:
            return split, ids[0]
    return None, None


def _read_jsonl(path: Path) -> Iterable[Dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                obj = json.loads(line)
                if isinstance(obj, dict):
                    yield obj


def _node_xy(row: Dict[str, Any]) -> Optional[Tuple[float, float]]:
    pose = row.get("pose") if isinstance(row.get("pose"), dict) else {}
    x = _f(pose.get("x") if pose else row.get("x"))
    y = _f(pose.get("y") if pose else row.get("y"))
    return (x, y) if x is not None and y is not None else None


def _in_radius(x: float, y: float, cx: float, cy: float, r: float) -> bool:
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def _segment_near(points: List[List[float]], cx: float, cy: float, r: float) -> bool:
    # Conservative bbox cull is enough for drawing; it cannot create evidence.
    if not points:
        return False
    xs = [float(p[0]) for p in points if len(p) >= 2]
    ys = [float(p[1]) for p in points if len(p) >= 2]
    return bool(xs and min(xs) <= cx + r and max(xs) >= cx - r and min(ys) <= cy + r and max(ys) >= cy - r)


class GraphCache:
    def __init__(self, data_root: Path, max_entries: int = 4) -> None:
        self.data_root = data_root
        self.max_entries = max_entries
        self.cache: OrderedDict[Tuple[str, str], Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]] = OrderedDict()

    def get(self, split: str, episode_id: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        key = (split, episode_id)
        if key in self.cache:
            value = self.cache.pop(key); self.cache[key] = value; return value
        root = self.data_root / "outputs" / "prepared" / split / "accessibility_graphs"
        np = root / f"{episode_id}.nodes.jsonl"
        ep = root / f"{episode_id}.edges.jsonl"
        if not np.exists() or not ep.exists():
            raise FileNotFoundError(f"missing graph for {split}/{episode_id}: {np} / {ep}")
        value = (list(_read_jsonl(np)), list(_read_jsonl(ep)))
        self.cache[key] = value
        while len(self.cache) > self.max_entries:
            self.cache.popitem(last=False)
        return value


def _render_one(row: Dict[str, Any], out_path: Path, transformer: CoordinateTransformer,
                cache: GraphCache, radius_m: float) -> Dict[str, Any]:
    lon = _f(row.get("lon")); lat = _f(row.get("lat"))
    if lon is None or lat is None:
        raise ValueError("invalid PUDO lon/lat")
    cx, cy = transformer.wgs84_to_local(lon, lat)
    split, eid = _first_episode(row)

    fig, ax = plt.subplots(figsize=(10.5, 8.0))
    plotted_nodes = plotted_edges = 0
    graph_error = None
    if split and eid:
        try:
            nodes, edges = cache.get(split, eid)
            local_nodes: Dict[str, Tuple[float, float]] = {}
            for n in nodes:
                xy = _node_xy(n)
                if xy and _in_radius(xy[0], xy[1], cx, cy, radius_m):
                    nid = str(n.get("node_id") or n.get("id") or "")
                    if nid:
                        local_nodes[nid] = xy
            for e in edges:
                geom = e.get("geometry") if isinstance(e.get("geometry"), list) else None
                pts: List[List[float]] = []
                if geom and all(isinstance(p, (list, tuple)) and len(p) >= 2 for p in geom):
                    pts = [[float(p[0]), float(p[1])] for p in geom]
                else:
                    u = local_nodes.get(str(e.get("from_node") or "")); v = local_nodes.get(str(e.get("to_node") or ""))
                    if u and v:
                        pts = [[u[0], u[1]], [v[0], v[1]]]
                if pts and _segment_near(pts, cx, cy, radius_m):
                    ax.plot([p[0] for p in pts], [p[1] for p in pts], linewidth=0.7, alpha=0.45)
                    plotted_edges += 1
            if local_nodes:
                ax.scatter([p[0] for p in local_nodes.values()], [p[1] for p in local_nodes.values()], s=5, alpha=0.4)
                plotted_nodes = len(local_nodes)
        except Exception as exc:
            graph_error = str(exc)

    ax.scatter([cx], [cy], s=100, marker="x", label="PUDO audit site")

    entrance_kind = None
    elon = _f(row.get("entrance_lon")); elat = _f(row.get("entrance_lat"))
    if elon is not None and elat is not None:
        entrance_kind = "entrance truth/source"
    else:
        elon = _f(row.get("entrance_candidate_lon")); elat = _f(row.get("entrance_candidate_lat"))
        if elon is not None and elat is not None:
            entrance_kind = "entrance candidate (NOT truth)"
    if elon is not None and elat is not None:
        ex, ey = transformer.wgs84_to_local(elon, elat)
        ax.scatter([ex], [ey], s=85, marker="o", label=entrance_kind)
        ax.plot([cx, ex], [cy, ey], linestyle="--", linewidth=1.0)
        ax.annotate(str(row.get("entrance_id") or row.get("entrance_candidate_id") or "entrance"), (ex, ey))

    ax.set_xlim(cx - radius_m, cx + radius_m)
    ax.set_ylim(cy - radius_m, cy + radius_m)
    ax.set_aspect("equal", adjustable="box")
    ax.grid(True, alpha=0.2)
    ax.legend(loc="upper right")
    decision = str(row.get("machine_triage_decision") or row.get("review_decision") or "")
    title = f"{row.get('audit_id', '')} | {row.get('city', '')} | {decision}"
    ax.set_title(title)
    note_lines = [
        f"episode={split or '-'} / {eid or '-'}",
        f"PUDO lon/lat=({lon:.7f}, {lat:.7f})",
        f"physical source={row.get('curb_height_m_source') or row.get('candidate_sources') or '-'}",
        f"legal={row.get('legal_stop') or '-'} basis={row.get('legal_basis') or '-'} source={row.get('legal_stop_source') or '-'}",
        f"entrance source={row.get('entrance_source') or row.get('entrance_candidate_source') or '-'}",
        f"entrance match m={row.get('entrance_match_distance_m') or row.get('entrance_candidate_match_distance_m') or '-'}",
        f"triage reasons={row.get('machine_triage_reasons') or row.get('review_reasons') or '-'}",
        "Interpretation: topology/context visualization only; spatial proximity is not semantic truth.",
    ]
    if graph_error:
        note_lines.append(f"graph warning={graph_error}")
    fig.text(0.02, 0.02, "\n".join(note_lines), fontsize=8, va="bottom")
    fig.tight_layout(rect=(0, 0.18, 1, 1))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return {"split": split, "episode_id": eid, "nodes_in_view": plotted_nodes, "edges_in_view": plotted_edges, "graph_error": graph_error}


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--input_csv", required=True)
    p.add_argument("--data_root", required=True)
    p.add_argument("--georeference_json", required=True)
    p.add_argument("--output_dir", required=True)
    p.add_argument("--radius_m", type=float, default=120.0)
    p.add_argument("--max_rows", type=int, default=0, help="0 renders all rows")
    p.add_argument("--report_json", default=None)
    args = p.parse_args()

    with Path(args.input_csv).open("r", encoding="utf-8-sig", newline="") as f:
        rows = [dict(r) for r in csv.DictReader(f)]
    if args.max_rows > 0:
        rows = rows[:args.max_rows]
    out_dir = Path(args.output_dir); out_dir.mkdir(parents=True, exist_ok=True)
    transformer = CoordinateTransformer.from_file(args.georeference_json)
    cache = GraphCache(Path(args.data_root))

    results: List[Dict[str, Any]] = []
    for i, row in enumerate(rows, 1):
        aid = str(row.get("audit_id") or f"row_{i:06d}").replace("/", "_")
        png = out_dir / f"{i:05d}_{aid}.png"
        try:
            stats = _render_one(row, png, transformer, cache, args.radius_m)
            status = "PASS"
        except Exception as exc:
            stats = {"graph_error": str(exc)}; status = "ERROR"
        results.append({"row": i, "audit_id": aid, "image": png.name if png.exists() else None, "status": status, **stats})

    html_rows = []
    for rec in results:
        img = f'<a href="{html.escape(rec["image"])}"><img src="{html.escape(rec["image"])}" width="420"></a>' if rec.get("image") else "(render failed)"
        html_rows.append(
            "<tr>" +
            f"<td>{rec['row']}</td><td>{html.escape(str(rec['audit_id']))}</td>" +
            f"<td>{html.escape(str(rec.get('status')))}</td><td>{html.escape(str(rec.get('episode_id') or '-'))}</td><td>{img}</td>" +
            "</tr>"
        )
    index = """<!doctype html><meta charset='utf-8'><title>CapPlan PUDO audit packet</title>
<style>body{font-family:sans-serif}table{border-collapse:collapse}td,th{border:1px solid #aaa;padding:6px;vertical-align:top}img{max-width:420px}</style>
<h1>CapPlan PUDO audit packet</h1>
<p>These plots are review aids only. A nearest/spatially plausible feature is not automatically a legal stop or intended entrance.</p>
<table><tr><th>#</th><th>audit id</th><th>render</th><th>representative episode</th><th>plot</th></tr>
""" + "\n".join(html_rows) + "</table>\n"
    (out_dir / "index.html").write_text(index, encoding="utf-8")
    report = {
        "status": "PASS" if all(r["status"] == "PASS" for r in results) else "WARN",
        "rows_requested": len(rows), "rendered": sum(r.get("image") is not None for r in results),
        "radius_m": args.radius_m, "output_dir": str(out_dir), "items": results,
        "interpretation": "Offline accessibility-graph context for human semantic review; not an automatic evidence source.",
    }
    report_path = Path(args.report_json) if args.report_json else out_dir / "manifest.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "items"}, indent=2, sort_keys=True))
    print("PUDO_AUDIT_PACKET_RENDER=PASS")


if __name__ == "__main__":
    main()
