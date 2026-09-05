#!/usr/bin/env python
"""Preregistered V10-fast gate: exact V9 semantics, cheaper construction."""
from __future__ import annotations
import argparse,json
from pathlib import Path
T5=['DF_phase_macro_f1','DF_resource_macro_f1','DF_source_macro_f1','DF_certificate_exact_match']
def metrics(p): return json.loads((Path(p)/'metrics.json').read_text())
def pair(p): return json.loads(Path(p).read_text())

def main():
    ap=argparse.ArgumentParser()
    for x in ['full','v9','v2','v5','structural','no_kernel','no_delta','no_packed','no_lazy']:
        ap.add_argument(f'--{x.replace("_","-")}',dest=x,required=True)
    for x in ['v10_v9','v10_v2','v10_v5','v10_structural','v10_no_delta','v10_no_packed','v10_no_lazy']:
        ap.add_argument(f'--{x.replace("_","-")}',dest=x,required=True)
    ap.add_argument('--output',required=True); a=ap.parse_args()
    full,v9,v2,v5,structural,no_kernel,no_delta,no_packed,no_lazy=map(metrics,[a.full,a.v9,a.v2,a.v5,a.structural,a.no_kernel,a.no_delta,a.no_packed,a.no_lazy])
    p9,p2,p5,ps,pd,pp,pl=map(pair,[a.v10_v9,a.v10_v2,a.v10_v5,a.v10_structural,a.v10_no_delta,a.v10_no_packed,a.v10_no_lazy])
    c={}
    c['hard_pc_f1']=float(full.get('PCDecisionF1',0))>=0.99
    c['hard_far_zero']=abs(float(full.get('PCFalseAcceptRate',1)))<=1e-12
    c['hard_frr_zero']=abs(float(full.get('PCFalseRejectRate',1)))<=1e-12
    c['hard_matches_v9_decisions']=int(p9.get('decision_mismatch_count',1))==0
    c['search_matches_v9_expansions']=int(p9.get('expansion_mismatch_count',1))==0
    for k in T5: c[f't5::{k}']=float(full.get(k,0))+0.01+1e-12>=float(v2.get(k,0))
    cis=ps.get('paired_expansion_delta_ci95_episode_clustered') or [0,0]
    c['typed_beats_structural_mean']=float(ps.get('paired_expansion_delta_reference_minus_candidate_mean',0))>0
    c['typed_beats_structural_ci']=float(cis[0])>0
    c['typed_pruning_fires']=float(full.get('CVK_typed_pruned_mean',0) or 0)>0
    c['kernel_complete_on_fast']=abs(float(full.get('DCP_incomplete_states_mean',0) or 0))<=1e-12

    # Construction mechanisms: each ablation must preserve primary semantics.
    c['delta_active']=float(full.get('SNK_delta_propagations_mean',0) or 0)>0 and float(full.get('SNK_delta_admissions_mean',0) or 0)>0
    c['delta_same_decisions']=int(pd.get('decision_mismatch_count',1))==0
    c['delta_same_expansions']=int(pd.get('expansion_mismatch_count',1))==0
    c['delta_reduces_candidates']=float(full.get('DCP_build_candidates_mean',1e30)) < float(no_delta.get('DCP_build_candidates_mean',-1))
    c['delta_reduces_dominance_checks']=float(full.get('CPK_frontier_dominance_checks_mean',1e30)) < float(no_delta.get('CPK_frontier_dominance_checks_mean',-1))
    c['packed_same_decisions']=int(pp.get('decision_mismatch_count',1))==0
    c['packed_same_expansions']=int(pp.get('expansion_mismatch_count',1))==0
    c['packed_fastpath_fires']=float(full.get('SNK_packed_fastpath_mean',0) or 0)>0
    ci_packed=pp.get('paired_latency_delta_ci95_episode_clustered_ms') or [0,0]
    c['packed_latency_nonworse']=float(pp.get('latency_delta_reference_minus_candidate_mean_ms',0))>=0

    ci9=p9.get('paired_latency_delta_ci95_episode_clustered_ms') or [0,0]
    ci5=p5.get('paired_latency_delta_ci95_episode_clustered_ms') or [0,0]
    c['latency_beats_v9_mean']=float(p9.get('latency_delta_reference_minus_candidate_mean_ms',0))>0
    c['latency_beats_v9_ci']=float(ci9[0])>0
    c['latency_beats_v5_mean']=float(p5.get('latency_delta_reference_minus_candidate_mean_ms',0))>0
    c['latency_beats_v5_ci']=float(ci5[0])>0
    c['latency_within_2x_v2']=float(full.get('PlannerLatency_ms_mean',1e30))<=2.0*float(v2.get('PlannerLatency_ms_mean',0))
    c['build_time_lower_than_v9']=float(full.get('CPK_build_ms_mean',1e30))<float(v9.get('CPK_build_ms_mean',-1))

    c['lazy_same_decisions']=int(pl.get('decision_mismatch_count',1))==0
    c['lazy_same_primary_expansions']=int(pl.get('expansion_mismatch_count',1))==0
    gains={k:float(full.get(k,0))-float(no_lazy.get(k,0)) for k in T5}
    c['lazy_nonnegative_all_t5']=all(v>=-1e-12 for v in gains.values())
    c['lazy_material_t5_gain']=max(gains.values())>=0.02
    c['lazy_replay_fires']=float(full.get('DiagnosticReplayRate',0) or 0)>0
    c['lazy_replay_never_rescues_plan']=abs(float(full.get('DiagnosticReplayRescueRate',1) or 0))<=1e-12

    semantic=[k for k in c if k.startswith(('hard_','search_matches_','t5::'))]
    search=['typed_beats_structural_mean','typed_beats_structural_ci','typed_pruning_fires','kernel_complete_on_fast']
    construction=['delta_active','delta_same_decisions','delta_same_expansions','delta_reduces_candidates','delta_reduces_dominance_checks','packed_same_decisions','packed_same_expansions','packed_fastpath_fires','packed_latency_nonworse']
    runtime=['latency_beats_v9_mean','latency_beats_v9_ci','latency_beats_v5_mean','latency_beats_v5_ci','latency_within_2x_v2','build_time_lower_than_v9']
    lazy=[k for k in c if k.startswith('lazy_')]
    gates={'v9_equivalent_semantics':all(c[k] for k in semantic),'typed_search_mechanism':all(c[k] for k in search),'exact_construction_closure':all(c[k] for k in construction),'runtime':all(c[k] for k in runtime),'lazy_diagnosis':all(c[k] for k in lazy)}
    result={'status':'GO' if all(gates.values()) else 'STOP','gates':gates,'checks':c,'t5_lazy_gain':gains,'v10_vs_v9':p9,'v10_vs_v2':p2,'v10_vs_v5':p5,'v10_vs_structural':ps,'v10_vs_no_delta':pd,'v10_vs_no_packed':pp,'v10_vs_no_lazy':pl,'note':'Fast exact-construction closure only. Do not introduce the heterogeneous learned backbone or run publication closed loop until this gate is resolved.'}
    Path(a.output).write_text(json.dumps(result,indent=2,ensure_ascii=False)); print(json.dumps(result,indent=2,ensure_ascii=False))
if __name__=='__main__': main()
