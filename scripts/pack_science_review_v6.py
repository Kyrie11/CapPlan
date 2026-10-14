#!/usr/bin/env python3
"""Small reproducible evidence pack: no raw nuPlan DB or giant graph arrays."""
import argparse,hashlib,json,zipfile
from pathlib import Path
FILES=(
 'site_sampling.summary.json','selected_log_sites.jsonl','physical_review_template.csv',
 'physical_review_quality.json','nuplan_log_bindings.json','dataset_audit.json',
 'carla_control_audit.json','carla_calibration/calibration_summary.json',
 'gate3_profile_time_sweep.summary.json','hypothesis_tracker.json')
def pack(study,out):
    root=Path(study);output=Path(out);output.parent.mkdir(parents=True,exist_ok=True)
    fs=[(name,root/name) for name in FILES if (root/name).is_file()]
    manifest={'files':{},'research_scope':'site/motion provenance summaries; not primary physical truth'}
    with zipfile.ZipFile(output,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for name,p in fs:
            z.write(p,name);manifest['files'][name]=hashlib.sha256(p.read_bytes()).hexdigest()
        z.writestr('MANIFEST.json',json.dumps(manifest,indent=2))
    return {'archive':str(output),'files':len(fs)}
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--study',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();print(json.dumps(pack(a.study,a.output)))
