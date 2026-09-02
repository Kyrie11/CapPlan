#!/usr/bin/env python
"""Train the V3 Executable Capability Frontier ranker.

Training is pairwise within a *single symbolic search frontier*: the oracle
skeleton successor should rank above alternative one-step-feasible successors
from the same (episode, passenger, anchor, phase, ledger) state.  This avoids the
class-imbalance failure mode of the old global completion-value BCE head.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import math
from pathlib import Path
import random
import sys
import time

import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capplan.data.capability_contracts import contract_episode_id
from capplan.data.schemas import contract_from_dict, transition_from_dict
from capplan.models.frontier_ranker import (
    FRONTIER_FEATURE_VERSION,
    FrontierFeatureSpec,
    build_frontier_features,
    frontier_feature_names,
)
from capplan.planning.typed_safe_budget_search import SearchConfig, SearchLabel, TypedSafeBudgetSearch
from capplan.semantics.capability_compiler import CapabilityCompiler
from capplan.semantics.resource_registry import DEFAULT_REGISTRY
from capplan.semantics.service_automaton import ServiceAutomaton
from capplan.semantics.typed_resource_algebra import init_ledger
from capplan.utils.serialization import dump_json, read_jsonl


def _split_ids(root: Path, split: str) -> set[str]:
    p = root / "splits" / f"{split}_episodes.txt"
    if p.exists():
        return {x.strip() for x in p.read_text(encoding="utf-8").splitlines() if x.strip()}
    eps = read_jsonl(root / "episodes.jsonl")
    by_declared = {str(x.get("episode_id")) for x in eps if str(x.get("split", "")) == split}
    return by_declared or {str(x.get("episode_id")) for x in eps}


def _load_subset(root: Path, split: str, episode_limit: int | None, seed: int):
    ids = sorted(_split_ids(root, split))
    if episode_limit and 0 < episode_limit < len(ids):
        ids = sorted(random.Random(seed).sample(ids, episode_limit))
    selected = set(ids)
    episodes = {str(x["episode_id"]): x for x in read_jsonl(root / "episodes.jsonl") if str(x.get("episode_id")) in selected}
    scenes = {str(x["episode_id"]): x for x in read_jsonl(root / "scenes.jsonl") if str(x.get("episode_id")) in selected}
    requests = defaultdict(list)
    for x in read_jsonl(root / "service_requests.jsonl"):
        if str(x.get("episode_id")) in selected:
            requests[str(x.get("episode_id"))].append(x)
    contracts = defaultdict(list)
    for x in read_jsonl(root / "capability_contracts.jsonl"):
        c = contract_from_dict(x); eid = contract_episode_id(c)
        if eid in selected:
            contracts[eid].append(c)
    transitions = defaultdict(list)
    for x in read_jsonl(root / "candidate_transitions.jsonl"):
        if str(x.get("episode_id")) in selected:
            t = transition_from_dict(x); transitions[t.episode_id].append(t)
    skeletons = {(str(x.get("episode_id")), str(x.get("passenger_id"))): x for x in read_jsonl(root / "skeleton_labels.jsonl") if str(x.get("episode_id")) in selected}
    return ids, episodes, scenes, requests, contracts, transitions, skeletons


def _trip_context(eid, meta, scene, requests, passenger_id):
    profile_key = str(passenger_id).split(":")[-1]
    req = next((r for r in requests if str(r.get("passenger_profile_id")) == profile_key), requests[0] if requests else {})
    return {
        **(meta or {}),
        "route_corridor": (scene or {}).get("route_corridor", (meta or {}).get("metadata", {}).get("route_corridor", {})),
        **((meta or {}).get("metadata") or {}),
        **((scene or {}).get("metadata") or {}),
        "service_request": req,
        "request_time_s": req.get("request_time_s", (meta or {}).get("request_time_s")),
        "origin_entrance_id": req.get("origin_entrance_id", (meta or {}).get("origin_anchor")),
        "destination_entrance_id": req.get("destination_entrance_id", (meta or {}).get("destination_anchor")),
    }


def build_pairs(root: Path, split: str, *, feature_mode: str, episode_limit: int | None, seed: int, max_negatives: int):
    ids, episodes, scenes, requests, contracts, transitions, skeletons = _load_subset(root, split, episode_limit, seed)
    compiler = CapabilityCompiler(DEFAULT_REGISTRY)
    searcher = TypedSafeBudgetSearch(ServiceAutomaton(), DEFAULT_REGISTRY, SearchConfig(no_completion_value_guidance=True, lambda_learned_feasibility=0.0))
    pos_rows, neg_rows = [], []
    stats = {"episodes": len(ids), "successful_passengers": 0, "frontier_groups": 0, "pairs": 0, "skipped_no_alternative": 0, "integrity_failures": 0}
    rng = random.Random(seed)
    for eid in ids:
        ts = transitions.get(eid, [])
        by_id = {t.transition_id: t for t in ts}
        outgoing = defaultdict(list)
        for t in ts:
            outgoing[(t.from_anchor, t.from_phase)].append(t)
        for contract in contracts.get(eid, []):
            sk = skeletons.get((eid, contract.passenger_id))
            tids = list((sk or {}).get("transitions") or [])
            if not tids:
                continue
            stats["successful_passengers"] += 1
            trip = _trip_context(eid, episodes.get(eid, {}), scenes.get(eid, {}), requests.get(eid, []), contract.passenger_id)
            compiled = compiler.compile(contract, trip_context=trip)
            clauses, groups = compiled.clauses, compiled.groups
            first = by_id.get(tids[0])
            if first is None:
                stats["integrity_failures"] += 1
                continue
            ledger = init_ledger({c.resource_name for c in clauses}, DEFAULT_REGISTRY)
            label = SearchLabel(first.from_anchor, first.from_phase, ledger, 0.0, [], [])
            for tid in tids:
                positive_edge = by_id.get(tid)
                if positive_edge is None:
                    stats["integrity_failures"] += 1
                    break
                candidates = list(outgoing.get((label.anchor, label.phase), []))
                feasible = []
                positive_successor = None
                for e in candidates:
                    ok, new_ledger, step, _ = searcher._try_expand(label, e, compiled, clauses, groups, None)
                    if not ok:
                        continue
                    succ = SearchLabel(e.to_anchor, e.to_phase, new_ledger, label.cost + e.cost, label.history + [e], label.steps + [step])
                    feat = build_frontier_features(successor_label=succ, transition=e, compiled=compiled, feature_mode=feature_mode)
                    feasible.append((e, succ, feat))
                    if e.transition_id == tid:
                        positive_successor = (e, succ, feat)
                if positive_successor is None:
                    stats["integrity_failures"] += 1
                    break
                negatives = [x for x in feasible if x[0].transition_id != tid]
                if negatives:
                    if max_negatives > 0 and len(negatives) > max_negatives:
                        negatives = rng.sample(negatives, max_negatives)
                    for _, _, nf in negatives:
                        pos_rows.append(positive_successor[2]); neg_rows.append(nf)
                    stats["frontier_groups"] += 1
                    stats["pairs"] += len(negatives)
                else:
                    stats["skipped_no_alternative"] += 1
                label = positive_successor[1]
    return np.asarray(pos_rows, dtype=np.float32), np.asarray(neg_rows, dtype=np.float32), stats


def make_model(torch, input_dim: int, hidden_dim: int):
    from torch import nn
    return nn.Sequential(
        nn.Linear(input_dim, hidden_dim), nn.ReLU(), nn.LayerNorm(hidden_dim),
        nn.Linear(hidden_dim, hidden_dim // 2), nn.ReLU(), nn.Linear(hidden_dim // 2, 1),
    )


def evaluate_pairwise(model, torch, pos, neg, mean, std, device, batch_size):
    model.eval(); wins = []; margins = []
    with torch.inference_mode():
        for i in range(0, len(pos), batch_size):
            p = torch.from_numpy(pos[i:i+batch_size]).to(device)
            n = torch.from_numpy(neg[i:i+batch_size]).to(device)
            p = (p - mean) / std; n = (n - mean) / std
            sp = model(p).squeeze(-1); sn = model(n).squeeze(-1)
            d = sp - sn
            wins.append((d > 0).float().cpu().numpy()); margins.append(d.float().cpu().numpy())
    if not wins:
        return {"pairwise_accuracy": 0.0, "mean_score_margin": 0.0, "num_pairs": 0}
    w = np.concatenate(wins); d = np.concatenate(margins)
    return {"pairwise_accuracy": float(w.mean()), "mean_score_margin": float(d.mean()), "num_pairs": int(len(w))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset_dir", required=True)
    ap.add_argument("--output_dir", required=True)
    ap.add_argument("--feature_mode", choices=["full", "structural"], default="full")
    ap.add_argument("--objective", choices=["pairwise", "bce"], default="pairwise")
    ap.add_argument("--train_episode_limit", type=int, default=None)
    ap.add_argument("--val_episode_limit", type=int, default=None)
    ap.add_argument("--max_negatives_per_frontier", type=int, default=8)
    ap.add_argument("--epochs", type=int, default=12)
    ap.add_argument("--batch_size", type=int, default=4096)
    ap.add_argument("--hidden_dim", type=int, default=128)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--weight_decay", type=float, default=1e-4)
    ap.add_argument("--seed", type=int, default=13)
    ap.add_argument("--device", default="auto")
    args = ap.parse_args()

    import torch
    import torch.nn.functional as F
    torch.manual_seed(args.seed); np.random.seed(args.seed); random.seed(args.seed)
    device = ("cuda" if torch.cuda.is_available() else "cpu") if args.device == "auto" else args.device
    out = Path(args.output_dir); out.mkdir(parents=True, exist_ok=True)
    root = Path(args.dataset_dir)

    t0 = time.time()
    p, n, train_stats = build_pairs(root, "train", feature_mode=args.feature_mode, episode_limit=args.train_episode_limit, seed=args.seed, max_negatives=args.max_negatives_per_frontier)
    pv, nv, val_stats = build_pairs(root, "val", feature_mode=args.feature_mode, episode_limit=args.val_episode_limit, seed=args.seed + 1, max_negatives=args.max_negatives_per_frontier)
    if len(p) == 0:
        raise RuntimeError("frontier training produced zero pairwise examples; check skeleton labels and split IDs")
    if len(pv) == 0:
        pv, nv = p.copy(), n.copy(); val_stats = {**train_stats, "fallback_to_train": True}
    all_train = np.concatenate([p, n], axis=0)
    mean_np = all_train.mean(axis=0).astype(np.float32)
    std_np = np.maximum(all_train.std(axis=0).astype(np.float32), 1e-4)
    mean = torch.from_numpy(mean_np).to(device); std = torch.from_numpy(std_np).to(device)

    model = make_model(torch, p.shape[1], args.hidden_dim).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=args.weight_decay)
    rng = np.random.default_rng(args.seed)
    history = []
    for epoch in range(1, args.epochs + 1):
        model.train(); order = rng.permutation(len(p)); total = 0.0; count = 0
        for j in range(0, len(order), args.batch_size):
            idx = order[j:j+args.batch_size]
            pb = torch.from_numpy(p[idx]).to(device); nb = torch.from_numpy(n[idx]).to(device)
            pb = (pb - mean) / std; nb = (nb - mean) / std
            sp = model(pb).squeeze(-1); sn = model(nb).squeeze(-1)
            if args.objective == "pairwise":
                loss = F.softplus(-(sp - sn)).mean()
            else:
                scores = torch.cat([sp, sn]); labels = torch.cat([torch.ones_like(sp), torch.zeros_like(sn)])
                loss = F.binary_cross_entropy_with_logits(scores, labels)
            opt.zero_grad(set_to_none=True); loss.backward(); opt.step()
            total += float(loss.detach().cpu()) * len(idx); count += len(idx)
        val = evaluate_pairwise(model, torch, pv, nv, mean, std, device, args.batch_size)
        rec = {"epoch": epoch, "train_loss": total/max(count,1), **val}
        history.append(rec)
        print(rec, flush=True)

    val = evaluate_pairwise(model, torch, pv, nv, mean, std, device, args.batch_size)
    ckpt = {
        "algorithm_version": "V3",
        "mechanism": "executable_capability_frontier_ranker",
        "feature_version": FRONTIER_FEATURE_VERSION,
        "feature_mode": args.feature_mode,
        "objective": args.objective,
        "input_dim": int(p.shape[1]),
        "hidden_dim": int(args.hidden_dim),
        "feature_names": frontier_feature_names(),
        "mean": mean_np.tolist(), "std": std_np.tolist(),
        "state_dict": {k: v.detach().cpu() for k, v in model.state_dict().items()},
        "train_stats": train_stats, "val_stats": val_stats, "val_metrics": val,
        "config": vars(args),
    }
    torch.save(ckpt, out / "checkpoint.pt")
    dump_json(out / "training_summary.json", {
        "algorithm_version": "V3", "feature_version": FRONTIER_FEATURE_VERSION,
        "feature_mode": args.feature_mode, "objective": args.objective,
        "train_stats": train_stats, "val_stats": val_stats, "val_metrics": val,
        "history": history, "wall_seconds": time.time()-t0,
    })
    print(f"saved {out/'checkpoint.pt'}", flush=True)


if __name__ == "__main__":
    main()
