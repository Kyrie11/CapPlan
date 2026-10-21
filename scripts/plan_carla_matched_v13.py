#!/usr/bin/env python3
"""Generate, but do NOT execute, matched CARLA blocker/redirect run commands.

Reads independently audited clear-A control traces. Only calibrated runs with
complete experiment_config are eligible; real-world handoff is not asserted.
Each planned job reuses its clear control's non-intervention settings.
"""
from __future__ import annotations
import argparse,hashlib,json,shlex,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from capplan.handoff.carla_calibration_v12 import audit


def construct(clear_dir,out_dir,*,runner='scripts/run_carla_controlled_handoff_v5.py',max_controls=6,
              port=2000,host='127.0.0.1',redirect_s=8.0):
    out=Path(out_dir);out.mkdir(parents=True,exist_ok=True)
    r=audit(clear_dir);eligible=[];skipped=[]
    for trial in r.get('trials',[]):
        if not trial.get('valid_clear') or not trial.get('exact_experiment_config_present'):
            skipped.append({'trial':trial['trial'],'reason':'uncalibrated_or_config_missing'});continue
        report_path=Path(trial['report']);report=json.loads(report_path.read_text())
        c=report['experiment_config'];geom=report['geometry_parameters'];
        keys=('spawn_index','a_steps','b_steps')
        if any(geom.get(k) is None for k in keys) or any(c.get(k) is None for k in ('seed','target_speed_kmh','arrival_radius_m','max_seconds','dt','stop_speed_mps','service_stop_hold_s')):
            skipped.append({'trial':trial['trial'],'reason':'required_control_metadata_missing'});continue
        if len(eligible)<max_controls:eligible.append((trial,report))
    lines=['#!/usr/bin/env bash','set -euo pipefail',
        '# Generated paired CARLA controls. This file DOES NOT get executed by the generator.',
        '# Ensure that one CARLA world is owned exclusively by this experiment.',
        '# Only CARLA Town surrogate, NOT independently verified legal roadside handoff.']
    jobs=[]
    for trial,report in eligible:
        control=report['experiment_config'];geom=report['geometry_parameters']
        opts=['--map',str(control['map']).split('/')[-1],
            '--spawn_index',str(geom['spawn_index']),'--a_steps',str(geom['a_steps']),
            '--b_steps',str(geom['b_steps']),'--seed',str(control['seed']),
            '--host',host,'--port',str(port),
            '--target_speed_kmh',str(control['target_speed_kmh']),
            '--arrival_radius_m',str(control['arrival_radius_m']),
            '--max_seconds',str(control['max_seconds']),
            '--dt',str(control['dt']),
            '--service_stop_hold_s',str(control['service_stop_hold_s']),
            '--stop_speed_mps',str(control['stop_speed_mps'])]
        if control.get('freeze_traffic_lights'):opts.append('--freeze_traffic_lights')
        if control.get('no_rendering'):opts.append('--no_rendering')
        for mode in ('clear_A','blocked_A','blocked_A_redirect_B'):
            target=out/({'clear_A':'clear_A','blocked_A':'blocked_A','blocked_A_redirect_B':'redirect_B'}[mode])/trial['trial']
            cmd=['python',runner,'--mode',mode,'--output_dir',str(target),*opts]
            if mode=='blocked_A_redirect_B':cmd+=['--redirect_at_s',str(redirect_s)]
            lines.append(' '.join(shlex.quote(x) for x in cmd))
            if mode=='clear_A':
                # Re-run the matched control in the SAME software and CARLA
                # session. Historical calibration is only a selection filter.
                lines.append(' '.join(shlex.quote(x) for x in
                    ['python','scripts/check_single_clear_v13.py','--clear_dir',str(target)]))
            jobs.append({'trial':trial['trial'],'mode':mode,'target':str(target),
                         'matched_clear_report':str(trial['report']),
                         'clear_report_sha256':hashlib.sha256(Path(trial['report']).read_bytes()).hexdigest(),
                         'command':cmd})
    script=out/'run_matched_carla_v13.sh';script.write_text('\n'.join(lines)+'\n');script.chmod(0o750)
    summary={'controls_selected':len(eligible),'jobs_planned':len(jobs),'skipped':skipped,
             'jobs':jobs,'run_script':str(script), 'status':'PLAN_ONLY_NOT_EXECUTED',
             'this_is_real_pudo_evidence':False,'must_audit_with_existing_v12_paired_gate':True,
             'must_rerun_successful_clear_in_same_runtime':True}
    (out/'paired_plan_manifest_v13.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
    return summary


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--clear_dir',required=True)
    p.add_argument('--output_dir',required=True);p.add_argument('--max_controls',type=int,default=6)
    p.add_argument('--port',type=int,default=2000);p.add_argument('--host',default='127.0.0.1')
    p.add_argument('--redirect_at_s',type=float,default=8);a=p.parse_args()
    if a.max_controls<1 or a.redirect_at_s<0:p.error('invalid controls or redirect time')
    r=construct(a.clear_dir,a.output_dir,max_controls=a.max_controls,port=a.port,host=a.host,redirect_s=a.redirect_at_s)
    print(json.dumps({'selected':r['controls_selected'],'jobs':r['jobs_planned'],'script':r['run_script'],'executed':False}))
if __name__=='__main__':main()
