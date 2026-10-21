#!/usr/bin/env python3
"""Read *both* V11 and V12 result bundles and create an evidence-lineage gate.

A missing prior-run input is NOT negative science evidence. A report that was
merely generated is not proof of a real independently verified handoff.
Accepts ZIPs or extracted directories. No writing into the source bundles.
"""
from __future__ import annotations
import argparse,hashlib,json,zipfile
from pathlib import Path

class Bundle:
    def __init__(self,path):
        self.path=Path(path)
        self.z=zipfile.ZipFile(self.path) if self.path.is_file() and zipfile.is_zipfile(self.path) else None
        if self.z:
            names=self.z.namelist()
            if any(n.startswith('/') or '..' in Path(n).parts for n in names):
                raise ValueError('unsafe ZIP paths')
        elif not self.path.is_dir():raise FileNotFoundError(self.path)
    def json(self,relative):
        if self.z:
            matches=[n for n in self.z.namelist() if n.endswith('/'+relative) or n==relative]
            # An ambiguous basename could mix multiple runs. Fail closed.
            if len(matches)>1:raise ValueError(f'ambiguous {relative}: {matches}')
            return json.loads(self.z.read(matches[0])) if matches else None
        matches=list(self.path.rglob(relative))
        if len(matches)>1:raise ValueError(f'ambiguous {relative}: {matches}')
        return json.loads(matches[0].read_text()) if matches else None
    def fingerprint(self):
        if self.z:
            size=self.path.stat().st_size
            return {'path':str(self.path),'bytes':size,'sha256':hashlib.sha256(self.path.read_bytes()).hexdigest()}
        return {'path':str(self.path),'sha256':None,'source_is_directory':True}
    def close(self):
        if self.z:self.z.close()

def build(v11,v12):
    p={'v11':v11.fingerprint(),'v12':v12.fingerprint()};missing=[]
    def load(bundle,k,file):
        d=bundle.json(file)
        if d is None:missing.append({'source':k,'required_file':file,'kind':'EVIDENCE_NOT_SUPPLIED'})
        return d
    h4=load(v11,'V11','h4_protocol_robustness.summary.json')
    opt=load(v11,'V11','alternatives_directed_optimistic.summary.json')
    strict=load(v11,'V11','alternatives_directed_strict.summary.json')
    physical=load(v11,'V11','physical_pending.summary.json')
    temporal=load(v11,'V11','physical_pending_timing.summary.json')
    vb=load(v12,'V12','temporal_benchmark_v12.json')
    tc=load(v12,'V12','temporal_cases_v12.summary.json')
    clear=load(v12,'V12','carla_v12/clear_control_gate.json')
    paired=load(v12,'V12','carla_v12/paired_gate.json')
    controls=load(v12,'V12','unscreened_controls.manifest.json')
    old=load(v12,'V12','triage_v12.json')
    # Distinguish a previous script's incorrectly pointed input directory
    # from the evidence actually contained in the supplied earlier bundle.
    previous_missing=old.get('missing_required_input_reports') if isinstance(old,dict) else None
    protocols={}
    if h4:
        for z in h4.get('protocols',[]):
            key=f"parallel={bool(z['allow_parallel_control_actions'])},wait={int(z['max_wait_ticks'])}"
            protocols[key]={'wins':z.get('winning_cells'),'out_of':z.get('cells'),
                             'contrast':z.get('three_way_binary_contrast'),'complete':z.get('fully_explored')}
    original=protocols.get('parallel=False,wait=0');parallel=protocols.get('parallel=True,wait=0')
    wait=protocols.get('parallel=False,wait=1')
    h4vanishes=bool(original and parallel and wait and original['wins']<original['out_of']
                   and parallel['wins']==parallel['out_of'] and wait['wins']==wait['out_of'])
    physical_n=None if physical is None else physical.get('real_temporal_packets_verified')
    shared={'research_level':'ONLY_CONTROLLED_AND_GRAPH_FINDINGS_UNLESS_INDEPENDENTLY_ATTESTED',
            'natural_incidence_estimated':False,'direct_published_shield_comparison':False}
    return {'schema':'capplan_evidence_lineage_v13','sources':p,
      'prior_triage_missing_due_to_cross_run_input':previous_missing,
      'input_availability':{'not_supplied':missing,'all_required_bundles_present':not missing},
      'H1':{'state':'PHYSICAL_ATTESTATION_PENDING' if physical_n==0 else 'NOT_EVALUABLE' if physical_n is None else 'PHYSICAL_ATTESTATION_REQUIRES_CHECK',
             'independently_verified_packets':physical_n,
             'independent_episode_count_in_queue':physical.get('episode_count') if physical else None,
             'unique_physical_site_keys':physical.get('unique_known_physical_site_keys') if physical else None,
             'time_window_packet_statuses':temporal.get('statuses') if temporal else None,
             'CARLA_clear_trials':clear.get('clear_trials') if clear else None,
             'CARLA_clear_stop_surrogate':clear.get('clear_stop_surrogate_success') if clear else None,
             'CARLA_paired_comparison_gate':paired.get('blocked_comparison_gate') if paired else None,
             **shared},
      'H2':{'state':'DIRECTIONAL_GRAPH_AND_PROGRESS_REQUIRES_PHYSICAL_VALIDATION',
            'optimistic_third_option_rows':opt.get('counts',{}).get('third_option_graph_feasible') if opt else None,
            'strict_graph_eligible_rows':strict.get('counts',{}).get('B_graph_feasible',0)+strict.get('counts',{}).get('third_option_graph_feasible',0) if strict else None,
            'graph_edge_direction_independently_verified':bool(opt and opt.get('directed_edge_encoding_verified')),
            'cannot_claim_global_irrecoverability':True},
      'H3':{'state':'NO_FAITHFUL_PUBLISHED_SHIELD_REPRODUCTION','full_information_baseline_required':True},
      'H4':{'state':'ORIGINAL_INTERACTION_NOT_ROBUST_TO_REASONABLE_ACTIONS' if h4vanishes else 'INSUFFICIENT_EVIDENCE',
            'protocols':protocols,'original_counterexample_disappears':h4vanishes,
            'must_retest_with_parallel_actions':True},
      'H5':{'state':'RESTRICTED_INTERVAL_GRID_CONSISTENCY_ONLY',
            'V12_integer_grid_disagreements':None if not vb else sum(x['mismatches'] for x in vb.get('horizons',[])),
            'V12_random_cases':None if not vb else sum(x['cases'] for x in vb.get('horizons',[])),
            'V12_verified':vb.get('all_verified') if vb else None,
            'synthetic_case_count':tc.get('n') if tc else None,
            'full_pomdp_oracle_implemented':False,
            'time_window_projection_theorem_proven':False},
      'sampling':{'blind':controls.get('screening_blind') if controls else None,
                  'selected_by_city':controls.get('selected_by_city') if controls else None,
                  'is_event_incidence_sample':False},
      'next_gates':['H1 independent typed curb + stop pose + boarded dwell on independent sites',
                    'H2 directed mid-edge progress + actionable ACK + third PUDO + real motion',
                    'H4 matched CARLA clear/blocked/redirect + realistic parallel controls',
                    'H5 one-observation grid exactness => multistep policy exactness => fair published shield']}

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--v11',required=True);p.add_argument('--v12',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();b=Bundle(a.v11);c=Bundle(a.v12)
    try:r=build(b,c)
    finally:b.close();c.close()
    out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print(json.dumps({k:r[k]['state'] for k in ('H1','H2','H3','H4','H5')},ensure_ascii=False))
if __name__=='__main__':main()
