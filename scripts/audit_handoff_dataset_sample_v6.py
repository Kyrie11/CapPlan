#!/usr/bin/env python3
"""Inspect uploaded dataset ZIP/dir type and PUDO semantics, never claim site truth."""
import argparse,zipfile,json,collections
from pathlib import Path

def audit(p,max_anchors=500):
    p=Path(p)
    if p.suffix.lower()=='.zip':
        z=zipfile.ZipFile(p)
        def lines(n):
            with z.open(n) as f:
                for row in f:
                    if row.strip():yield json.loads(row)
        def get(n):return json.loads(z.read(n))
    else:
        def lines(n):
            with (p/n).open() as f:
                for row in f:
                    if row.strip():yield json.loads(row)
        def get(n):return json.loads((p/n).read_text())
    manifest=get('dataset_manifest.json');validation=get('validation_report.json')
    items=[]
    for v in lines('pudo_anchors.jsonl'):
        items.append(v)
        if len(items)>=max_anchors:break
    source=collections.Counter(v.get('source') or 'missing' for v in items)
    legal=collections.Counter(str(v.get('legal_stop')) for v in items)
    conn=collections.Counter('NO_EXPLICIT_CURB_CONNECTOR_CLAIM' if not v.get('roadblock_id') else 'ROADBLOCK_ID_PRESENT_NOT_PHYSICAL_CURB' for v in items)
    name=manifest.get('dataset_name');hybrid=('hybrid' in str(name).lower())
    return {'dataset_name':name,'appears_hybrid':hybrid,'validation_ok':validation.get('ok'),
            'num_episodes':manifest.get('num_episodes'),'num_contracts':manifest.get('num_contracts'),
            'sample_anchors':len(items),'anchor_source_distribution':dict(source),
            'legal_stop_labels':dict(legal),'roadblock_or_interface_binding':dict(conn),
            'num_validation_warnings':len(validation.get('warnings',[])),
            'usable_as_handoff_hybrid_test_truth':False,
            'reason':'hybrid' if hybrid else 'NON_HYBRID_BOOTSTRAP_OR_BASE_DATASET_NOT_CURRENT_FUNNEL_CORPUS',
            'scientific_limit':'manifest semantic validation is not independent pedestrian-curb/vehicle-motion field validation'}

def main():
    p=argparse.ArgumentParser();p.add_argument('--dataset',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();d=audit(a.dataset);out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(d,indent=2,ensure_ascii=False)+'\n');print(json.dumps({k:d[k] for k in ('dataset_name','appears_hybrid','sample_anchors','validation_ok')}))
if __name__=='__main__':main()
