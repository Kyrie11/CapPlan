#!/usr/bin/env python3
"""CSV evidence worksheet -> v5 auditor JSONL. Only manually supplied values."""
import argparse,csv,json
from pathlib import Path

TRUE={'true':True,'false':False,'unknown':'unknown','': 'unknown'}
def convert(input_csv,out_jsonl):
    rows=[];errors=[]
    with Path(input_csv).open(newline='',encoding='utf-8') as f:
        for i,row in enumerate(csv.DictReader(f),2):
            raw=(row.get('value') or '').strip().lower()
            if raw not in TRUE:errors.append({'line':i,'error':'VALUE_MUST_BE_true_false_unknown'});continue
            x={k:row.get(k,'') for k in ('city','physical_site_key','reviewer_id','source_id','source_kind','checked_at_utc','evidence_uri','fact')}
            x.update(value=TRUE[raw],derived_from_hybrid_label=False,site_location={'x':row.get('site_x'),'y':row.get('site_y')})
            rows.append(x)
    if errors:raise ValueError(errors)
    out=Path(out_jsonl);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows))
    return {'rows':len(rows),'positive':sum(r['value'] is True for r in rows),
            'scope':'claims subject to v5 independent source audit; auto-conversion is not validation'}
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--csv',required=True);p.add_argument('--output',required=True);a=p.parse_args()
    print(json.dumps(convert(a.csv,a.output)))
