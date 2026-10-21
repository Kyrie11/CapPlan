#!/usr/bin/env python3
"""Hard-gate CARLA intervention runs against a fresh successful clear control."""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from capplan.handoff.carla_calibration_v12 import audit

def main():
    p=argparse.ArgumentParser();p.add_argument('--clear_dir',required=True);a=p.parse_args()
    r=audit(Path(a.clear_dir));rows=r.get('trials',[])
    passed=(len(rows)==1 and rows[0].get('valid_clear') is True and
            rows[0].get('exact_experiment_config_present') is True)
    print(json.dumps({'status':'PASS_FRESH_CLEAR_SURROGATE' if passed else 'STOP_NO_MATCHED_CLEAR',
                      'trial_count':len(rows),'scope':'CARLA_TOWN_SURROGATE_ONLY'}))
    if not passed:raise SystemExit(2)
if __name__=='__main__':main()
