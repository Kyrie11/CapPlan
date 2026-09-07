#!/usr/bin/env python
"""Run one V15 exact-continuation-robustness ordering variant."""
from __future__ import annotations
import argparse, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from capplan.evaluation.closed_loop import ClosedLoopRunner
from capplan.planning.planner import PlannerConfig

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--dataset_dir',required=True); ap.add_argument('--output_dir',required=True); ap.add_argument('--casa_checkpoint',required=True)
    ap.add_argument('--mode',choices=['full','exact','count_only','legacy_static'],default='full')
    ap.add_argument('--robustness_weight',type=float,default=0.35)
    ap.add_argument('--episode_limit',type=int,default=0); ap.add_argument('--episode_seed',type=int,default=13); ap.add_argument('--show_progress',action='store_true')
    a=ap.parse_args(); legacy=a.mode=='legacy_static'; exact=a.mode=='exact'; count=a.mode=='count_only'
    cfg=PlannerConfig(
        algorithm_version='V15R', evidence_grounded_runtime=True, casa_mode='learned', casa_checkpoint=a.casa_checkpoint,
        casa_device='auto', trajectory_mode='mock_strict', no_completion_value_guidance=True,
        exact_robustness_weight=float(a.robustness_weight),
        no_exact_robustness_ordering=bool(exact or legacy), exact_robustness_count_only=bool(count),
        v15_legacy_static_guidance=bool(legacy), no_learned_feasibility_guidance=not bool(legacy),
        no_cqhpt=True,
    )
    ClosedLoopRunner(cfg).run_dataset(a.dataset_dir,a.output_dir,show_progress=a.show_progress,episode_limit=(a.episode_limit or None),episode_seed=a.episode_seed,progress_desc=Path(a.output_dir).name)
if __name__=='__main__': main()
