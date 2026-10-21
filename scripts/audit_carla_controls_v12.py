#!/usr/bin/env python3
"""Audit CARLA calibration from recorded trajectories, fail-closed."""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from capplan.handoff.carla_calibration_v12 import audit

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--clear_dir',required=True)
    p.add_argument('--blocked_dir')
    p.add_argument('--redirect_dir')
    p.add_argument('--output',required=True)
    args=p.parse_args()
    result=audit(args.clear_dir,args.blocked_dir,args.redirect_dir)
    out=Path(args.output);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ('gate','clear_trials','clear_stop_surrogate_success','blocked_comparison_gate') if k in result},ensure_ascii=False))
if __name__=='__main__':main()
