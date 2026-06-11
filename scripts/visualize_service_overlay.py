#!/usr/bin/env python
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capplan.utils.serialization import read_jsonl


def _available_episode_ids(dataset_dir: Path) -> list[str]:
    ids: set[str] = {str(r.get("episode_id")) for r in read_jsonl(dataset_dir / "episodes.jsonl") if r.get("episode_id")}
    graph_dir = dataset_dir / "accessibility_graphs"
    if graph_dir.exists():
        for path in graph_dir.glob("*.nodes.jsonl"):
            ids.add(path.name[: -len(".nodes.jsonl")])
    return sorted(ids)


def _resolve_episode_id(dataset_dir: Path, episode_id: str | None) -> str:
    ids = _available_episode_ids(dataset_dir)
    if episode_id is None:
        if not ids:
            raise RuntimeError(f"No episodes found in {dataset_dir}")
        return ids[0]
    requested = str(episode_id)
    if requested in ids:
        return requested
    prefixed = f"nuplan_{requested}"
    if prefixed in ids:
        return prefixed
    suffix_matches = [eid for eid in ids if eid.endswith(requested)]
    if len(suffix_matches) == 1:
        return suffix_matches[0]
    if len(suffix_matches) > 1:
        sample = ", ".join(suffix_matches[:8])
        raise RuntimeError(f"Ambiguous episode_id suffix {requested!r}; matches: {sample}")
    sample = ", ".join(ids[:8]) if ids else "<none>"
    raise RuntimeError(f"Unknown episode_id {requested!r}. Available examples: {sample}")


def _load_episode_id(dataset_dir: Path, episode_id: str | None) -> str:
    return _resolve_episode_id(dataset_dir, episode_id)


def main() -> None:
    p = argparse.ArgumentParser(description="Visualize CapPlan service overlay alignment for one episode.")
    p.add_argument("--dataset_dir", required=True)
    p.add_argument("--episode_id", default=None)
    p.add_argument("--output", default=None)
    args = p.parse_args()

    dataset_dir = Path(args.dataset_dir)
    eid = _load_episode_id(dataset_dir, args.episode_id)
    nodes = read_jsonl(dataset_dir / "accessibility_graphs" / f"{eid}.nodes.jsonl")
    edges = read_jsonl(dataset_dir / "accessibility_graphs" / f"{eid}.edges.jsonl")
    pudo = [r for r in read_jsonl(dataset_dir / "pudo_anchors.jsonl") if r.get("episode_id") == eid]
    entrances = [r for r in read_jsonl(dataset_dir / "entrances.jsonl") if r.get("episode_id") == eid]
    if not nodes or not edges or not pudo or not entrances:
        raise RuntimeError(
            "Cannot visualize incomplete episode overlay: "
            f"episode_id={eid}, nodes={len(nodes)}, edges={len(edges)}, "
            f"pudo={len(pudo)}, entrances={len(entrances)}"
        )

    try:
        import matplotlib.pyplot as plt
    except Exception as e:
        raise RuntimeError("matplotlib is required: pip install matplotlib") from e

    fig, ax = plt.subplots(figsize=(8, 8))
    for edge in edges:
        geom = edge.get("geometry") or []
        if len(geom) >= 2:
            ax.plot([p[0] for p in geom], [p[1] for p in geom], linewidth=0.8, alpha=0.55)
    for node in nodes:
        marker = "o" if node.get("kind") != "pudo" else "s"
        ax.scatter([node.get("x")], [node.get("y")], marker=marker, s=18)
        if node.get("kind") in {"entrance", "pudo"}:
            ax.text(node.get("x"), node.get("y"), str(node.get("node_id")), fontsize=7)
    for anchor in pudo:
        pose = anchor.get("curb_pose", {})
        stop = anchor.get("stop_pose", {})
        ax.scatter([pose.get("x")], [pose.get("y")], marker="x", s=45)
        ax.plot([pose.get("x"), stop.get("x")], [pose.get("y"), stop.get("y")], linewidth=0.8)
        ax.text(pose.get("x"), pose.get("y"), anchor.get("anchor_id"), fontsize=7)
    for ent in entrances:
        pose = ent.get("pose", {})
        ax.scatter([pose.get("x")], [pose.get("y")], marker="*", s=90)
        ax.text(pose.get("x"), pose.get("y"), ent.get("anchor_id"), fontsize=8)
    ax.set_title(f"CapPlan service overlay: {eid}")
    ax.set_aspect("equal", adjustable="datalim")
    ax.grid(True, linewidth=0.2)
    out = Path(args.output) if args.output else dataset_dir / f"{eid}_service_overlay.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out, dpi=180)
    diagnostics = {
        "episode_id": eid,
        "requested_episode_id": args.episode_id,
        "num_nodes": len(nodes),
        "num_edges": len(edges),
        "num_pudo": len(pudo),
        "pudo_sources": sorted({str(a.get("source")) for a in pudo}),
        "entrance_sources": sorted({str(e.get("source")) for e in entrances}),
        "output": str(out),
    }
    print(json.dumps(diagnostics, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
