#!/usr/bin/env python
"""Prepare compact passenger-independent raw-evidence banks for CQ-HPT.

This is a one-time preprocessing step.  It reads the already frozen
accessibility graphs, keeps only service-referenced lower-level path/elevation
geometry plus PUDO/dynamic/route context, and writes one compact JSONL row per
episode.  Runtime evaluation can then keep the historical saved-transition fast
path instead of reparsing full graph JSON for each episode.
"""
from __future__ import annotations
import argparse, random, sys
from pathlib import Path
from collections import defaultdict
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capplan.data.accessibility_layer import load_accessibility_graph
from capplan.data.schemas import pudo_from_dict, transition_from_dict
from capplan.models.cqhpt_features import build_episode_evidence_bank, CQHPT_EVIDENCE_CACHE_VERSION
from capplan.utils.serialization import read_jsonl, write_jsonl, dump_json


def _split_ids(root: Path, split: str) -> set[str]:
    p=root/'splits'/f'{split}_episodes.txt'
    if p.exists(): return {x.strip() for x in p.read_text().splitlines() if x.strip()}
    return {str(e['episode_id']) for e in read_jsonl(root/'episodes.jsonl') if str(e.get('split',''))==split}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--dataset_dir',required=True)
    ap.add_argument('--output',default=None)
    ap.add_argument('--splits',nargs='+',default=['train','val','test'])
    ap.add_argument('--episode_limit_per_split',type=int,default=0)
    ap.add_argument('--seed',type=int,default=13)
    ap.add_argument('--max_path_bank',type=int,default=0,help='0 keeps all service-referenced path edges; positive values are explicit engineering caps')
    args=ap.parse_args()
    root=Path(args.dataset_dir); out=Path(args.output) if args.output else root/'cqhpt_evidence_cache.jsonl'
    selected=set(); split_sets={}
    for i,split in enumerate(args.splits):
        ids=sorted(_split_ids(root,split))
        if args.episode_limit_per_split and args.episode_limit_per_split < len(ids):
            ids=sorted(random.Random(args.seed+i).sample(ids,args.episode_limit_per_split))
        selected.update(ids); split_sets[split]=set(ids)
    scenes={str(x['episode_id']):x for x in read_jsonl(root/'scenes.jsonl') if str(x.get('episode_id')) in selected}
    pudos=defaultdict(list)
    for x in read_jsonl(root/'pudo_anchors.jsonl'):
        if str(x.get('episode_id')) in selected:
            p=pudo_from_dict(x); pudos[p.episode_id].append(p)
    transitions=defaultdict(list)
    for x in read_jsonl(root/'candidate_transitions.jsonl'):
        if str(x.get('episode_id')) in selected:
            t=transition_from_dict(x); transitions[t.episode_id].append(t)
    rows=[]; truncated=0; missing_graph=[]
    for n,eid in enumerate(sorted(selected),1):
        try: graph=load_accessibility_graph(root,eid)
        except Exception as exc:
            missing_graph.append({'episode_id':eid,'error':repr(exc)}); continue
        row=build_episode_evidence_bank(graph=graph,pudos=pudos.get(eid,[]),scene=scenes.get(eid,{}),transitions=transitions.get(eid,[]),max_path_bank=args.max_path_bank)
        row['split']=next((sp for sp in args.splits if eid in split_sets.get(sp,set())),'unknown')
        truncated += int(row.get('path_bank_truncated',False)); rows.append(row)
        if n==1 or n%100==0: print(f'CQHPT cache {n}/{len(selected)} rows={len(rows)}',flush=True)
    write_jsonl(out,rows)
    summary={'version':CQHPT_EVIDENCE_CACHE_VERSION,'selected_episodes':len(selected),'written_episodes':len(rows),'truncated_path_banks':truncated,'missing_graph_count':len(missing_graph),'missing_graph_examples':missing_graph[:20],'output':str(out)}
    dump_json(out.with_suffix('.summary.json'),summary)
    print(summary,flush=True)
    if missing_graph: raise SystemExit(2)

if __name__=='__main__': main()
