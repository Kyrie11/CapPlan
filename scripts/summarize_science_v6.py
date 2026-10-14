#!/usr/bin/env python3
"""Evidence-based hypothesis tracker; no synthetic acceptance becomes proof."""
import argparse,json
from pathlib import Path

def read(p):
    if not p or not Path(p).is_file():return None
    return json.loads(Path(p).read_text())

def summarize(carla=None,logs=None,review=None,graph=None,ds=None):
    control=carla or {};g=graph or {};motion=logs or {};r=review or {}
    return {
      'H1_physical_handoff_joint_feasibility':{
        'state':'UNVERIFIED',
        'basis':'No independently verified A/B pedestrian curb + stop pose + boarding constraints across matched real sites',
        'independent_physical_confirmations':r.get('independently_confirmed_sites',0),
        'not_disproved_by_missing_gis':True},
      'H2_progress_timed_recourse':{
        'state':'OPTIMISTIC_GRAPH_MECHANISM_ONLY' if g.get('grid_rows',0)>0 else 'AWAIT_GRAPH_STRESS',
        'graph_grid_rows':g.get('grid_rows',0),
        'limits':['selected risk-conditioned sites','symmetric pedestrian edges','nonempirical speed profiles',
                  'no full action space','no real intersection or vehicle meeting deadline']},
      'H3_evidence_timed_joint_decision':{
        'state':'NOT_SHOWN_NECESSARY',
        'observed_carla_redirect_goal_zone':control.get('redirect_B_goal_zone_observed',False),
        'causal_blockage_control_valid':control.get('blocked_A_causal_effect_established',False),
        'strong_joint_baseline_reproduced':False,
        'reason':'No matched physical-pedestrian-vehicle policy evaluation; fixed B matched finite oracle previously.'},
      'next_branch':('REPAIR_CARLA_CLEAR_CONTROL_AND_COLLECT_AUDITED_PUDO' if not control.get('clear_A_control_valid')
           else 'AUDIT_PHYSICAL_PUDO_AND_BIND_NUPLAN_LOGS'),
      'field_evidence_status':'PENDING_REVIEW' if not r else 'METADATA_CLAIMS_NOT_VERIFIED',
      'nuplan_log_binding_status':'NOT_EXECUTED' if not motion else 'LOG_CONTEXT_ONLY',
      'dataset_type':(ds or {}).get('dataset_name','NOT_PROVIDED'),
      'CCF_A_novelty_conclusion':'OPEN_HYPOTHESIS_NOT_PROVEN',
      'disallowed_claims':['measured natural event prevalence','true passenger stranded frequency','CARLA proof of city curb legality',
                           'generic POMDP/shield theoretically unable to solve handoff']}

def main():
    p=argparse.ArgumentParser()
    for k in ('carla','logs','review','graph','dataset'):p.add_argument('--'+k)
    p.add_argument('--output',required=True);a=p.parse_args();d=summarize(*[read(getattr(a,k)) for k in ('carla','logs','review','graph','dataset')])
    f=Path(a.output);f.parent.mkdir(parents=True,exist_ok=True);f.write_text(json.dumps(d,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps({'next_branch':d['next_branch'],'H1':d['H1_physical_handoff_joint_feasibility']['state'],
                      'H2':d['H2_progress_timed_recourse']['state'],'H3':d['H3_evidence_timed_joint_decision']['state']}))
if __name__=='__main__':main()
