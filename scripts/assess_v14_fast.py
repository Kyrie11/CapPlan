#!/usr/bin/env python
"""Preregistered V14 CQ-HPT necessity-validation gate.

The gate asks whether capability-conditioned heterogeneous evidence *routing*
adds value beyond capacity-matched controls.  Hard passenger semantics remain
exact and cannot be rescued by learned scores.
"""
from __future__ import annotations
import argparse,json
from pathlib import Path
T5=['DF_phase_macro_f1','DF_resource_macro_f1','DF_source_macro_f1','DF_certificate_exact_match']

def load(p): return json.loads(Path(p).read_text())
def met(d): return load(Path(d)/'metrics.json')

def positive_ci(comp,key='expansion_delta_ci95_episode_clustered'):
    ci=comp.get(key) or [0,0]; return float(ci[0])>0

def main():
    ap=argparse.ArgumentParser()
    for x in ['full','late','neutral','query_only','exact','legacy']:
        ap.add_argument(f'--{x.replace("_","-")}',dest=x,required=True)
    for x in ['full_exact','full_legacy','full_late','full_neutral','full_query','exactness','repeat_primary']:
        ap.add_argument(f'--{x.replace("_","-")}',dest=x,required=True)
    for x in ['train_full','train_late','train_neutral','train_query']:
        ap.add_argument(f'--{x.replace("_","-")}',dest=x,required=True)
    ap.add_argument('--output',required=True); a=ap.parse_args()
    full,late,neutral,qo,exact,legacy=map(met,[a.full,a.late,a.neutral,a.query_only,a.exact,a.legacy])
    comps={n:load(getattr(a,n)) for n in ['full_exact','full_legacy','full_late','full_neutral','full_query']}
    ex=load(a.exactness); rep=load(a.repeat_primary)
    tr={n:load(Path(getattr(a,f'train_{n}'))/'training_summary.json') for n in ['full','late','neutral','query']}
    c={}
    c['pc_f1']=float(full.get('PCDecisionF1',0))>=0.99; c['far_zero']=abs(float(full.get('PCFalseAcceptRate',1)))<1e-12; c['frr_zero']=abs(float(full.get('PCFalseRejectRate',1)))<1e-12
    c['decision_exact_vs_symbolic']=int(comps['full_exact'].get('decision_mismatch_count',1))==0
    c['failure_certificate_exact']=int(ex.get('certificate_full_json_mismatch_count',1))==0
    for k in T5: c[f't5::{k}']=float(full.get(k,0))+0.01>=float(exact.get(k,0))
    # Learned mechanism must beat exact/no-learning and the historical static prior.
    for name in ['full_exact','full_legacy','full_late','full_neutral','full_query']:
        cmp=comps[name]
        c[f'{name}_expansion_mean']=float(cmp.get('expansion_delta_reference_minus_candidate_mean',0))>0
        c[f'{name}_expansion_ci']=positive_ci(cmp)
    # Operational requirement: expansion savings must survive learned inference.
    c['primary_latency_beats_exact_mean']=float(rep.get('latency_delta_reference_minus_candidate_mean_ms',0))>0
    ci=rep.get('paired_latency_delta_ci95_episode_clustered_ms') or [0,0]
    c['primary_latency_beats_exact_ci']=float(ci[0])>0
    c['repeat_decisions_exact']=int(rep.get('decision_mismatch_count_max_over_repeats',1))==0
    # Training signal must actually be learnable; this is not sufficient alone.
    def acc(x): return float(tr[x].get('val_metrics',{}).get('pairwise_accuracy',0))
    c['full_teacher_pairwise']=acc('full')>=0.60
    c['full_teacher_not_worse_neutral']=acc('full')+0.01>=acc('neutral')
    c['full_teacher_not_worse_late']=acc('full')+0.01>=acc('late')
    semantic=['pc_f1','far_zero','frr_zero','decision_exact_vs_symbolic','failure_certificate_exact']+[f't5::{k}' for k in T5]
    necessity=[f'{n}_expansion_{s}' for n in ['full_exact','full_legacy','full_late','full_neutral','full_query'] for s in ['mean','ci']]
    operational=['primary_latency_beats_exact_mean','primary_latency_beats_exact_ci','repeat_decisions_exact']
    learning=['full_teacher_pairwise','full_teacher_not_worse_neutral','full_teacher_not_worse_late']
    gates={'semantic_authority':all(c[k] for k in semantic),'capability_routing_necessity':all(c[k] for k in necessity),'operational_net_gain':all(c[k] for k in operational),'teacher_learnability':all(c[k] for k in learning)}
    status='GO' if all(gates.values()) else 'STOP'
    out={'status':status,'gates':gates,'checks':c,'comparisons':comps,'exactness':ex,'repeated_primary_timing':rep,
         'training_validation':{k:tr[k].get('val_metrics',{}) for k in tr},
         'next_if_go':'Run 997-episode V14 full confirmatory. If full confirms capability-routing necessity and net primary-decision gain, promote CQ-HPT as the paper\'s learned mechanism under the frozen executable semantics, then run method-specific nuPlan closed loop.',
         'next_if_stop':'Do not keep CQ-HPT merely for architectural novelty. Inspect which matched-capacity gate failed: if full≈late/neutral, capability-conditioned routing is unproven; if query-only matches full, raw heterogeneous evidence is unnecessary; if only primary latency fails, retain the semantic backbone and seek a smaller evidence router rather than weakening hard authority.'}
    Path(a.output).write_text(json.dumps(out,indent=2,ensure_ascii=False)); print(json.dumps(out,indent=2,ensure_ascii=False))
if __name__=='__main__': main()
