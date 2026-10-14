#!/usr/bin/env python3
"""Create a human-fillable field-review worksheet (NOT evidence)."""
import argparse,csv,json
from pathlib import Path

FACTS=('sidewalk_to_curb_continuity','crossing_access','legal_stopping','deployment_region','stopping_pose')
FIELDS=('city','physical_site_key','site_id','fact','value','reviewer_id','source_id','source_kind',
        'checked_at_utc','evidence_uri','description','site_x','site_y','derived_from_hybrid_label')

def create(sites,out_csv,out_jsonl):
    seen=set();rows=[]
    for line in Path(sites).open():
        if not line.strip():continue
        item=json.loads(line);site_key=item.get('site_key')
        if not site_key:continue
        for fact in FACTS:
            key=(item['city'],site_key,fact)
            if key in seen:continue
            seen.add(key)
            rows.append({'city':item['city'],'physical_site_key':site_key,'site_id':item.get('site_id'),
                'fact':fact,'value':'unknown','reviewer_id':'','source_id':'',
                'source_kind':'','checked_at_utc':'','evidence_uri':'','description':'',
                'site_x':item['pose']['x'],'site_y':item['pose']['y'],'derived_from_hybrid_label':False})
    if not rows:raise ValueError('no site keys: please provide selected_log_sites.jsonl')
    csv_path=Path(out_csv);csv_path.parent.mkdir(parents=True,exist_ok=True)
    with csv_path.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=FIELDS);w.writeheader();w.writerows(rows)
    # Pending rows deliberately remain unverified and will fail closed in v5 audit.
    js=Path(out_jsonl);js.parent.mkdir(parents=True,exist_ok=True)
    with js.open('w') as f:
        for r in rows:
            x={k:r[k] for k in ('city','physical_site_key','fact','value','reviewer_id','source_id','source_kind','checked_at_utc','evidence_uri','derived_from_hybrid_label')}
            x['site_location']={'x':r['site_x'],'y':r['site_y']}
            f.write(json.dumps(x,ensure_ascii=False)+'\n')
    return {'unique_sites':len(set((r['city'],r['physical_site_key']) for r in rows)),'fact_rows':len(rows),
        'confirmed_physical_sites':0,'status':'UNREVIEWED_TEMPLATE_NOT_INDEPENDENT_EVIDENCE'}

def main():
    p=argparse.ArgumentParser();p.add_argument('--sites',required=True);p.add_argument('--out_csv',required=True);p.add_argument('--out_jsonl',required=True)
    a=p.parse_args();print(json.dumps(create(a.sites,a.out_csv,a.out_jsonl)))
if __name__=='__main__':main()
