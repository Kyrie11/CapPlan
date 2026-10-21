#!/usr/bin/env python3
"""Run exact interval policy-class analysis; no CARLA or POMDP reproduction."""
from __future__ import annotations
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from capplan.handoff.temporal_recourse_v12 import analyze

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cases',required=True)
    p.add_argument('--output',required=True)
    args=p.parse_args()
    rows=[analyze(json.loads(line)) for line in Path(args.cases).read_text().splitlines() if line.strip()]
    out=Path(args.output);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows),encoding='utf-8')
    from collections import Counter
    stats=dict(Counter(r['status'] for r in rows))
    summ={'n':len(rows),'statuses':stats,'scope':'DECLARED_RESTRICTED_POLICY_CLASS_SYNTHETIC_OR_AUDITED_INPUT_ONLY',
          'does_not_prove':'new planner required or real-world incidence'}
    out.with_suffix('.summary.json').write_text(json.dumps(summ,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps(summ,ensure_ascii=False))
if __name__=='__main__':main()
