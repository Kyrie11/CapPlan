#!/usr/bin/env python
"""Machine-readable preregistered GO/STOP assessment for V7-fast."""
from __future__ import annotations
import argparse,json
from pathlib import Path

T5_KEYS=["DF_phase_macro_f1","DF_resource_macro_f1","DF_source_macro_f1","DF_certificate_exact_match"]
def metrics(path): return json.loads((Path(path)/"metrics.json").read_text())
def pair(path): return json.loads(Path(path).read_text())

def main():
    ap=argparse.ArgumentParser()
    for x in ['full','v2','v5','v6','structural','no_rejection']:
        ap.add_argument(f'--{x.replace("_","-")}',dest=x,required=True)
    for x in ['v7_v2','v7_v5','v7_v6','v7_structural','v7_no_rejection']:
        ap.add_argument(f'--{x.replace("_","-")}',dest=x,required=True)
    ap.add_argument('--output',required=True); a=ap.parse_args()
    full,v2,v5,v6,structural,no_rej=map(metrics,[a.full,a.v2,a.v5,a.v6,a.structural,a.no_rejection])
    p2,p5,p6,ps,pr=map(pair,[a.v7_v2,a.v7_v5,a.v7_v6,a.v7_structural,a.v7_no_rejection])
    c={}
    c['hard_pc_f1']=float(full.get('PCDecisionF1',0))>=0.99
    c['hard_far_zero']=abs(float(full.get('PCFalseAcceptRate',1)))<=1e-12
    c['hard_frr_zero']=abs(float(full.get('PCFalseRejectRate',1)))<=1e-12
    c['cf_flip_precision_no_regression']=float(full.get('CF_success_flip_precision',0))+1e-12>=float(v2.get('CF_success_flip_precision',0))
    c['cf_flip_recall_no_regression']=float(full.get('CF_success_flip_recall',0))+1e-12>=float(v2.get('CF_success_flip_recall',0))
    for k in T5_KEYS:
        c[f't5::{k}']=float(full.get(k,0))+0.01+1e-12>=float(v2.get(k,0))

    ci2=p2.get('paired_expansion_delta_ci95_episode_clustered') or [0,0]
    c['primary_beats_v2_mean']=float(p2.get('paired_expansion_delta_reference_minus_candidate_mean',0))>0
    c['primary_beats_v2_ci']=float(ci2[0])>0
    c['primary_decisions_identical']=int(p2.get('decision_mismatch_count',1))==0

    cis=ps.get('paired_expansion_delta_ci95_episode_clustered') or [0,0]
    c['typed_viability_fires']=float(full.get('CVK_typed_pruned_mean',0) or 0)>0
    c['typed_beats_structural_mean']=float(ps.get('paired_expansion_delta_reference_minus_candidate_mean',0))>0
    c['typed_beats_structural_ci']=float(cis[0])>0

    c['v5_equivalent_decisions']=int(p5.get('decision_mismatch_count',1))==0
    c['v5_equivalent_expansions']=abs(float(p5.get('paired_expansion_delta_reference_minus_candidate_mean',999)))<=1e-12
    c['no_raw_suffix_enumeration']=abs(float(full.get('WPA_raw_suffixes_mean',1)))<=1e-12
    c['no_raw_proof_enumeration']=abs(float(full.get('WPA_raw_proofs_mean',1)))<=1e-12
    c['direct_compiler_active']=float(full.get('DCP_build_candidates_mean',0) or 0)>0
    c['direct_candidate_work_below_v6_raw_universe']=float(full.get('DCP_build_candidates_mean',1e30)) < (float(v6.get('WPA_raw_suffixes_mean',0))+float(v6.get('WPA_raw_proofs_mean',0)))

    ci6=p6.get('paired_latency_delta_ci95_episode_clustered_ms') or [0,0]
    ci5=p5.get('paired_latency_delta_ci95_episode_clustered_ms') or [0,0]
    c['latency_beats_v6_mean']=float(p6.get('latency_delta_reference_minus_candidate_mean_ms',0))>0
    c['latency_beats_v6_ci']=float(ci6[0])>0
    c['latency_beats_v5_mean']=float(p5.get('latency_delta_reference_minus_candidate_mean_ms',0))>0
    c['latency_beats_v5_ci']=float(ci5[0])>0
    c['latency_within_2x_v2']=float(full.get('PlannerLatency_ms_mean',1e30))<=2.0*float(v2.get('PlannerLatency_ms_mean',0))

    c['rejection_same_decisions']=int(pr.get('decision_mismatch_count',1))==0
    c['rejection_same_expansions']=abs(float(pr.get('paired_expansion_delta_reference_minus_candidate_mean',999)))<=1e-12
    gains={k:float(full.get(k,0))-float(no_rej.get(k,0)) for k in T5_KEYS}
    c['rejection_nonnegative_all_t5']=all(v>=-1e-12 for v in gains.values())
    c['rejection_material_t5_gain']=max(gains.values())>=0.02
    c['rejection_frontier_active']=float(full.get('DCP_rejection_checks_mean',0) or 0)>0

    semantic=[k for k in c if k.startswith(('hard_','cf_','t5::','primary_'))]
    typed=['typed_viability_fires','typed_beats_structural_mean','typed_beats_structural_ci']
    rep=['v5_equivalent_decisions','v5_equivalent_expansions','no_raw_suffix_enumeration','no_raw_proof_enumeration','direct_compiler_active','direct_candidate_work_below_v6_raw_universe']
    scale=['latency_beats_v6_mean','latency_beats_v6_ci','latency_beats_v5_mean','latency_beats_v5_ci','latency_within_2x_v2']
    rej=['rejection_same_decisions','rejection_same_expansions','rejection_nonnegative_all_t5','rejection_material_t5_gain','rejection_frontier_active']
    gates={'semantic_and_primary':all(c[k] for k in semantic),'typed_viability':all(c[k] for k in typed),'direct_representation':all(c[k] for k in rep),'scalability':all(c[k] for k in scale),'asymmetric_rejection':all(c[k] for k in rej)}
    result={'status':'GO' if all(gates.values()) else 'STOP','gates':gates,'checks':c,
            'v7_vs_v2':p2,'v7_vs_v5':p5,'v7_vs_v6':p6,'v7_vs_structural_only':ps,'v7_vs_no_rejection':pr,
            't5_vs_v2':{k:float(full.get(k,0))-float(v2.get(k,0)) for k in T5_KEYS},
            't5_rejection_gain':gains,
            'note':'V7-fast tests direct asymmetric capability preconditions. Final latency claims require serial calibration and publication claims require full-test + integrated nuPlan closed loop.'}
    Path(a.output).write_text(json.dumps(result,indent=2,ensure_ascii=False)); print(json.dumps(result,indent=2,ensure_ascii=False))
if __name__=='__main__': main()
