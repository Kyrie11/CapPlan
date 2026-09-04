#!/usr/bin/env python
"""Preregistered V8-fast GO/STOP assessment.

V8 is allowed to change the *representation* and diagnosis schedule relative to
V5/V7, but not hard passenger semantics.  Acceptance acceleration and failure
explanation are therefore gated separately.
"""
from __future__ import annotations
import argparse,json
from pathlib import Path

T5=['DF_phase_macro_f1','DF_resource_macro_f1','DF_source_macro_f1','DF_certificate_exact_match']
def metrics(p): return json.loads((Path(p)/'metrics.json').read_text())
def pair(p): return json.loads(Path(p).read_text())

def main():
    ap=argparse.ArgumentParser()
    for x in ['full','v2','v5','structural','no_kernel','no_lazy']:
        ap.add_argument(f'--{x.replace("_","-")}',dest=x,required=True)
    for x in ['v8_v2','v8_v5','v8_structural','v8_no_lazy']:
        ap.add_argument(f'--{x.replace("_","-")}',dest=x,required=True)
    ap.add_argument('--output',required=True); a=ap.parse_args()
    full,v2,v5,structural,no_kernel,no_lazy=map(metrics,[a.full,a.v2,a.v5,a.structural,a.no_kernel,a.no_lazy])
    p2,p5,ps,pl=map(pair,[a.v8_v2,a.v8_v5,a.v8_structural,a.v8_no_lazy])
    c={}
    c['hard_pc_f1']=float(full.get('PCDecisionF1',0))>=0.99
    c['hard_far_zero']=abs(float(full.get('PCFalseAcceptRate',1)))<=1e-12
    c['hard_frr_zero']=abs(float(full.get('PCFalseRejectRate',1)))<=1e-12
    c['hard_matches_v2_decisions']=int(p2.get('decision_mismatch_count',1))==0
    for k in T5:
        c[f't5::{k}']=float(full.get(k,0))+0.01+1e-12>=float(v2.get(k,0))

    ci2=p2.get('paired_expansion_delta_ci95_episode_clustered') or [0,0]
    cis=ps.get('paired_expansion_delta_ci95_episode_clustered') or [0,0]
    c['beats_v2_expansions_mean']=float(p2.get('paired_expansion_delta_reference_minus_candidate_mean',0))>0
    c['beats_v2_expansions_ci']=float(ci2[0])>0
    c['typed_beats_structural_mean']=float(ps.get('paired_expansion_delta_reference_minus_candidate_mean',0))>0
    c['typed_beats_structural_ci']=float(cis[0])>0
    c['typed_pruning_fires']=float(full.get('CVK_typed_pruned_mean',0) or 0)>0

    c['no_raw_suffixes']=abs(float(full.get('WPA_raw_suffixes_mean',1) or 0))<=1e-12
    c['no_raw_proofs']=abs(float(full.get('WPA_raw_proofs_mean',1) or 0))<=1e-12
    c['no_eager_rejection_frontier']=abs(float(full.get('DCP_rejection_antichain_size_mean',1) or 0))<=1e-12
    c['incremental_compiler_active']=float(full.get('DCP_build_candidates_mean',0) or 0)>0
    c['kernel_complete_on_fast']=abs(float(full.get('DCP_incomplete_states_mean',0) or 0))<=1e-12

    ci5=p5.get('paired_latency_delta_ci95_episode_clustered_ms') or [0,0]
    c['latency_beats_v5_mean']=float(p5.get('latency_delta_reference_minus_candidate_mean_ms',0))>0
    c['latency_beats_v5_ci']=float(ci5[0])>0
    c['latency_within_2x_v2']=float(full.get('PlannerLatency_ms_mean',1e30))<=2.0*float(v2.get('PlannerLatency_ms_mean',0))

    c['lazy_same_decisions']=int(pl.get('decision_mismatch_count',1))==0
    c['lazy_same_primary_expansions']=abs(float(pl.get('paired_expansion_delta_reference_minus_candidate_mean',999)))<=1e-12
    gains={k:float(full.get(k,0))-float(no_lazy.get(k,0)) for k in T5}
    c['lazy_nonnegative_all_t5']=all(v>=-1e-12 for v in gains.values())
    c['lazy_material_t5_gain']=max(gains.values())>=0.02
    c['lazy_replay_fires']=float(full.get('DiagnosticReplayRate',0) or 0)>0
    c['lazy_replay_never_rescues_plan']=abs(float(full.get('DiagnosticReplayRescueRate',1) or 0))<=1e-12

    semantic=[k for k in c if k.startswith(('hard_','t5::'))]
    search=['beats_v2_expansions_mean','beats_v2_expansions_ci','typed_beats_structural_mean','typed_beats_structural_ci','typed_pruning_fires']
    representation=['no_raw_suffixes','no_raw_proofs','no_eager_rejection_frontier','incremental_compiler_active','kernel_complete_on_fast']
    runtime=['latency_beats_v5_mean','latency_beats_v5_ci','latency_within_2x_v2']
    lazy=[k for k in c if k.startswith('lazy_')]
    gates={
        'semantic':all(c[k] for k in semantic),
        'search':all(c[k] for k in search),
        'incremental_representation':all(c[k] for k in representation),
        'runtime':all(c[k] for k in runtime),
        'lazy_diagnosis':all(c[k] for k in lazy),
    }
    result={
        'status':'GO' if all(gates.values()) else 'STOP',
        'gates':gates,'checks':c,'t5_lazy_gain':gains,
        'v8_vs_v2':p2,'v8_vs_v5':p5,'v8_vs_structural':ps,'v8_vs_no_lazy':pl,
        'note':'Fast mechanism-selection gate only. Publication claims require full test, independent lifecycle/typed-ledger controls, and method-specific nuPlan closed loop.'
    }
    Path(a.output).write_text(json.dumps(result,indent=2,ensure_ascii=False)); print(json.dumps(result,indent=2,ensure_ascii=False))
if __name__=='__main__': main()
