#!/usr/bin/env python
"""Compare plan/certificate exactness between two CapPlan evaluation runs."""
from __future__ import annotations
import argparse, json
from pathlib import Path


def _rows(path: Path):
    return [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines() if x.strip()]

def _map(d: str):
    p=Path(d)/'plans.jsonl'
    return {(str(r['episode_id']),str(r['passenger_id'])):r for r in _rows(p)}

def _cert_sig(c):
    if not c: return None
    return (
        str(c.get('phase')), str(c.get('transition_id')), str(c.get('resource_type')),
        float(c.get('signed_margin',0.0)), str(c.get('evidence_source')),
        float(c.get('confidence',0.0)), str(c.get('reason')),
    )

def _sk_sig(s):
    if not s: return None
    return (bool(s.get('accepted')), tuple(s.get('transitions') or ()), float(s.get('cost',0.0)))

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--reference',required=True); ap.add_argument('--candidate',required=True)
    ap.add_argument('--output',required=True); a=ap.parse_args()
    r,c=_map(a.reference),_map(a.candidate)
    keys=sorted(set(r)&set(c))
    if not keys: raise RuntimeError('no paired plan records')
    decision=sk=cert=cert_full=0; examples=[]
    for k in keys:
        x,y=r[k],c[k]
        if bool(x.get('success'))!=bool(y.get('success')): decision+=1
        if _sk_sig(x.get('skeleton'))!=_sk_sig(y.get('skeleton')): sk+=1
        if _cert_sig(x.get('certificate'))!=_cert_sig(y.get('certificate')):
            cert+=1
            if len(examples)<10: examples.append({'key':k,'reference':_cert_sig(x.get('certificate')),'candidate':_cert_sig(y.get('certificate'))})
        if (x.get('certificate') or None)!=(y.get('certificate') or None): cert_full+=1
    result={
        'paired_requests':len(keys),
        'decision_mismatch_count':decision,
        'skeleton_mismatch_count':sk,
        'certificate_signature_mismatch_count':cert,
        'certificate_full_json_mismatch_count':cert_full,
        'examples':examples,
    }
    Path(a.output).write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps(result,indent=2,ensure_ascii=False))
if __name__=='__main__': main()
