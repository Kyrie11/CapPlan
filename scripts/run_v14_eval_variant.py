#!/usr/bin/env python
"""Run one exact-authority V14 learned-guidance variant."""
from __future__ import annotations
import argparse, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from capplan.evaluation.closed_loop import ClosedLoopRunner
from capplan.planning.planner import PlannerConfig


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--dataset_dir',required=True); ap.add_argument('--output_dir',required=True); ap.add_argument('--casa_checkpoint',required=True)
    ap.add_argument('--cqhpt_checkpoint',default=None); ap.add_argument('--cqhpt_device',default='auto'); ap.add_argument('--cqhpt_weight',type=float,default=0.35)
    ap.add_argument('--legacy_static_guidance',action='store_true'); ap.add_argument('--episode_limit',type=int,default=0); ap.add_argument('--episode_seed',type=int,default=13)
    ap.add_argument('--show_progress',action='store_true'); args=ap.parse_args()
    cfg=PlannerConfig(
        algorithm_version='V14', evidence_grounded_runtime=True, casa_mode='learned', casa_checkpoint=args.casa_checkpoint,
        casa_device='auto', trajectory_mode='mock_strict', no_completion_value_guidance=True,
        cqhpt_checkpoint=args.cqhpt_checkpoint, cqhpt_device=args.cqhpt_device, cqhpt_weight=args.cqhpt_weight,
        no_cqhpt=not bool(args.cqhpt_checkpoint), v14_legacy_static_guidance=bool(args.legacy_static_guidance),
        no_learned_feasibility_guidance=not bool(args.legacy_static_guidance),
    )
    runner=ClosedLoopRunner(cfg)
    runner.run_dataset(args.dataset_dir,args.output_dir,show_progress=args.show_progress,episode_limit=(args.episode_limit or None),episode_seed=args.episode_seed,progress_desc=Path(args.output_dir).name)

if __name__=='__main__': main()
