#!/usr/bin/env python3
"""Screen independently supplied local handoff timing/geometry packets.

Never backfill site coordinates, legal-stop records or curb connectors. A
local temporal witness is neither a non-anticipatory robust handoff policy nor
physical boarding evidence. Do not estimate natural failure rates on mined
samples.
"""
from __future__ import annotations
import argparse,json,sys
from collections import Counter
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from capplan.handoff.window_intersection_v11 import check_option


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--packets',required=True)
    p.add_argument('--output',required=True)
    a=p.parse_args()
    rows=[json.loads(x) for x in Path(a.packets).read_text(encoding='utf-8').splitlines() if x.strip()]
    seen=set()
    out=[]
    for r in rows:
        if not r.get('option_id') or r['option_id'] in seen:
            raise ValueError('nonempty, unique option_id required; do not treat duplicated temporal frames as independent sites')
        seen.add(r['option_id'])
        result=check_option(r);result['site_key']=r.get('site_key')
        result['input_evidence_class']=r.get('input_evidence_class','UNKNOWN_UNVERIFIED')
        out.append(result)
    dest=Path(a.output);dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(''.join(json.dumps(x,ensure_ascii=False)+'\n' for x in out),encoding='utf-8')
    summary={'options':len(out),'statuses':dict(Counter(x['status'] for x in out)),
             'at_least_two_disconnected_windows':sum(x.get('disconnected_windows',0)>=2 for x in out),
             'physical_handoffs_independently_proven_by_this_script':0,
             'notes':['Not a generic POMDP shielding competitor',
                      'Time overlap is necessary not sufficient for safe boarding',
                      'Input fields cannot be assumed observed: distinguish synthetic versus independently audited',
                      'No incidence estimation on mined or convenience-selected site sample']}
    dest.with_suffix('.summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False))
if __name__=='__main__':main()
