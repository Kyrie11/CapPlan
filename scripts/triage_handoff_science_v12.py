#!/usr/bin/env python3
"""Generate a fail-closed H1--H4 evidence decision from a SINGLE V11 result dir.

Never infer study completion from filenames. No physical field is filled and no
scientific hypothesis automatically receives a 'CCF-A novel' label.
"""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from capplan.handoff.carla_calibration_v12 import audit

def load(root,name):
    p=root/name
    return json.loads(p.read_text()) if p.is_file() else None

def triage(root):
    root=Path(root)
    a=load(root,'alternatives_directed_optimistic.summary.json')
    b=load(root,'alternatives_directed_strict.summary.json')
    h4=load(root,'h4_protocol_robustness.summary.json')
    physical=load(root,'physical_pending.summary.json')
    time=load(root,'physical_pending_timing.summary.json')
    clear=audit(root/'carla_clear_A')
    missing=[label for label,val in [('alternatives_optimistic',a),('alternatives_strict',b),('h4_protocol',h4),
                                     ('physical_queue',physical),('physical_timing',time)] if val is None]
    h4robust=False
    if h4:
        by={(r['max_wait_ticks'],r['allow_parallel_control_actions']):r for r in h4.get('protocols',[])}
        original=by.get((0,False));joint=by.get((0,True));wait=by.get((1,False))
        if original and joint and wait and original.get('fully_explored') and joint.get('fully_explored') and wait.get('fully_explored'):
            h4robust=(original['three_way_binary_contrast']!=0 and joint['three_way_binary_contrast']!=0 and wait['three_way_binary_contrast']!=0)
    result={'source_root':str(root),'missing_required_input_reports':missing,
            'conservative_interpretation':True,'H1':{'state':'NOT_PHYSICALLY_ESTABLISHED',
                    'unique_verified_physical_sites':0 if physical is None else physical.get('real_temporal_packets_verified',0),
                    'distinct_episodes_in_queue':None if physical is None else physical.get('episode_count'),
                    'current_limitation':'No independent legal curb connector + vehicle stop pose + handoff window witness'},
            'H2':{'state':'GEOMETRIC_TIMING_SENSITIVITY_ONLY',
                    'strict_graph_eligible_rows':None if b is None else b['counts'].get('B_graph_feasible',0)+b['counts'].get('third_option_graph_feasible',0),
                    'optimistic_third_option_rows':None if a is None else a['counts'].get('third_option_graph_feasible'),
                    'directed_graph_semantics_independently_validated':bool(a and a.get('directed_edge_encoding_verified')),
                    'next':'Time-indexed execution trace, physical curb audit, third options and accurate passenger velocity'},
            'H3':{'state':'NO_PUBLISHED_STRONG_BASELINE_REPRODUCTION',
                    'strong_shield_direct_comparison_done':False,
                    'next':'Same state/action/observation/motion oracle for exact, centralized/factored and structured search'},
            'H4':{'state':'MODEL_ASSUMPTION_SENSITIVE' if h4 and not h4robust else 'UNKNOWN_OR_NEEDS_INDEPENDENT_REPLICATION',
                    'robust_to_parallel_and_wait':h4robust,
                    'three_way_original_binary_contrast':None if h4 is None else next((r.get('three_way_binary_contrast') for r in h4['protocols'] if r['max_wait_ticks']==0 and not r['allow_parallel_control_actions']),None),
                    'next':'Use measured/validated vehicle stop and response deadlines; do not suppress reasonable control actions'},
            'CARLA':{'gate':clear['gate'],
                     'successes':clear.get('clear_stop_surrogate_success',0),
                     'trials':clear.get('clear_trials',0),
                     'is_real_pudo_evidence':False},
            'recommended_gate_order':['G1_independent_curb_evidence','G2_paired_vehicle_controls',
                                      'G3_time_and_progress_replay','G4_compiled_full_contract',
                                      'G5_published_shields_equal_information']}
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--v11_dir',required=True)
    p.add_argument('--output',required=True)
    a=p.parse_args();d=triage(a.v11_dir)
    out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'H1':d['H1']['state'],'H2':d['H2']['state'],
                      'H3':d['H3']['state'],'H4':d['H4']['state'],
                      'CARLA':d['CARLA'],'missing':d['missing_required_input_reports']},ensure_ascii=False))
if __name__=='__main__':main()
