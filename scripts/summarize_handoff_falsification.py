#!/usr/bin/env python3
"""Grouped hypothesis/falsification report; never combine event designs blindly."""
import argparse,json,statistics
from pathlib import Path
from collections import defaultdict


def main():
    p=argparse.ArgumentParser();p.add_argument('--rollouts',required=True);p.add_argument('--output',required=True)
    a=p.parse_args()
    data=defaultdict(lambda:defaultdict(list));n=defaultdict(int)
    for line in Path(a.rollouts).open(encoding='utf-8'):
        if not line.strip():continue
        r=json.loads(line)
        variant=r['overlay_id'].rsplit(':',1)[-1]
        n[variant]+=1
        for name,metrics in r['results'].items():
            data[variant][name].append({
             'success':metrics['weighted_candidate_success_rate'],
             'guarantee':int(metrics['world_support_guaranteed']),
             'false_commit':metrics['weighted_false_commit_rate'],
             'recourse_loss':metrics['weighted_post_commitment_recourse_loss_rate']})
    summary={}
    for variant,methods in data.items():
        summary[variant]={'overlays':n[variant],'methods':{}}
        for method,rs in methods.items():
            summary[variant]['methods'][method]={
              'mean_candidate_success':statistics.mean(v['success'] for v in rs),
              'world_support_guarantee_fraction':statistics.mean(v['guarantee'] for v in rs),
              'mean_conditional_false_commit':statistics.mean(v['false_commit'] for v in rs if v['false_commit'] is not None)
                   if any(v['false_commit'] is not None for v in rs) else None,
              'mean_conditional_recourse_loss':statistics.mean(v['recourse_loss'] for v in rs if v['recourse_loss'] is not None)
                   if any(v['recourse_loss'] is not None for v in rs) else None}
    result={'status':'CONTROLLED_FALSIFICATION_ONLY','event_family_results':summary,
       'not_claimed':['real blocker prevalence','real nuPlan motion verification',
                      'safe continuation','the necessity of a new kernel',
                      'a published resource-POMDP-shield baseline'],
       'interpretation':{
         'one_sided':'Fixed B is dominant by scenario construction; no novelty conclusion',
         'two_sided_observable':'Information can beat fixed B, but verify-then-guide can match exact',
         'two_sided_unobservable':'A causal policy may not robustly guarantee handoff; refusal is not service success',
         'both_fail_negative_control':'Some supported worlds physically lack any completion in the restricted O/A/B model',
         'late_event_negative_control':'After-handoff events should not create spurious planning difficulty'}}
    out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(result,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    return result

if __name__=='__main__':
    x=main();print(json.dumps({'event_families':list(x['event_family_results'])},ensure_ascii=False))
