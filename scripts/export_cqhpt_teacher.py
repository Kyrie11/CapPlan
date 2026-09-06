#!/usr/bin/env python
"""Export exact multi-path CQ-HPT teacher frontiers from the frozen SN-CPK.

Unlike V3 single-skeleton imitation, every target is computed from the exact
capability-projected acceptance antichain.  The teacher is exported at the
*same frontier on which CQ-HPT is allowed to act*: only successors that already
pass hard local feasibility, typed backward viability, and dominance.  Thus the
network learns to order multiple exact-executable continuations; it is never
trained or used to rescue a hard-infeasible successor.
"""
from __future__ import annotations
import argparse, gzip, hashlib, json, random, sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from capplan.data.capability_contracts import contract_episode_id
from capplan.data.schemas import AccessibilityGraph, contract_from_dict, pudo_from_dict, transition_from_dict, vehicle_from_dict
from capplan.models.cqhpt_features import build_capability_query_features, select_transition_tokens_from_bank, CQHPT_FEATURE_VERSION
from capplan.planning.planner import CapPlanPlanner, PlannerConfig
from capplan.planning.typed_safe_budget_search import TypedSafeBudgetSearch
from capplan.utils.serialization import read_jsonl, dump_json


def split_ids(root: Path, split: str) -> list[str]:
    p=root/'splits'/f'{split}_episodes.txt'
    if p.exists(): return sorted(x.strip() for x in p.read_text().splitlines() if x.strip())
    return sorted(str(x['episode_id']) for x in read_jsonl(root/'episodes.jsonl') if str(x.get('split',''))==split)


def load_subset(root: Path, selected: set[str]):
    episodes={str(x['episode_id']):x for x in read_jsonl(root/'episodes.jsonl') if str(x.get('episode_id')) in selected}
    scenes={str(x['episode_id']):x for x in read_jsonl(root/'scenes.jsonl') if str(x.get('episode_id')) in selected}
    requests=defaultdict(list)
    for x in read_jsonl(root/'service_requests.jsonl'):
        if str(x.get('episode_id')) in selected: requests[str(x.get('episode_id'))].append(x)
    contracts=defaultdict(list)
    for x in read_jsonl(root/'capability_contracts.jsonl'):
        c=contract_from_dict(x); eid=contract_episode_id(c)
        if eid in selected: contracts[eid].append(c)
    transitions=defaultdict(list)
    for x in read_jsonl(root/'candidate_transitions.jsonl'):
        if str(x.get('episode_id')) in selected:
            t=transition_from_dict(x); transitions[t.episode_id].append(t)
    pudos=defaultdict(list)
    for x in read_jsonl(root/'pudo_anchors.jsonl'):
        if str(x.get('episode_id')) in selected:
            p=pudo_from_dict(x); pudos[p.episode_id].append(p)
    vehicles=defaultdict(list)
    for x in read_jsonl(root/'vehicle_interfaces.jsonl'):
        if str(x.get('episode_id')) in selected:
            v=vehicle_from_dict(x); vehicles[v.episode_id].append(v)
    banks={}
    cache=root/'cqhpt_evidence_cache.jsonl'
    if not cache.exists(): raise RuntimeError(f'missing {cache}; run prepare_cqhpt_evidence_cache.py first')
    for x in read_jsonl(cache):
        eid=str(x.get('episode_id') or '')
        if eid in selected: banks[eid]=x
    return episodes,scenes,requests,contracts,transitions,pudos,vehicles,banks


def trip_context(meta,scene,request,bank):
    return {**(meta or {}), 'route_corridor':(scene or {}).get('route_corridor',(meta or {}).get('metadata',{}).get('route_corridor',{})),
            'scene_record':scene, **((meta or {}).get('metadata') or {}), **((scene or {}).get('metadata') or {}),
            'service_request':request, 'request_time_s':request.get('request_time_s',(meta or {}).get('request_time_s')),
            'origin_entrance_id':request.get('origin_entrance_id',(meta or {}).get('origin_anchor')),
            'destination_entrance_id':request.get('destination_entrance_id',(meta or {}).get('destination_anchor')),
            'cqhpt_evidence_bank':bank}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--dataset_dir',required=True); ap.add_argument('--split',choices=['train','val'],required=True)
    ap.add_argument('--output',required=True); ap.add_argument('--casa_checkpoint',required=True)
    ap.add_argument('--episode_limit',type=int,default=0); ap.add_argument('--seed',type=int,default=13)
    ap.add_argument('--max_frontiers',type=int,default=0); ap.add_argument('--device',default='auto')
    args=ap.parse_args(); root=Path(args.dataset_dir)
    ids=split_ids(root,args.split)
    if args.episode_limit and args.episode_limit<len(ids): ids=sorted(random.Random(args.seed).sample(ids,args.episode_limit))
    selected=set(ids); episodes,scenes,requests,contracts,transitions,pudos,vehicles,banks=load_subset(root,selected)
    cfg=PlannerConfig(algorithm_version='V14',evidence_grounded_runtime=True,casa_mode='learned',casa_checkpoint=args.casa_checkpoint,casa_device=args.device,
                      no_cqhpt=True,no_completion_value_guidance=True,no_learned_feasibility_guidance=True,trajectory_mode='mock_strict')
    planner=CapPlanPlanner(cfg)
    out=Path(args.output); out.parent.mkdir(parents=True,exist_ok=True)
    opener=gzip.open if str(out).endswith('.gz') else open
    stats={'feature_version':CQHPT_FEATURE_VERSION,'split':args.split,'episodes':len(ids),'requests':0,'frontiers':0,'samples':0,'singletons':0,'teacher_missing':0,'bank_missing':0,'plans_success':0}
    stop=False
    with opener(out,'wt',encoding='utf-8') as f:
        for ei,eid in enumerate(ids,1):
            bank=banks.get(eid)
            if bank is None: stats['bank_missing']+=1; continue
            ts=transitions.get(eid,[]); vs=vehicles.get(eid,[])
            if not vs: continue
            reqs=requests.get(eid,[]); req_by_profile={str(r.get('passenger_profile_id')):r for r in reqs}
            for contract in contracts.get(eid,[]):
                if args.max_frontiers and stats['frontiers']>=args.max_frontiers: stop=True; break
                profile=str(contract.passenger_id).split(':')[-1]; req=req_by_profile.get(profile) or (reqs[0] if reqs else {})
                vid=req.get('vehicle_id') or req.get('fleet_vehicle_id')
                vehicle=next((v for v in vs if vid and v.vehicle_id==vid),next((v for v in vs if v.vehicle_id=='wav_ramp_right'),vs[0]))
                trip=trip_context(episodes.get(eid,{}),scenes.get(eid,{}),req,bank)
                groups: Dict[str,list[Dict[str,Any]]]=defaultdict(list)
                def cb(**kw):
                    teacher=kw.get('teacher')
                    if teacher is None or teacher.robust_margin is None or not teacher.viable:
                        stats['teacher_missing']+=1; return
                    parent=kw['parent_label']; succ=kw['successor_label']; edge=kw['transition']; compiled=kw['compiled']
                    sig=TypedSafeBudgetSearch._ledger_signature(parent.resource_ledger)
                    key=hashlib.sha1(repr((eid,contract.passenger_id,parent.anchor,parent.phase,sig)).encode()).hexdigest()[:20]
                    row={'frontier_key':key,'episode_id':eid,'passenger_id':contract.passenger_id,'profile':contract.metadata.get('profile_name') if isinstance(contract.metadata,dict) else None,
                         'counterfactual_axis':contract.metadata.get('counterfactual_axis') if isinstance(contract.metadata,dict) else None,
                         'transition_id':edge.transition_id,'action':edge.action,'from_phase':edge.from_phase,'to_phase':edge.to_phase,
                         'query':build_capability_query_features(successor_label=succ,transition=edge,compiled=compiled),
                         'tokens':select_transition_tokens_from_bank(bank=bank,transition=edge,vehicle=vehicle),
                         'target_margin':float(teacher.robust_margin),'viable_summary_count':int(teacher.viable_summary_count)}
                    groups[key].append(row)
                res=planner.plan(eid,contract,AccessibilityGraph(eid,[],[],{'cqhpt_teacher_fast_path':True}),pudos.get(eid,[]),vehicle,transitions=ts,trip_context=trip,frontier_trace_callback=cb)
                stats['requests']+=1; stats['plans_success']+=int(bool(res.success))
                for key,rows in groups.items():
                    # Need a genuine choice and a non-degenerate exact target.
                    if len(rows)<2: stats['singletons']+=1; continue
                    vals=[round(float(r['target_margin']),8) for r in rows]
                    if len(set(vals))<2: stats['singletons']+=1; continue
                    for r in rows: f.write(json.dumps(r,separators=(',',':'))+'\n')
                    stats['frontiers']+=1; stats['samples']+=len(rows)
            if ei==1 or ei%50==0: print(f"teacher {args.split} {ei}/{len(ids)} frontiers={stats['frontiers']} samples={stats['samples']}",flush=True)
            if stop: break
    dump_json(out.with_suffix(out.suffix+'.summary.json'),stats); print(stats,flush=True)
    if stats['frontiers']==0: raise SystemExit('zero CQ-HPT teacher frontiers')

if __name__=='__main__': main()
