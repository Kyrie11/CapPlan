#!/usr/bin/env python3
"""Science V12: genuinely state-aware heuristic first-action screening vs full
finite belief-support AND/OR oracle on exactly the same V10 model.

Baselines: nearest route, vehicle-first, fresh-evidence-first, resource-slack
scoring, and depth-2/depth-3 robust receding-horizon lookahead. Heuristics
observe support/evidence, passenger/vehicle states and ACKs; NO hidden world
identity or future realized trace is directly disclosed. Each model.successors
call can use the declared robust simulator as with the generic solver.

Reports initial-action regret ONLY on completed oracle-WIN cases. The lookahead
is not a published external solver. Labels are finite-model correctness, not
real-world failure or online closed-loop task success.
"""
from __future__ import annotations
import argparse, json, math, sys
from collections import Counter,deque
from heapq import heappush,heappop
from pathlib import Path
from time import perf_counter
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from capplan.handoff.commitment_model_v10 import FiniteCommitmentModel,GenericSupportSolver,cheapest_heuristic_actions,State

class Heuristic:
    def __init__(self,m):
        self.m=m;self.visited=0;self.distance_cache={};self.vehicle_cache={};self.memo={}
    def walk_dist(self,a,b):
        key=(a,b)
        if key in self.distance_cache:return self.distance_cache[key]
        pq=[(0.,a)];best={a:0.}
        while pq:
            d,u=heappop(pq)
            if d>best[u]+1e-8:continue
            if u==b:break
            for e in self.m.pgraph[u]:
                if not bool(e.get('step_free',True)) or float(e.get('min_width_m',math.inf))+1e-8<self.m.width:continue
                nd=d+float(e['length_m']);v=e['to']
                if nd+1e-8<best.get(v,math.inf):best[v]=nd;heappush(pq,(nd,v))
        self.distance_cache[key]=best.get(b,math.inf)
        return self.distance_cache[key]
    def vehicle_steps(self,a,b):
        key=(a,b)
        if key in self.vehicle_cache:return self.vehicle_cache[key]
        q=deque([(a,0)]);seen={a};ans=math.inf
        while q:
            v,d=q.popleft()
            if v==b:ans=d;break
            for nxt in self.m.vgraph[v]:
                if nxt not in seen:seen.add(nxt);q.append((nxt,d+1))
        self.vehicle_cache[key]=ans
        return ans
    def score(self,s:State):
        m=self.m
        if s.tick>=m.horizon:return 1e6
        if s.walked>m.max_dist or s.waited>m.max_wait:return 1e6
        best=math.inf
        for anchor,data in m.sites.items():
            if data.get('motion_ok') is not True or data.get('interface_ok') is not True:continue
            dst=self.walk_dist(s.passenger,data.get('passenger_node',anchor))
            veh=self.vehicle_steps(s.vehicle,anchor)
            if not math.isfinite(dst) or not math.isfinite(veh) or s.walked+dst>m.max_dist+1e-9:continue
            fresh=any(z==anchor and ok is True and expiry>=s.tick+m.board_ticks for z,ok,_,expiry in s.evidence)
            ack_rem=max(0,(s.acknowledgement_due or s.tick)-s.tick) if s.pending_instruction is not None else 0
            pending_penalty=1.5 if s.active_instruction not in (None,anchor) else 0.
            # Optimistic model-based lower-order estimate; no unobserved world ID used.
            tick_bound=s.tick+max(math.ceil(dst),veh,ack_rem)+m.board_ticks
            remaining=m.horizon-tick_bound
            score=2.*dst+veh+1.5*(not fresh)+pending_penalty+0.2*s.waited-0.5*remaining
            if remaining<0:score+=200
            if score<best:best=score
        return best if math.isfinite(best) else 1e5
    def successors(self,s,act):
        self.visited+=1
        return self.m.successors(s,act)
    def value(self,s,depth):
        if s is None:return 0.
        if depth==0:return self.score(s)
        key=(s,depth)
        if key in self.memo:return self.memo[key]
        vals=[]
        for act in self.m.actions(s):
            trans=self.successors(s,act)
            if trans is None:continue
            branches,local=trans
            # Treat all observation branches as possible; cannot pick world beforehand.
            v=local+max(self.value(nxt,depth-1) for _,nxt in branches)
            vals.append((v,act))
        best=min((v for v,_ in vals),default=self.score(s)+500)
        self.memo[key]=best
        return best
    def choose(self,depth):
        init=self.m.initial
        opts=[]
        for act in self.m.actions(init):
            trans=self.successors(init,act)
            if trans is None:continue
            branches,local=trans
            val=local+max(self.value(nxt,depth-1) for _,nxt in branches)
            opts.append((round(val,8),act))
        return min(opts)[1] if opts else None

def study(cases,max_states=15000,depths=(2,3)):
    rows=[];stats=Counter();family=Counter()
    names=['nearest_static','vehicle_first','fixed_evidence_anchor','slack_greedy']+[f'robust_depth_{d}' for d in depths]
    for case in cases:
        m=FiniteCommitmentModel(case)
        t0=perf_counter();ref=GenericSupportSolver(m,max_states=max_states).solve()
        exact_ms=(perf_counter()-t0)*1000
        # New model per heuristic avoids cross-run cache/query contamination.
        refs=set(ref.first_safe_actions) if ref.exact_action_set_complete else None
        row={'case_id':case.get('case_id'),'family':case.get('family'),
             'oracle_status':ref.status,'oracle_exact':ref.exact_action_set_complete,
             'oracle_safe_first_actions':sorted(refs) if refs is not None else None,
             'oracle_explored_states':ref.explored_states,'oracle_ms':round(exact_ms,4),
             'baseline':{}}
        if not ref.exact_action_set_complete:
            stats['oracle_unknown']+=1;rows.append(row);continue
        if ref.status=='WIN':stats['oracle_wins']+=1
        else:stats['oracle_losses']+=1
        proposals=cheapest_heuristic_actions(FiniteCommitmentModel(case))
        for name in names:
            mh=FiniteCommitmentModel(case);h=Heuristic(mh);start=perf_counter()
            if name=='slack_greedy':chosen=h.choose(1)
            elif name.startswith('robust_depth_'):chosen=h.choose(int(name.rsplit('_',1)[-1]))
            else:chosen=proposals.get(name)
            duration=(perf_counter()-start)*1000
            valid=chosen in mh.actions(mh.initial) if chosen is not None else False
            valid_tr=valid and mh.successors(mh.initial,chosen) is not None
            safe=(chosen in refs) if ref.status=='WIN' else None
            out={'action':chosen,'enabled':bool(valid_tr),
                 'certified_safe_first_action':safe,
                 'evaluated_model_successors':h.visited,
                 'planning_ms':round(duration,4)}
            row['baseline'][name]=out
            if ref.status=='WIN':
                stats[f'{name}_safe_first']+=int(bool(safe))
                stats[f'{name}_unsafe_first']+=int(not safe)
                family[(case.get('family'),name)]+=int(not safe)
        rows.append(row)
    summary={'cases':len(rows),'oracle_status':{'WIN':stats['oracle_wins'],'LOSE':stats['oracle_losses'],'UNKNOWN':stats['oracle_unknown']},
             'baselines':{name:{'safe_actions_on_oracle_win':stats[f'{name}_safe_first'],
                       'unsafe_actions_on_oracle_win':stats[f'{name}_unsafe_first'],
                       'denominator_exact_wins':stats['oracle_wins']} for name in names},
             'unsafe_by_family':{f'{x[0]}::{x[1]}':v for x,v in family.items()},
             'interpretation':'Only conditional synthetic first-action agreement. Not published-baseline replication, physical collision evidence, full rollout success, or generalization.'}
    return rows,summary

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--cases',required=True)
    p.add_argument('--output',required=True);p.add_argument('--max_states',type=int,default=15000)
    p.add_argument('--depths',default='2,3');p.add_argument('--limit',type=int,default=0)
    args=p.parse_args();depths=tuple(int(x) for x in args.depths.split(','))
    if args.max_states<1 or not depths or any(d<1 or d>5 for d in depths):p.error('invalid depth/max_states')
    cases=list(json.loads(x) for x in Path(args.cases).read_text().splitlines() if x.strip())
    if args.limit:cases=cases[:args.limit]
    rows,summary=study(cases,args.max_states,depths)
    out=Path(args.output);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows))
    out.with_suffix('.summary.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps(summary,ensure_ascii=False))
if __name__=='__main__':main()
