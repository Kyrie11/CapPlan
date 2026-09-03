#!/usr/bin/env python
"""Machine-readable preregistered GO/STOP assessment for V5-fast."""
from __future__ import annotations
import argparse, json
from pathlib import Path

T5_KEYS = ["DF_phase_macro_f1", "DF_resource_macro_f1", "DF_source_macro_f1", "DF_certificate_exact_match"]

def load_metrics(path):
    return json.loads((Path(path)/"metrics.json").read_text())

def load_pair(path):
    return json.loads(Path(path).read_text())

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--full', required=True)
    ap.add_argument('--v2', required=True)
    ap.add_argument('--structural', required=True)
    ap.add_argument('--generic', required=True)
    ap.add_argument('--v4', required=True)
    ap.add_argument('--v5-v2', required=True)
    ap.add_argument('--v5-structural', required=True)
    ap.add_argument('--v5-generic', required=True)
    ap.add_argument('--v5-v4', required=True)
    ap.add_argument('--output', required=True)
    a=ap.parse_args()
    full, v2, structural, generic, v4 = map(load_metrics, [a.full,a.v2,a.structural,a.generic,a.v4])
    p_v2=load_pair(a.v5_v2); p_struct=load_pair(a.v5_structural); p_generic=load_pair(a.v5_generic); p_v4=load_pair(a.v5_v4)

    checks={}
    checks['hard_pc_f1'] = float(full.get('PCDecisionF1',0)) >= 0.99
    checks['hard_far_zero'] = abs(float(full.get('PCFalseAcceptRate',1))) <= 1e-12
    checks['hard_frr_zero'] = abs(float(full.get('PCFalseRejectRate',1))) <= 1e-12
    checks['cf_flip_precision_no_regression'] = float(full.get('CF_success_flip_precision',0)) + 1e-12 >= float(v2.get('CF_success_flip_precision',0))
    checks['cf_flip_recall_no_regression'] = float(full.get('CF_success_flip_recall',0)) + 1e-12 >= float(v2.get('CF_success_flip_recall',0))
    for k in T5_KEYS:
        checks[f't5::{k}'] = float(full.get(k,0)) + 0.01 + 1e-12 >= float(v2.get(k,0))

    ci_v2=p_v2.get('paired_expansion_delta_ci95_episode_clustered') or [0,0]
    checks['primary_v5_beats_v2_mean'] = float(p_v2.get('paired_expansion_delta_reference_minus_candidate_mean',0)) > 0
    checks['primary_v5_beats_v2_ci'] = float(ci_v2[0]) > 0
    checks['primary_decisions_identical'] = int(p_v2.get('decision_mismatch_count',1)) == 0

    ci_struct=p_struct.get('paired_expansion_delta_ci95_episode_clustered') or [0,0]
    checks['typed_viability_fires'] = float(full.get('CVK_typed_pruned_mean',0) or 0) > 0
    checks['typed_viability_beats_structural_mean'] = float(p_struct.get('paired_expansion_delta_reference_minus_candidate_mean',0)) > 0
    checks['typed_viability_beats_structural_ci'] = float(ci_struct[0]) > 0

    # Proof presentation must not affect the search itself. It is promoted only
    # if concrete witnesses measurably improve diagnosis over a generic pseudo certificate.
    checks['proof_control_same_decisions'] = int(p_generic.get('decision_mismatch_count',1)) == 0
    checks['proof_control_same_expansions'] = abs(float(p_generic.get('paired_expansion_delta_reference_minus_candidate_mean',999))) <= 1e-12
    proof_gain = max(float(full.get(k,0))-float(generic.get(k,0)) for k in T5_KEYS)
    checks['proof_witness_improves_t5'] = proof_gain >= 0.01

    # V4 comparison is diagnostic rather than a hard V5 gate: it checks whether
    # V5 retains the useful pruning regime while repairing the certificate failure.
    v4_t5_repair = {k: float(full.get(k,0))-float(v4.get(k,0)) for k in T5_KEYS}
    checks['v5_v4_decisions_identical'] = int(p_v4.get('decision_mismatch_count',1)) == 0

    hard_keys=[k for k in checks if k.startswith('hard_') or k.startswith('cf_') or k.startswith('t5::') or k.startswith('primary_')]
    mechanism_keys=['typed_viability_fires','typed_viability_beats_structural_mean','typed_viability_beats_structural_ci']
    proof_keys=['proof_control_same_decisions','proof_control_same_expansions','proof_witness_improves_t5']
    hard_go=all(checks[k] for k in hard_keys)
    typed_go=all(checks[k] for k in mechanism_keys)
    proof_go=all(checks[k] for k in proof_keys)
    overall='GO' if (hard_go and typed_go and proof_go) else 'STOP'
    result={
        'status':overall,
        'hard_and_primary_gate':hard_go,
        'typed_viability_promotion_gate':typed_go,
        'proof_carrying_diagnosis_gate':proof_go,
        'checks':checks,
        'v4_t5_repair':v4_t5_repair,
        'v5_vs_v2':p_v2,
        'v5_vs_structural_only':p_struct,
        'v5_vs_generic_certificate_control':p_generic,
        'v5_vs_v4':p_v4,
        'note':'Fast subset is a mechanism gate, not a final per-axis or publication-scale claim.',
    }
    Path(a.output).write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(json.dumps(result, indent=2, ensure_ascii=False))

if __name__=='__main__': main()
