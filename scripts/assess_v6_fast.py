#!/usr/bin/env python
"""Machine-readable preregistered GO/STOP assessment for V6-fast."""
from __future__ import annotations
import argparse, json
from pathlib import Path

T5_KEYS=["DF_phase_macro_f1","DF_resource_macro_f1","DF_source_macro_f1","DF_certificate_exact_match"]

def metrics(path): return json.loads((Path(path)/"metrics.json").read_text())
def pair(path): return json.loads(Path(path).read_text())

def main():
    ap=argparse.ArgumentParser()
    for x in ['full','v2','v5','structural','no_proof']: ap.add_argument(f'--{x.replace("_","-")}', dest=x, required=True)
    for x in ['v6_v2','v6_v5','v6_structural','v6_no_proof']: ap.add_argument(f'--{x.replace("_","-")}', dest=x, required=True)
    ap.add_argument('--output', required=True)
    a=ap.parse_args()
    full,v2,v5,structural,no_proof=map(metrics,[a.full,a.v2,a.v5,a.structural,a.no_proof])
    p2,p5,ps,pp=map(pair,[a.v6_v2,a.v6_v5,a.v6_structural,a.v6_no_proof])
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

    # V6 is a representation/scalability refinement of the exact V5 mechanism.
    # It must not silently change which states V5 prunes.
    c['v5_equivalent_decisions']=int(p5.get('decision_mismatch_count',1))==0
    c['v5_equivalent_expansions']=abs(float(p5.get('paired_expansion_delta_reference_minus_candidate_mean',999)))<=1e-12
    c['antichain_compresses_or_equal']=float(full.get('WPA_antichain_size_mean',0))<=float(full.get('WPA_raw_suffixes_mean',0))+1e-12
    c['summary_checks_below_v5_path_checks']=float(full.get('WPA_summary_checks_mean',1e30))<float(v5.get('CVK_path_checks_mean',0))

    cil=p5.get('paired_latency_delta_ci95_episode_clustered_ms') or [0,0]
    c['latency_beats_v5_mean']=float(p5.get('latency_delta_reference_minus_candidate_mean_ms',0))>0
    c['latency_beats_v5_ci']=float(cil[0])>0
    c['latency_within_2x_v2']=float(full.get('PlannerLatency_ms_mean',1e30))<=2.0*float(v2.get('PlannerLatency_ms_mean',0))

    c['proof_same_decisions']=int(pp.get('decision_mismatch_count',1))==0
    c['proof_same_expansions']=abs(float(pp.get('paired_expansion_delta_reference_minus_candidate_mean',999)))<=1e-12
    proof_gain=max(float(full.get(k,0))-float(no_proof.get(k,0)) for k in T5_KEYS)
    c['proof_envelope_improves_t5']=proof_gain>=0.01

    semantic_keys=[k for k in c if k.startswith(('hard_','cf_','t5::','primary_'))]
    typed_keys=['typed_viability_fires','typed_beats_structural_mean','typed_beats_structural_ci']
    rep_keys=['v5_equivalent_decisions','v5_equivalent_expansions','antichain_compresses_or_equal','summary_checks_below_v5_path_checks']
    lat_keys=['latency_beats_v5_mean','latency_beats_v5_ci','latency_within_2x_v2']
    proof_keys=['proof_same_decisions','proof_same_expansions','proof_envelope_improves_t5']
    gates={
        'semantic_and_primary':all(c[k] for k in semantic_keys),
        'typed_viability':all(c[k] for k in typed_keys),
        'antichain_exactness':all(c[k] for k in rep_keys),
        'scalability':all(c[k] for k in lat_keys),
        'proof_envelope':all(c[k] for k in proof_keys),
    }
    result={
        'status':'GO' if all(gates.values()) else 'STOP',
        'gates':gates,'checks':c,
        'v6_vs_v2':p2,'v6_vs_v5':p5,'v6_vs_structural_only':ps,'v6_vs_no_proof':pp,
        't5_vs_v2':{k:float(full.get(k,0))-float(v2.get(k,0)) for k in T5_KEYS},
        't5_proof_gain':{k:float(full.get(k,0))-float(no_proof.get(k,0)) for k in T5_KEYS},
        'note':'V6-fast is a mechanism/scalability gate; publication claims require full-test confirmation and later integrated nuPlan closed-loop evaluation.'
    }
    Path(a.output).write_text(json.dumps(result,indent=2,ensure_ascii=False))
    print(json.dumps(result,indent=2,ensure_ascii=False))
if __name__=='__main__': main()
