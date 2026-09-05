#!/usr/bin/env python
"""Preregistered V9-fast GO/STOP assessment.

V9 is a representation specialization of V8: the hard capability program
induces an observable resource quotient for backward acceptance preconditions.
The strongest gate is therefore semantic/search equivalence to V8 plus a
statistically supported runtime win.  Projection-specific compression is
required before the mechanism can be promoted.
"""
from __future__ import annotations
import argparse, json
from pathlib import Path

T5=['DF_phase_macro_f1','DF_resource_macro_f1','DF_source_macro_f1','DF_certificate_exact_match']
def metrics(p): return json.loads((Path(p)/'metrics.json').read_text())
def pair(p): return json.loads(Path(p).read_text())

def main():
    ap=argparse.ArgumentParser()
    for x in ['full','v8','v2','v5','structural','no_kernel','no_projection','no_index','no_lazy']:
        ap.add_argument(f'--{x.replace("_","-")}',dest=x,required=True)
    for x in ['v9_v8','v9_v2','v9_v5','v9_structural','v9_no_projection','v9_no_index','v9_no_lazy']:
        ap.add_argument(f'--{x.replace("_","-")}',dest=x,required=True)
    ap.add_argument('--output',required=True); a=ap.parse_args()
    full,v8,v2,v5,structural,no_kernel,no_projection,no_index,no_lazy=map(
        metrics,[a.full,a.v8,a.v2,a.v5,a.structural,a.no_kernel,a.no_projection,a.no_index,a.no_lazy]
    )
    p8,p2,p5,ps,pp,pi,pl=map(pair,[a.v9_v8,a.v9_v2,a.v9_v5,a.v9_structural,a.v9_no_projection,a.v9_no_index,a.v9_no_lazy])
    c={}
    c['hard_pc_f1']=float(full.get('PCDecisionF1',0))>=0.99
    c['hard_far_zero']=abs(float(full.get('PCFalseAcceptRate',1)))<=1e-12
    c['hard_frr_zero']=abs(float(full.get('PCFalseRejectRate',1)))<=1e-12
    c['hard_matches_v8_decisions']=int(p8.get('decision_mismatch_count',1))==0
    c['search_matches_v8_expansions']=int(p8.get('expansion_mismatch_count',1))==0
    for k in T5:
        c[f't5::{k}']=float(full.get(k,0))+0.01+1e-12>=float(v2.get(k,0))

    cis=ps.get('paired_expansion_delta_ci95_episode_clustered') or [0,0]
    c['typed_beats_structural_mean']=float(ps.get('paired_expansion_delta_reference_minus_candidate_mean',0))>0
    c['typed_beats_structural_ci']=float(cis[0])>0
    c['typed_pruning_fires']=float(full.get('CVK_typed_pruned_mean',0) or 0)>0

    c['projection_active']=float(full.get('CPK_projected_resource_count_mean',0) or 0)>0
    c['projection_drops_irrelevant_evidence']=float(full.get('CPK_projected_evidence_dropped_mean',0) or 0)>0
    c['projection_reduces_antichain']=float(full.get('WPA_antichain_size_mean',1e30)) < float(no_projection.get('WPA_antichain_size_mean',-1))
    c['projection_reduces_build_candidates']=float(full.get('DCP_build_candidates_mean',1e30)) < float(no_projection.get('DCP_build_candidates_mean',-1))
    c['kernel_complete_on_fast']=abs(float(full.get('DCP_incomplete_states_mean',0) or 0))<=1e-12
    c['no_raw_suffixes']=abs(float(full.get('WPA_raw_suffixes_mean',1) or 0))<=1e-12
    c['no_eager_rejection_frontier']=abs(float(full.get('DCP_rejection_antichain_size_mean',1) or 0))<=1e-12

    ci8=p8.get('paired_latency_delta_ci95_episode_clustered_ms') or [0,0]
    ci5=p5.get('paired_latency_delta_ci95_episode_clustered_ms') or [0,0]
    c['latency_beats_v8_mean']=float(p8.get('latency_delta_reference_minus_candidate_mean_ms',0))>0
    c['latency_beats_v8_ci']=float(ci8[0])>0
    c['latency_beats_v5_mean']=float(p5.get('latency_delta_reference_minus_candidate_mean_ms',0))>0
    c['latency_beats_v5_ci']=float(ci5[0])>0
    c['latency_within_2x_v2']=float(full.get('PlannerLatency_ms_mean',1e30))<=2.0*float(v2.get('PlannerLatency_ms_mean',0))
    c['build_time_below_v8_latency']=float(full.get('CPK_build_ms_mean',1e30)) < float(v8.get('PlannerLatency_ms_mean',0))

    # Index is an engineering specialization, not a paper-semantic claim.  It
    # is promoted only if it is non-worse on the paired fast set.
    ci_index=pi.get('paired_latency_delta_ci95_episode_clustered_ms') or [0,0]
    c['signature_index_nonworse_mean']=float(pi.get('latency_delta_reference_minus_candidate_mean_ms',0))>=0

    c['lazy_same_decisions']=int(pl.get('decision_mismatch_count',1))==0
    c['lazy_same_primary_expansions']=int(pl.get('expansion_mismatch_count',1))==0
    gains={k:float(full.get(k,0))-float(no_lazy.get(k,0)) for k in T5}
    c['lazy_nonnegative_all_t5']=all(v>=-1e-12 for v in gains.values())
    c['lazy_material_t5_gain']=max(gains.values())>=0.02
    c['lazy_replay_fires']=float(full.get('DiagnosticReplayRate',0) or 0)>0
    c['lazy_replay_never_rescues_plan']=abs(float(full.get('DiagnosticReplayRescueRate',1) or 0))<=1e-12

    semantic=[k for k in c if k.startswith(('hard_','search_matches_','t5::'))]
    search=['typed_beats_structural_mean','typed_beats_structural_ci','typed_pruning_fires']
    projection=['projection_active','projection_drops_irrelevant_evidence','projection_reduces_antichain','projection_reduces_build_candidates','kernel_complete_on_fast','no_raw_suffixes','no_eager_rejection_frontier']
    runtime=['latency_beats_v8_mean','latency_beats_v8_ci','latency_beats_v5_mean','latency_beats_v5_ci','latency_within_2x_v2','build_time_below_v8_latency']
    lazy=[k for k in c if k.startswith('lazy_')]
    gates={
        'v8_equivalent_semantics':all(c[k] for k in semantic),
        'typed_search_mechanism':all(c[k] for k in search),
        'capability_projection':all(c[k] for k in projection),
        'runtime':all(c[k] for k in runtime),
        'lazy_diagnosis':all(c[k] for k in lazy),
    }
    result={
        'status':'GO' if all(gates.values()) else 'STOP',
        'gates':gates,'checks':c,'t5_lazy_gain':gains,
        'v9_vs_v8':p8,'v9_vs_v2':p2,'v9_vs_v5':p5,'v9_vs_structural':ps,
        'v9_vs_no_projection':pp,'v9_vs_no_index':pi,'v9_vs_no_lazy':pl,
        'index_promotion':'PROMOTE' if c['signature_index_nonworse_mean'] else 'DROP_AND_KEEP_PROJECTION',
        'note':'Fast mechanism-selection gate only. Full test, fair lifecycle/typed-ledger baselines, site-heldout stress testing, and method-specific nuPlan closed loop remain publication requirements.'
    }
    Path(a.output).write_text(json.dumps(result,indent=2,ensure_ascii=False)); print(json.dumps(result,indent=2,ensure_ascii=False))
if __name__=='__main__': main()
