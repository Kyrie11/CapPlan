#!/usr/bin/env python
"""Preregistered V11-fast gate: close the exact backbone before CQ-HPT.

V11-A is confirmatory and intentionally minimal: it keeps the already validated
V10 SN-CPK / lazy exact proof semantics and retires only the transition-static
learned ordering that V10-fast showed to be net slower.  V11-B (native quotient
composition) is exploratory and receives its own promotion/stop result; it can
never turn a failed V11-A confirmation into GO.
"""
from __future__ import annotations
import argparse, json
from pathlib import Path

T5 = ['DF_phase_macro_f1','DF_resource_macro_f1','DF_source_macro_f1','DF_certificate_exact_match']

def metrics(p): return json.loads((Path(p)/'metrics.json').read_text())
def pair(p): return json.loads(Path(p).read_text())

def main():
    ap=argparse.ArgumentParser()
    for x in ['full','v10','v2','v5','structural','no_kernel','legacy_guidance','no_lazy','native','native_no_fused']:
        ap.add_argument(f'--{x.replace("_","-")}',dest=x,required=True)
    for x in ['v11_v10','v11_v2','v11_v5','v11_structural','v11_legacy','v11_no_lazy','native_v11','native_no_fused_pair','recheck_v11_v2','recheck_v11_v10']:
        ap.add_argument(f'--{x.replace("_","-")}',dest=x,required=True)
    ap.add_argument('--recheck-full',required=True); ap.add_argument('--recheck-v2',required=True); ap.add_argument('--recheck-v10',required=True)
    ap.add_argument('--output',required=True); a=ap.parse_args()

    full,v10,v2,v5,structural,no_kernel,legacy,no_lazy,native,native_nf=map(metrics,[
        a.full,a.v10,a.v2,a.v5,a.structural,a.no_kernel,a.legacy_guidance,a.no_lazy,a.native,a.native_no_fused])
    p10,p2,p5,ps,plg,plazy,pnative,pnative_nf,pr2,pr10=map(pair,[
        a.v11_v10,a.v11_v2,a.v11_v5,a.v11_structural,a.v11_legacy,a.v11_no_lazy,a.native_v11,a.native_no_fused_pair,a.recheck_v11_v2,a.recheck_v11_v10])
    rfull,rv2,rv10=map(metrics,[a.recheck_full,a.recheck_v2,a.recheck_v10])

    c={}
    # Frozen passenger semantics.
    c['hard_pc_f1']=float(full.get('PCDecisionF1',0))>=0.99
    c['hard_far_zero']=abs(float(full.get('PCFalseAcceptRate',1)))<=1e-12
    c['hard_frr_zero']=abs(float(full.get('PCFalseRejectRate',1)))<=1e-12
    c['hard_matches_v10_decisions']=int(p10.get('decision_mismatch_count',1))==0
    for k in T5: c[f't5::{k}']=float(full.get(k,0))+0.01+1e-12>=float(v2.get(k,0))

    # Promoted typed backward viability must remain causally distinct from graph reachability.
    ci_s=ps.get('paired_expansion_delta_ci95_episode_clustered') or [0,0]
    c['typed_beats_structural_mean']=float(ps.get('paired_expansion_delta_reference_minus_candidate_mean',0))>0
    c['typed_beats_structural_ci']=float(ci_s[0])>0
    c['typed_pruning_fires']=float(full.get('CVK_typed_pruned_mean',0) or 0)>0
    c['kernel_complete_on_fast']=abs(float(full.get('DCP_incomplete_states_mean',0) or 0))<=1e-12

    # V11-A hypothesis: legacy ordering helps a few expansions but is net slower.
    c['legacy_guidance_same_decisions']=int(plg.get('decision_mismatch_count',1))==0
    c['legacy_guidance_reduces_expansions']=float(plg.get('paired_expansion_delta_reference_minus_candidate_mean',0))<0
    ci_retire=plg.get('paired_latency_delta_ci95_episode_clustered_ms') or [0,0]
    c['retiring_legacy_guidance_saves_latency_mean']=float(plg.get('latency_delta_reference_minus_candidate_mean_ms',0))>0
    c['retiring_legacy_guidance_saves_latency_ci']=float(ci_retire[0])>0

    # Publication-oriented cold runtime closure. Require both original and opposite-order timing.
    ci10=p10.get('paired_latency_delta_ci95_episode_clustered_ms') or [0,0]
    ci5=p5.get('paired_latency_delta_ci95_episode_clustered_ms') or [0,0]
    c['latency_beats_v10_mean']=float(p10.get('latency_delta_reference_minus_candidate_mean_ms',0))>0
    c['latency_beats_v10_ci']=float(ci10[0])>0
    c['latency_beats_v5_mean']=float(p5.get('latency_delta_reference_minus_candidate_mean_ms',0))>0
    c['latency_beats_v5_ci']=float(ci5[0])>0
    c['latency_within_2x_v2_primary']=float(full.get('PlannerLatency_ms_mean',1e30))<=2.0*float(v2.get('PlannerLatency_ms_mean',0))
    c['latency_within_2x_v2_recheck']=float(rfull.get('PlannerLatency_ms_mean',1e30))<=2.0*float(rv2.get('PlannerLatency_ms_mean',0))
    c['recheck_beats_v10_mean']=float(pr10.get('latency_delta_reference_minus_candidate_mean_ms',0))>0

    # Exact proof-on-demand remains a semantic requirement even though it costs latency.
    c['lazy_same_decisions']=int(plazy.get('decision_mismatch_count',1))==0
    c['lazy_same_primary_expansions']=int(plazy.get('expansion_mismatch_count',1))==0
    gains={k:float(full.get(k,0))-float(no_lazy.get(k,0)) for k in T5}
    c['lazy_nonnegative_all_t5']=all(v>=-1e-12 for v in gains.values())
    c['lazy_material_t5_gain']=max(gains.values())>=0.02
    c['lazy_replay_fires']=float(full.get('DiagnosticReplayRate',0) or 0)>0
    c['lazy_replay_never_rescues_plan']=abs(float(full.get('DiagnosticReplayRescueRate',1) or 0))<=1e-12

    semantic=[k for k in c if k.startswith(('hard_','t5::'))]
    search=['typed_beats_structural_mean','typed_beats_structural_ci','typed_pruning_fires','kernel_complete_on_fast']
    lean=[k for k in c if k.startswith(('legacy_guidance_','retiring_legacy_'))]
    runtime=['latency_beats_v10_mean','latency_beats_v10_ci','latency_beats_v5_mean','latency_beats_v5_ci','latency_within_2x_v2_primary','latency_within_2x_v2_recheck','recheck_beats_v10_mean']
    lazy=[k for k in c if k.startswith('lazy_')]
    gates={
        'semantic_exactness':all(c[k] for k in semantic),
        'typed_search_mechanism':all(c[k] for k in search),
        'legacy_guidance_retirement':all(c[k] for k in lean),
        'runtime_closure':all(c[k] for k in runtime),
        'lazy_diagnosis':all(c[k] for k in lazy),
    }

    # Exploratory V11-B. This result is informational and is not included in V11-A status.
    e={}
    e['same_decisions_as_v11']=int(pnative.get('decision_mismatch_count',1))==0
    e['same_expansions_as_v11']=int(pnative.get('expansion_mismatch_count',1))==0
    e['active']=float(native.get('NQK_native_compositions_mean',0) or 0)>0
    e['materializes_only_frontier']=0 < float(native.get('NQK_native_materializations_mean',0) or 0) < float(native.get('NQK_native_compositions_mean',0) or 0)
    e['no_fallback']=abs(float(native.get('NQK_native_fallbacks_mean',1) or 0))<=1e-12
    ci_native=pnative.get('paired_latency_delta_ci95_episode_clustered_ms') or [0,0]
    e['beats_v11_mean']=float(pnative.get('latency_delta_reference_minus_candidate_mean_ms',0))>0
    e['beats_v11_ci']=float(ci_native[0])>0
    e['fused_same_decisions']=int(pnative_nf.get('decision_mismatch_count',1))==0
    e['fused_same_expansions']=int(pnative_nf.get('expansion_mismatch_count',1))==0
    e['fused_active']=float(native.get('NQK_fused_frontier_passes_mean',0) or 0)>0
    e['fused_latency_nonworse']=float(pnative_nf.get('latency_delta_reference_minus_candidate_mean_ms',0))>=0
    exploratory_status='PROMOTE' if all(e.values()) else 'STOP_OR_REVISE'

    result={
        'status':'GO' if all(gates.values()) else 'STOP',
        'gates':gates,'checks':c,'t5_lazy_gain':gains,
        'exploratory_native_quotient':{'status':exploratory_status,'checks':e,'v11_reference_minus_native':pnative,'no_fused_minus_native':pnative_nf},
        'primary':{'v11_vs_v10':p10,'v11_vs_v2':p2,'v11_vs_v5':p5,'v11_vs_structural':ps,'v11_vs_legacy_guidance':plg,'v11_vs_no_lazy':plazy},
        'timing_recheck':{'v11_vs_v2':pr2,'v11_vs_v10':pr10,'v11_ms':rfull.get('PlannerLatency_ms_mean'),'v2_ms':rv2.get('PlannerLatency_ms_mean'),'v10_ms':rv10.get('PlannerLatency_ms_mean')},
        'note':'V11-A is the final exact-backbone closure gate. Only its GO permits CQ-HPT to become the next main algorithm version; the native quotient branch is exploratory only.'
    }
    Path(a.output).write_text(json.dumps(result,indent=2,ensure_ascii=False))
    print(json.dumps(result,indent=2,ensure_ascii=False))
if __name__=='__main__': main()
