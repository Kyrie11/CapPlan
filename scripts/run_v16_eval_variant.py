#!/usr/bin/env python
"""Run one V16 Parametric Capability Kernel control."""
from __future__ import annotations
import argparse, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from capplan.evaluation.closed_loop import ClosedLoopRunner
from capplan.planning.planner import PlannerConfig

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--dataset_dir',required=True); ap.add_argument('--output_dir',required=True); ap.add_argument('--casa_checkpoint',required=True)
    ap.add_argument('--mode',choices=['parametric','full_contract_cache','exact'],default='parametric')
    ap.add_argument('--episode_limit',type=int,default=0); ap.add_argument('--episode_seed',type=int,default=13); ap.add_argument('--show_progress',action='store_true')
    a=ap.parse_args(); use_cache=a.mode!='exact'; full_key=a.mode=='full_contract_cache'
    cfg=PlannerConfig(
        algorithm_version='V16', evidence_grounded_runtime=True, casa_mode='learned', casa_checkpoint=a.casa_checkpoint,
        casa_device='auto', trajectory_mode='mock_strict', no_completion_value_guidance=True,
        no_learned_feasibility_guidance=True, no_cqhpt=True,
        use_parametric_capability_kernel=bool(use_cache),
        parametric_kernel_full_contract_key=bool(full_key),
    )
    ClosedLoopRunner(cfg).run_dataset(a.dataset_dir,a.output_dir,show_progress=a.show_progress,episode_limit=(a.episode_limit or None),episode_seed=a.episode_seed,progress_desc=Path(a.output_dir).name)
if __name__=='__main__': main()
