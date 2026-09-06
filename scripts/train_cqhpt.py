#!/usr/bin/env python
"""Train CQ-HPT on exact multi-path capability-continuation teacher margins."""
from __future__ import annotations
import argparse, gzip, json, math, random, sys, time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import torch
import torch.nn.functional as F

from capplan.models.cqhpt import CQHPTConfig, CQHPTModel, CQHPTGuide, save_cqhpt_checkpoint
from capplan.models.cqhpt_features import RAW_TOKEN_DIM, cqhpt_query_feature_names
from capplan.utils.serialization import dump_json


def read_rows(path: Path) -> List[Dict[str,Any]]:
    op=gzip.open if str(path).endswith('.gz') else open
    rows=[]
    with op(path,'rt',encoding='utf-8') as f:
        for line in f:
            if line.strip(): rows.append(json.loads(line))
    return rows


def group_rows(rows):
    g=defaultdict(list)
    for r in rows: g[str(r['frontier_key'])].append(r)
    return [v for v in g.values() if len(v)>=2]


def build_pairs(groups, max_pairs_per_frontier:int, seed:int):
    rng=random.Random(seed); pairs=[]
    for rows in groups:
        candidates=[]
        for i in range(len(rows)):
            for j in range(i+1,len(rows)):
                a,b=rows[i],rows[j]; da=float(a['target_margin'])-float(b['target_margin'])
                if abs(da)<1e-6: continue
                hi,lo=(a,b) if da>0 else (b,a)
                candidates.append((hi,lo,abs(da)))
        if max_pairs_per_frontier and len(candidates)>max_pairs_per_frontier:
            candidates=rng.sample(candidates,max_pairs_per_frontier)
        pairs.extend(candidates)
    return pairs


def pad_tokens(rows: Sequence[Dict[str,Any]], device: torch.device):
    lists=[r.get('tokens') or [] for r in rows]
    return CQHPTGuide._pad_token_batch(lists,device)


def score_rows(model:CQHPTModel, rows, device):
    q=torch.as_tensor([r['query'] for r in rows],dtype=torch.float32,device=device)
    raw,typ,rel,mask=pad_tokens(rows,device)
    scores,_=model(q,raw,typ,rel,mask)
    return scores


def eval_pairs(model,pairs,device,batch_size):
    model.eval(); wins=[]; diffs=[]
    with torch.inference_mode():
        for i in range(0,len(pairs),batch_size):
            batch=pairs[i:i+batch_size]; hi=[x[0] for x in batch]; lo=[x[1] for x in batch]
            sh=score_rows(model,hi,device); sl=score_rows(model,lo,device); d=sh-sl
            wins.extend((d>0).float().cpu().tolist()); diffs.extend(d.cpu().tolist())
    return {'pairwise_accuracy':float(np.mean(wins) if wins else 0.0),'mean_score_delta':float(np.mean(diffs) if diffs else 0.0),'num_pairs':len(pairs)}


def eval_frontier_top1(model,groups,device,max_groups=2000):
    model.eval(); correct=0; total=0; regrets=[]
    with torch.inference_mode():
        for rows in groups[:max_groups]:
            scores=score_rows(model,rows,device).cpu().numpy(); targets=np.asarray([float(r['target_margin']) for r in rows])
            pred=int(scores.argmax()); best=float(targets.max()); chosen=float(targets[pred])
            correct += int(abs(chosen-best)<1e-7); total+=1; regrets.append(best-chosen)
    return {'top1_teacher_accuracy':correct/max(total,1),'mean_teacher_margin_regret':float(np.mean(regrets) if regrets else 0.0),'num_frontiers':total}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--train_teacher',required=True); ap.add_argument('--val_teacher',required=True); ap.add_argument('--output_dir',required=True)
    ap.add_argument('--mode',choices=['full','late_fusion','neutral_query','query_only'],required=True)
    ap.add_argument('--hidden_dim',type=int,default=96); ap.add_argument('--heads',type=int,default=4); ap.add_argument('--token_layers',type=int,default=2)
    ap.add_argument('--epochs',type=int,default=16); ap.add_argument('--batch_size',type=int,default=128); ap.add_argument('--max_pairs_per_frontier',type=int,default=12)
    ap.add_argument('--lr',type=float,default=8e-4); ap.add_argument('--weight_decay',type=float,default=1e-4); ap.add_argument('--regression_weight',type=float,default=0.05)
    ap.add_argument('--seed',type=int,default=13); ap.add_argument('--device',default='auto')
    args=ap.parse_args(); random.seed(args.seed); np.random.seed(args.seed); torch.manual_seed(args.seed)
    device=torch.device(('cuda' if torch.cuda.is_available() else 'cpu') if args.device=='auto' else args.device)
    tr_groups=group_rows(read_rows(Path(args.train_teacher))); va_groups=group_rows(read_rows(Path(args.val_teacher)))
    tr_pairs=build_pairs(tr_groups,args.max_pairs_per_frontier,args.seed); va_pairs=build_pairs(va_groups,args.max_pairs_per_frontier,args.seed+1)
    if not tr_pairs: raise RuntimeError('zero train pairs')
    if not va_pairs: va_pairs=tr_pairs[:min(len(tr_pairs),5000)]; va_groups=tr_groups[:min(len(tr_groups),1000)]
    cfg=CQHPTConfig(query_dim=len(cqhpt_query_feature_names()),hidden_dim=args.hidden_dim,num_heads=args.heads,token_layers=args.token_layers,mode=args.mode)
    model=CQHPTModel(cfg).to(device); opt=torch.optim.AdamW(model.parameters(),lr=args.lr,weight_decay=args.weight_decay)
    rng=random.Random(args.seed); history=[]; t0=time.time(); best=-1.0; best_state=None
    for epoch in range(1,args.epochs+1):
        model.train(); order=list(range(len(tr_pairs))); rng.shuffle(order); total_loss=0.0; n=0
        for j in range(0,len(order),args.batch_size):
            idx=order[j:j+args.batch_size]; batch=[tr_pairs[k] for k in idx]; hi=[x[0] for x in batch]; lo=[x[1] for x in batch]
            sh=score_rows(model,hi,device); sl=score_rows(model,lo,device)
            diff=torch.as_tensor([min(4.0,float(x[2])) for x in batch],dtype=torch.float32,device=device)
            rank=(F.softplus(-(sh-sl))*diff.clamp_min(0.1)).mean()
            th=torch.tanh(torch.as_tensor([float(x[0]['target_margin']) for x in batch],dtype=torch.float32,device=device))
            tl=torch.tanh(torch.as_tensor([float(x[1]['target_margin']) for x in batch],dtype=torch.float32,device=device))
            reg=0.5*(F.smooth_l1_loss(torch.tanh(sh),th)+F.smooth_l1_loss(torch.tanh(sl),tl))
            loss=rank+args.regression_weight*reg
            opt.zero_grad(set_to_none=True); loss.backward(); torch.nn.utils.clip_grad_norm_(model.parameters(),5.0); opt.step()
            total_loss+=float(loss.detach().cpu())*len(batch); n+=len(batch)
        val=eval_pairs(model,va_pairs,device,args.batch_size); top=eval_frontier_top1(model,va_groups,device)
        rec={'epoch':epoch,'train_loss':total_loss/max(n,1),**val,**top}; history.append(rec); print(rec,flush=True)
        metric=val['pairwise_accuracy']-0.2*top['mean_teacher_margin_regret']
        if metric>best:
            best=metric; best_state={k:v.detach().cpu().clone() for k,v in model.state_dict().items()}
    if best_state is not None: model.load_state_dict(best_state)
    val=eval_pairs(model,va_pairs,device,args.batch_size); top=eval_frontier_top1(model,va_groups,device)
    out=Path(args.output_dir); out.mkdir(parents=True,exist_ok=True)
    metadata={'algorithm_version':'V14','mechanism':'capability_query_heterogeneous_polyline_transformer','mode':args.mode,'train_frontiers':len(tr_groups),'val_frontiers':len(va_groups),'train_pairs':len(tr_pairs),'val_pairs':len(va_pairs),'val_metrics':{**val,**top},'config':vars(args)}
    save_cqhpt_checkpoint(out/'checkpoint.pt',model,metadata=metadata)
    dump_json(out/'training_summary.json',{**metadata,'history':history,'wall_seconds':time.time()-t0})
    print(f"saved {out/'checkpoint.pt'}",flush=True)

if __name__=='__main__': main()
