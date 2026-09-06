#!/usr/bin/env python
"""Preregistered V12 gate: exact diagnostic-runtime closure before CQ-HPT.

V12 does not change the frozen passenger-complete acceptance semantics or the
V11 SN-CPK acceptance kernel. It asks whether proof-on-demand can reuse exact
forward semantic evaluations without changing one decision, expansion, skeleton
or canonical certificate, and whether that closes the repeated cold-latency gate.
The legacy static learned prior is classified separately because its micro-
latency sign changed between V10 and V11; that classification cannot rescue a
failed V12 exact-diagnosis gate.
"""
from __future__ import annotations
import argparse, json
from pathlib import Path

T5=['DF_phase_macro_f1','DF_resource_macro_f1','DF_source_macro_f1','DF_certificate_exact_match']

def metrics(p): return json.loads((Path(p)/'metrics.json').read_text())
def load(p): return json.loads(Path(p).read_text())

def main():
    ap=argparse.ArgumentParser()
    for x in ['full','v11','v2','structural','no_kernel','no_lazy','legacy']:
        ap.add_argument(f'--{x.replace("_","-")}',dest=x,required=True)
    for x in ['v11_v12','v2_v12','structural_v12','no_lazy_v12','legacy_v12','exact_v11_v12']:
        ap.add_argument(f'--{x.replace("_","-")}',dest=x,required=True)
    for x in ['repeat_v11_v12','repeat_v2_v12','repeat_legacy_v12']:
        ap.add_argument(f'--{x.replace("_","-")}',dest=x,required=True)
    ap.add_argument('--output',required=True); a=ap.parse_args()

    full,v11,v2,structural,no_kernel,no_lazy,legacy=map(metrics,[a.full,a.v11,a.v2,a.structural,a.no_kernel,a.no_lazy,a.legacy])
    p11,p2,ps,pnl,plg=map(load,[a.v11_v12,a.v2_v12,a.structural_v12,a.no_lazy_v12,a.legacy_v12])
    exact=load(a.exact_v11_v12)
    r11,r2,rlg=map(load,[a.repeat_v11_v12,a.repeat_v2_v12,a.repeat_legacy_v12])

    c={}
    c['pc_f1']=float(full.get('PCDecisionF1',0))>=0.99
    c['far_zero']=abs(float(full.get('PCFalseAcceptRate',1)))<=1e-12
    c['frr_zero']=abs(float(full.get('PCFalseRejectRate',1)))<=1e-12
    c['v11_decision_exact']=int(p11.get('decision_mismatch_count',1))==0
    c['v11_expansion_exact']=int(p11.get('expansion_mismatch_count',1))==0
    c['v11_skeleton_exact']=int(exact.get('skeleton_mismatch_count',1))==0
    c['v11_certificate_signature_exact']=int(exact.get('certificate_signature_mismatch_count',1))==0
    c['v11_certificate_full_exact']=int(exact.get('certificate_full_json_mismatch_count',1))==0
    for k in T5: c[f't5::{k}']=float(full.get(k,0))+0.01+1e-12>=float(v2.get(k,0))

    ci_s=ps.get('paired_expansion_delta_ci95_episode_clustered') or [0,0]
    c['typed_beats_structural_mean']=float(ps.get('paired_expansion_delta_reference_minus_candidate_mean',0))>0
    c['typed_beats_structural_ci']=float(ci_s[0])>0
    c['typed_pruning_fires']=float(full.get('CVK_typed_pruned_mean',0) or 0)>0
    c['kernel_complete']=abs(float(full.get('DCP_incomplete_states_mean',0) or 0))<=1e-12

    c['cache_active']=float(full.get('DiagnosticSemanticCacheHitsMean',0) or 0)>0
    c['cache_hit_rate_positive']=float(full.get('DiagnosticSemanticCacheHitRate',0) or 0)>0
    c['cache_has_primary_entries']=float(full.get('DiagnosticSemanticCachePrimaryStoresMean',0) or 0)>0
    c['diagnostic_replay_fires']=float(full.get('DiagnosticReplayRate',0) or 0)>0
    c['diagnostic_replay_never_rescues']=abs(float(full.get('DiagnosticReplayRescueRate',1) or 0))<=1e-12

    gains={k:float(full.get(k,0))-float(no_lazy.get(k,0)) for k in T5}
    c['lazy_same_decisions']=int(pnl.get('decision_mismatch_count',1))==0
    c['lazy_same_expansions']=int(pnl.get('expansion_mismatch_count',1))==0
    c['lazy_t5_nonnegative']=all(v>=-1e-12 for v in gains.values())
    c['lazy_t5_material']=max(gains.values())>=0.02

    # Timing claims must survive counterbalanced repeated orders. reference - candidate > 0 means V12 is faster.
    ci11=r11.get('paired_latency_delta_ci95_episode_clustered_ms') or [0,0]
    c['repeated_beats_v11_mean']=float(r11.get('latency_delta_reference_minus_candidate_mean_ms',0))>0
    c['repeated_beats_v11_ci']=float(ci11[0])>0
    v12_repeat=float(r2.get('candidate_latency_mean_ms',1e30))
    v2_repeat=float(r2.get('reference_latency_mean_ms',0))
    c['repeated_within_2x_v2']=v12_repeat<=2.0*v2_repeat
    c['repeated_v2_same_decisions']=int(r2.get('decision_mismatch_count_max_over_repeats',1))==0

    semantic_keys=['pc_f1','far_zero','frr_zero','v11_decision_exact','v11_expansion_exact','v11_skeleton_exact','v11_certificate_signature_exact','v11_certificate_full_exact']+[f't5::{k}' for k in T5]
    typed_keys=['typed_beats_structural_mean','typed_beats_structural_ci','typed_pruning_fires','kernel_complete']
    diag_keys=['cache_active','cache_hit_rate_positive','cache_has_primary_entries','diagnostic_replay_fires','diagnostic_replay_never_rescues','lazy_same_decisions','lazy_same_expansions','lazy_t5_nonnegative','lazy_t5_material']
    runtime_keys=['repeated_beats_v11_mean','repeated_beats_v11_ci','repeated_within_2x_v2','repeated_v2_same_decisions']
    gates={
        'semantic_exactness':all(c[k] for k in semantic_keys),
        'typed_search_mechanism':all(c[k] for k in typed_keys),
        'exact_diagnostic_reuse':all(c[k] for k in diag_keys),
        'runtime_closure':all(c[k] for k in runtime_keys),
    }

    # Separate repeated crossover classification for the legacy learned prior.
    same_decisions=int(plg.get('decision_mismatch_count',1))==0
    legacy_reduces_exp=float(plg.get('paired_expansion_delta_reference_minus_candidate_mean',0))<0
    ci_lg=rlg.get('paired_latency_delta_ci95_episode_clustered_ms') or [0,0]
    delta=float(rlg.get('latency_delta_reference_minus_candidate_mean_ms',0)) # legacy - full
    if same_decisions and legacy_reduces_exp and float(ci_lg[0])>0:
        guidance='RETIRE_DEFAULT'  # legacy slower than no-guidance V12
    elif same_decisions and legacy_reduces_exp and float(ci_lg[1])<0:
        guidance='KEEP_OPTIONAL'   # legacy measurably faster as ordering
    else:
        guidance='INCONCLUSIVE_SECONDARY'

    out={
        'status':'GO' if all(gates.values()) else 'STOP',
        'gates':gates,'checks':c,'t5_lazy_gain':gains,
        'legacy_static_guidance':{
            'classification':guidance,
            'same_decisions':same_decisions,
            'reduces_expansions':legacy_reduces_exp,
            'legacy_minus_v12_repeated_latency_ms':delta,
            'ci95_episode_clustered_ms':ci_lg,
            'note':'This micro-effect is not a paper contribution and does not determine the V12/CQ-HPT gate.',
        },
        'primary':{'v11_vs_v12':p11,'v2_vs_v12':p2,'structural_vs_v12':ps,'no_lazy_vs_v12':pnl,'legacy_vs_v12':plg,'exact_v11_vs_v12':exact},
        'repeated_timing':{'v11_vs_v12':r11,'v2_vs_v12':r2,'legacy_vs_v12':rlg},
        'next_if_go':'Run 997-episode V12 full, freeze exact symbolic backbone, then make CQ-HPT the next learned-algorithm mainline.',
        'next_if_stop':'Do not start CQ-HPT. Diagnose exact rejection extraction; selector-preserving diagnostic preconditions are the next contingency, with exact replay as fail-open fallback.',
    }
    Path(a.output).write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps(out,indent=2,ensure_ascii=False))
if __name__=='__main__': main()
