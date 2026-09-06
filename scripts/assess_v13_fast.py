#!/usr/bin/env python
"""Preregistered V13 gate: one final exact-diagnosis representation contingency.

V12 falsified exact state/ledger memoization as a useful replay accelerator: the
semantic cache was exact but had little primary/replay overlap. V13 therefore
compiles the ledger-independent transition semantics for the fixed passenger
request and reuses that program across diagnostic replay ledgers. V13 may only
change how exact rejection semantics are executed; V11 SN-CPK acceptance,
primary search, and the canonical certificate must remain bit-for-bit equivalent.

If this gate fails, do not try another diagnostic cache/compilation variant. The
symbolic semantic backbone is treated as experimentally frozen and the next
algorithmic mainline moves to capability-query raw-evidence learning, with
primary-decision and explanation latency reported separately.
"""
from __future__ import annotations
import argparse,json
from pathlib import Path

T5=['DF_phase_macro_f1','DF_resource_macro_f1','DF_source_macro_f1','DF_certificate_exact_match']

def metrics(p): return json.loads((Path(p)/'metrics.json').read_text())
def load(p): return json.loads(Path(p).read_text())

def main():
    ap=argparse.ArgumentParser()
    for x in ['full','v11','v12','v2','structural','no_lazy','no_program','legacy']:
        ap.add_argument(f'--{x.replace("_","-")}',dest=x,required=True)
    for x in ['v11_v13','v12_v13','v2_v13','structural_v13','no_lazy_v13','no_program_v13','legacy_v13','exact_v11_v13']:
        ap.add_argument(f'--{x.replace("_","-")}',dest=x,required=True)
    for x in ['repeat_no_program_v13','repeat_v2_v13','repeat_legacy_v13']:
        ap.add_argument(f'--{x.replace("_","-")}',dest=x,required=True)
    ap.add_argument('--output',required=True); a=ap.parse_args()

    full,v11,v12,v2,structural,no_lazy,no_program,legacy=map(metrics,[a.full,a.v11,a.v12,a.v2,a.structural,a.no_lazy,a.no_program,a.legacy])
    p11,p12,p2,ps,pnl,pnp,plg=map(load,[a.v11_v13,a.v12_v13,a.v2_v13,a.structural_v13,a.no_lazy_v13,a.no_program_v13,a.legacy_v13])
    exact=load(a.exact_v11_v13)
    rnp,r2,rlg=map(load,[a.repeat_no_program_v13,a.repeat_v2_v13,a.repeat_legacy_v13])

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

    c['program_compiles']=float(full.get('DiagnosticCompiledProgramCompilesMean',0) or 0)>0
    c['program_reuse_positive']=float(full.get('DiagnosticCompiledProgramReuseRate',0) or 0)>0
    c['program_applications_exceed_compiles']=float(full.get('DiagnosticCompiledProgramApplicationsMean',0) or 0)>float(full.get('DiagnosticCompiledProgramCompilesMean',0) or 0)
    c['program_no_fallbacks']=abs(float(full.get('DiagnosticCompiledProgramFallbacksMean',1) or 0))<=1e-12
    c['diagnostic_replay_fires']=float(full.get('DiagnosticReplayRate',0) or 0)>0
    c['diagnostic_replay_never_rescues']=abs(float(full.get('DiagnosticReplayRescueRate',1) or 0))<=1e-12

    gains={k:float(full.get(k,0))-float(no_lazy.get(k,0)) for k in T5}
    c['lazy_same_decisions']=int(pnl.get('decision_mismatch_count',1))==0
    c['lazy_same_expansions']=int(pnl.get('expansion_mismatch_count',1))==0
    c['lazy_t5_nonnegative']=all(v>=-1e-12 for v in gains.values())
    c['lazy_t5_material']=max(gains.values())>=0.02

    # Direct causal timing: no-program and V13 full differ only by the compiled
    # diagnostic transition program. reference - candidate > 0 means V13 wins.
    ci_np=rnp.get('paired_latency_delta_ci95_episode_clustered_ms') or [0,0]
    c['repeated_beats_no_program_mean']=float(rnp.get('latency_delta_reference_minus_candidate_mean_ms',0))>0
    c['repeated_beats_no_program_ci']=float(ci_np[0])>0
    v13_repeat=float(r2.get('candidate_latency_mean_ms',1e30))
    v2_repeat=float(r2.get('reference_latency_mean_ms',0))
    c['repeated_within_2x_v2']=v13_repeat<=2.0*v2_repeat
    c['repeated_v2_same_decisions']=int(r2.get('decision_mismatch_count_max_over_repeats',1))==0

    semantic_keys=['pc_f1','far_zero','frr_zero','v11_decision_exact','v11_expansion_exact','v11_skeleton_exact','v11_certificate_signature_exact','v11_certificate_full_exact']+[f't5::{k}' for k in T5]
    typed_keys=['typed_beats_structural_mean','typed_beats_structural_ci','typed_pruning_fires','kernel_complete']
    diag_keys=['program_compiles','program_reuse_positive','program_applications_exceed_compiles','program_no_fallbacks','diagnostic_replay_fires','diagnostic_replay_never_rescues','lazy_same_decisions','lazy_same_expansions','lazy_t5_nonnegative','lazy_t5_material']
    runtime_keys=['repeated_beats_no_program_mean','repeated_beats_no_program_ci','repeated_within_2x_v2','repeated_v2_same_decisions']
    gates={
        'semantic_exactness':all(c[k] for k in semantic_keys),
        'typed_search_mechanism':all(c[k] for k in typed_keys),
        'compiled_diagnostic_program':all(c[k] for k in diag_keys),
        'runtime_closure':all(c[k] for k in runtime_keys),
    }

    # Secondary learned guidance classification; cannot rescue V13.
    same_decisions=int(plg.get('decision_mismatch_count',1))==0
    legacy_reduces_exp=float(plg.get('paired_expansion_delta_reference_minus_candidate_mean',0))<0
    ci_lg=rlg.get('paired_latency_delta_ci95_episode_clustered_ms') or [0,0]
    delta=float(rlg.get('latency_delta_reference_minus_candidate_mean_ms',0)) # legacy - full
    if same_decisions and legacy_reduces_exp and float(ci_lg[0])>0:
        guidance='RETIRE_DEFAULT'
    elif same_decisions and legacy_reduces_exp and float(ci_lg[1])<0:
        guidance='KEEP_OPTIONAL'
    else:
        guidance='INCONCLUSIVE_SECONDARY'

    status='GO' if all(gates.values()) else 'STOP'
    out={
        'status':status,'gates':gates,'checks':c,'t5_lazy_gain':gains,
        'v12_negative_control':{
            'state_cache_reuse_rate':float(v12.get('DiagnosticSemanticCacheHitRate',0) or 0),
            'v12_vs_v13':p12,
            'note':'V12 is retained only as the falsified exact-state-cache control; it cannot determine the V13 gate.'
        },
        'legacy_static_guidance':{
            'classification':guidance,'same_decisions':same_decisions,'reduces_expansions':legacy_reduces_exp,
            'legacy_minus_v13_repeated_latency_ms':delta,'ci95_episode_clustered_ms':ci_lg,
            'note':'Secondary ordering only; not a paper contribution and not a V13 gate rescue.'
        },
        'primary':{'v11_vs_v13':p11,'v12_vs_v13':p12,'v2_vs_v13':p2,'structural_vs_v13':ps,'no_lazy_vs_v13':pnl,'no_program_vs_v13':pnp,'legacy_vs_v13':plg,'exact_v11_vs_v13':exact},
        'repeated_timing':{'no_program_vs_v13':rnp,'v2_vs_v13':r2,'legacy_vs_v13':rlg},
        'next_if_go':'Run 997-episode V13 full. If full confirms exactness and runtime, freeze symbolic execution/diagnosis representation and make CQ-HPT the next learned mainline.',
        'next_if_stop':'Do not try another diagnostic cache/compiled-replay micro-variant. Freeze the symbolic semantic backbone and report primary-decision vs explanation latency separately; next algorithmic mainline becomes CQ-HPT, whose learned outputs remain proposals/reliability/dynamics under exact symbolic authority.'
    }
    Path(a.output).write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps(out,indent=2,ensure_ascii=False))
if __name__=='__main__': main()
