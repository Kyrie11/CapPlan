#!/usr/bin/env python3
"""Compare interval intersection with independent INTEGER-GRID enumeration.

This is a correctness & scalability microbenchmark for a fixed-start,
no-observation, fully declared finite-window subset. It is emphatically NOT
an implementation/performance comparison against published POMDP shielding.
"""
from __future__ import annotations
import argparse,json,random,time,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from capplan.handoff.temporal_recourse_v12 import analyze


def make_case(seed,horizon,sites,worlds):
    rng=random.Random(seed)
    support=[f'w{i}' for i in range(worlds)]
    options=[]
    for k in range(sites):
        t=rng.randrange(0,horizon//3)
        vstart=rng.randrange(0,horizon//2)
        eend=rng.randrange(horizon//2,horizon+1)
        option={'option_id':f'z{k}', 'passenger_arrival_window_s':[t,t+rng.randrange(0,4)],
                'passenger_arrival_quantifier':'all', 'passenger_wait_remaining_s':rng.randrange(4,15),
                'boarding_duration_s':rng.randrange(1,5),
                'contract_feasible':rng.random()>.1,
                'vehicle_stop_windows_s':[[vstart,horizon]],
                'evidence_validity_windows_s':[[0,eend]],
                'interface_windows_s':[[0,horizon]],
                'world_handoff_open_windows_s':{w:[[0,rng.randrange(0,horizon//2)],[rng.randrange(horizon//2,horizon-4),horizon]]
                         if rng.random()>.2 else [] for w in support}}
        options.append(option)
    return {'case_id':str(seed),'world_support':support,'options':options}


def slow_integer_grid(case,horizon):
    # Independent direct predicate, no code shared with interval implementation.
    for s in case['options']:
        if not s['contract_feasible']:continue
        a,b=s['passenger_arrival_window_s']
        d=s['boarding_duration_s'];wait=s['passenger_wait_remaining_s']
        for start in range(horizon+1):
            if not (b<=start<=a+wait):continue # robust over all arrivals
            if not all(any(x<=start and start+d<=y for x,y in s[field]) for field in
                       ('vehicle_stop_windows_s','evidence_validity_windows_s','interface_windows_s')):continue
            if all(any(x<=start and start+d<=y for x,y in s['world_handoff_open_windows_s'][w])
                   for w in case['world_support']):return True
    return False


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',required=True)
    p.add_argument('--seed',type=int,default=13)
    p.add_argument('--replicates',type=int,default=100)
    p.add_argument('--horizons',default='100,500,2000')
    p.add_argument('--options',type=int,default=8)
    p.add_argument('--worlds',type=int,default=4)
    a=p.parse_args()
    horizons=[int(t) for t in a.horizons.split(',')]
    if a.replicates<1 or a.options<1 or a.worlds<1 or any(h<12 for h in horizons):p.error('invalid arguments')
    blocks=[]
    for h in horizons:
        fast_ns=slow_ns=mismatch=wins=0
        for i in range(a.replicates):
            case=make_case(a.seed+i,h,a.options,a.worlds)
            st=time.perf_counter_ns();res=analyze(case)
            fast_ns+=time.perf_counter_ns()-st
            st=time.perf_counter_ns();slow=slow_integer_grid(case,h)
            slow_ns+=time.perf_counter_ns()-st
            fast=(res['status']=='ROBUST_FIXED_IN_DECLARED_MODEL')
            wins+=int(fast)
            mismatch+=int(fast!=slow)
        blocks.append({'horizon_s':h,'cases':a.replicates,'fixed_wins':wins,'mismatches':mismatch,
                       'interval_total_ms':round(fast_ns/1e6,4),'grid_total_ms':round(slow_ns/1e6,4),
                       'speedup_on_THIS_subset':round(slow_ns/fast_ns,3) if fast_ns else None})
    summary={'scope':'RESTRICTED_FIXED_START_INTERVAL_VERSUS_INTEGER_GRID_MICROBENCHMARK',
             'not_a_published_shield_baseline':True,'not_a_new_algorithms_CCF_A_speedup':True,
             'seed':a.seed,'options':a.options,'worlds':a.worlds,'horizons':blocks,
             'all_verified':all(x['mismatches']==0 for x in blocks)}
    out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'all_verified':summary['all_verified'],'horizons':blocks},ensure_ascii=False))
    if not summary['all_verified']:raise SystemExit(2)
if __name__=='__main__':main()
