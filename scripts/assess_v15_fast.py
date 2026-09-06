#!/usr/bin/env python
"""Preregistered V15 Exact Capability Robustness Potential (ECRP) gate."""
from __future__ import annotations
import argparse,json
from pathlib import Path
T5=['DF_phase_macro_f1','DF_resource_macro_f1','DF_source_macro_f1','DF_certificate_exact_match']
def load(p): return json.loads(Path(p).read_text())
def met(d): return load(Path(d)/'metrics.json')
def posci(c,k='expansion_delta_ci95_episode_clustered'): return float((c.get(k) or [0,0])[0])>0

def main():
    ap=argparse.ArgumentParser()
    for x in ['full','exact','legacy','count_only']: ap.add_argument(f'--{x.replace("_","-")}',dest=x,required=True)
    for x in ['full_exact','full_legacy','full_count','exactness','repeat_primary']: ap.add_argument(f'--{x.replace("_","-")}',dest=x,required=True)
    ap.add_argument('--output',required=True); a=ap.parse_args()
    full,exact,legacy,count=map(met,[a.full,a.exact,a.legacy,a.count_only])
    comps={n:load(getattr(a,n)) for n in ['full_exact','full_legacy','full_count']}; ex=load(a.exactness); rep=load(a.repeat_primary)
    c={}
    c['pc_f1']=float(full.get('PCDecisionF1',0))>=0.99; c['far_zero']=abs(float(full.get('PCFalseAcceptRate',1)))<1e-12; c['frr_zero']=abs(float(full.get('PCFalseRejectRate',1)))<1e-12
    c['decision_exact']=int(comps['full_exact'].get('decision_mismatch_count',1))==0
    c['certificate_json_exact']=int(ex.get('certificate_full_json_mismatch_count',1))==0
    for k in T5: c[f't5::{k}']=float(full.get(k,0))+0.01>=float(exact.get(k,0))
    for n in ['full_exact','full_legacy','full_count']:
        c[f'{n}_mean']=float(comps[n].get('expansion_delta_reference_minus_candidate_mean',0))>0
        c[f'{n}_ci']=posci(comps[n])
    c['primary_latency_beats_exact_mean']=float(rep.get('latency_delta_reference_minus_candidate_mean_ms',0))>0
    c['primary_latency_beats_exact_ci']=float((rep.get('paired_latency_delta_ci95_episode_clustered_ms') or [0,0])[0])>0
    c['repeat_decisions_exact']=int(rep.get('decision_mismatch_count_max_over_repeats',1))==0
    c['robustness_scored']=float(full.get('ECRP_scored_successors_mean',0))>0
    c['robustness_checks']=float(full.get('ECRP_summary_checks_mean',0))>0
    semantic=['pc_f1','far_zero','frr_zero','decision_exact','certificate_json_exact']+[f't5::{k}' for k in T5]
    causal=['full_exact_mean','full_exact_ci','full_legacy_mean','full_legacy_ci','full_count_mean','full_count_ci','robustness_scored','robustness_checks']
    operational=['primary_latency_beats_exact_mean','primary_latency_beats_exact_ci','repeat_decisions_exact']
    gates={'semantic_authority':all(c[k] for k in semantic),'typed_robustness_specific_gain':all(c[k] for k in causal),'operational_net_gain':all(c[k] for k in operational)}
    status='GO' if all(gates.values()) else 'STOP'
    out={'status':status,'gates':gates,'checks':c,'comparisons':comps,'exactness':ex,'repeated_primary_timing':rep,
         'interpretation':{
             'full_vs_exact':'Does exact max-min continuation robustness improve the frozen exact SN-CPK ordering?',
             'full_vs_legacy':'Does ECRP beat the strongest surviving historical static learned ordering?',
             'full_vs_count':'Does typed robustness add value beyond merely counting executable continuation summaries at matched antichain-scan overhead?',
             'latency':'Does the small exact-ordering improvement have positive operational value on an already compressed frontier?'
         },
         'next_if_go':'Run 997-episode V15 full confirmatory. ECRP may be promoted as a search-ordering corollary of C2, not as a new headline contribution, only if full confirms net latency and typed-specific gain.',
         'next_if_stop':'Do not add another search-ordering mechanism. Freeze the exact SN-CPK backbone and shift remaining learning work to lower-level evidence/calibration or method-specific vehicle closed loop.'}
    Path(a.output).write_text(json.dumps(out,indent=2,ensure_ascii=False)); print(json.dumps(out,indent=2,ensure_ascii=False))
if __name__=='__main__': main()
