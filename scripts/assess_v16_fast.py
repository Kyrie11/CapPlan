#!/usr/bin/env python
"""Preregistered V16 Parametric Capability Kernel fast gate."""
from __future__ import annotations
import argparse,json
from pathlib import Path

def J(p): return json.loads(Path(p).read_text())
def main():
    ap=argparse.ArgumentParser()
    for x in ['full','exact','full_key','full_exact','full_full_key','exactness','repeat_primary','output']: ap.add_argument('--'+x.replace('_','-'),required=True,dest=x)
    a=ap.parse_args(); full=J(Path(a.full)/'metrics.json'); sem=J(Path(a.full)/'evaluation_semantics.json'); fx=J(a.full_exact); fk=J(a.full_full_key); ex=J(a.exactness); rp=J(a.repeat_primary); control=J(Path(a.full_key)/'metrics.json')
    checks={
      'pc_f1':float(full.get('PCDecisionF1',0))>=.99,'far_zero':float(full.get('PCFalseAcceptRate',1))==0,'frr_zero':float(full.get('PCFalseRejectRate',1))==0,
      'decision_exact':int(ex.get('decision_mismatch_count',1))==0,'skeleton_exact':int(ex.get('skeleton_mismatch_count',1))==0,'certificate_json_exact':int(ex.get('certificate_full_json_mismatch_count',1))==0,
      'expansions_exact':abs(float(fx.get('expansion_delta_reference_minus_candidate_mean',1)))<1e-12,
      'parametric_semantics_registered':sem.get('v16_contract_shape_semantics')=='erase_only_cumulative_upper_lower_numeric_thresholds_v1',
      'cache_hits_positive':float(full.get('PCK_cache_hit_rate',0))>0,'thresholds_erased_positive':float(full.get('PCK_thresholds_erased_mean',0))>0,
      'full_key_has_less_reuse':float(full.get('PCK_cache_hit_rate',0))>float(control.get('PCK_cache_hit_rate',0)),
      'cpk_build_beats_exact_mean':float(fx.get('cpk_build_ms_delta_reference_minus_candidate_mean',0))>0,'cpk_build_beats_exact_ci':float(fx.get('cpk_build_ms_delta_ci95_episode_clustered',[0,0])[0])>0,
      'primary_beats_exact_mean':float(rp.get('latency_delta_reference_minus_candidate_mean_ms',0))>0,'primary_beats_exact_ci':float(rp.get('paired_latency_delta_ci95_episode_clustered_ms',[0,0])[0])>0,
      'primary_beats_full_key_mean':float(fk.get('primary_latency_ms_delta_reference_minus_candidate_mean',0))>0,'primary_beats_full_key_ci':float(fk.get('primary_latency_ms_delta_ci95_episode_clustered',[0,0])[0])>0,
      'repeat_decisions_exact':int(rp.get('decision_mismatch_count_max_over_repeats',1))==0,'repeat_expansions_exact':int(rp.get('expansion_mismatch_count_max_over_repeats',1))==0,
    }
    gates={'semantic_equivalence':all(checks[k] for k in ['pc_f1','far_zero','frr_zero','decision_exact','skeleton_exact','certificate_json_exact','expansions_exact','parametric_semantics_registered']),
           'capability_factorization_specificity':all(checks[k] for k in ['cache_hits_positive','thresholds_erased_positive','full_key_has_less_reuse','primary_beats_full_key_mean','primary_beats_full_key_ci']),
           'operational_net_gain':all(checks[k] for k in ['cpk_build_beats_exact_mean','cpk_build_beats_exact_ci','primary_beats_exact_mean','primary_beats_exact_ci','repeat_decisions_exact','repeat_expansions_exact'])}
    out={'status':'GO' if all(gates.values()) else 'STOP','gates':gates,'checks':checks,'comparisons':{'full_exact':fx,'full_full_contract_key':fk},'repeated_primary_timing':rp,
         'interpretation':{'hypothesis':'Purely numerical monotone passenger thresholds are parameters of a shared executable capability-program shape; the exact SN-CPK fixed point can be reused without changing Allow/Update/Sat, search order, or exact rejection.','next_if_go':'Run 997-episode confirmatory and then consider PCK as a C2 compiler consequence, not a fourth contribution.','next_if_stop':'Do not add another exact-kernel micro-optimizer. Freeze SN-CPK construction and shift effort to method-specific closed loop/generalization and evidence-layer learning only if an evidence audit establishes a gap.'}}
    Path(a.output).write_text(json.dumps(out,indent=2)); print(json.dumps(out,indent=2))
if __name__=='__main__': main()
