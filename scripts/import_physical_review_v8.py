#!/usr/bin/env python3
"""Convert genuinely reviewed CSV claims to V5 auditing JSONL, fail closed.

Unknown/unreviewed worksheet rows are intentionally omitted. The exporter does
not adjudicate the truth of a URL/record/photo; V5 and manual auditing follow.
"""
from __future__ import annotations
import argparse,csv,json,math
from pathlib import Path

TRUTH={'true':True,'false':False,'unknown':'unknown'}
SOURCE_KINDS={'field_measurement','official_gis','municipal_record','time_stamped_site_imagery','independent_audit'}

def convert(path,output):
    verified=[];unknown=0;errors=[]
    with Path(path).open(newline='',encoding='utf-8') as f:
        for idx,row in enumerate(csv.DictReader(f),2):
            raw=str(row.get('value','')).strip().lower()
            if raw not in TRUTH:
                errors.append({'line':idx,'problem':'TRUTH_MUST_BE_TRUE_FALSE_UNKNOWN'});continue
            if raw=='unknown':unknown+=1;continue
            mandatory=('city','physical_site_key','fact','reviewer_id','source_id','source_kind','checked_at_utc','evidence_uri')
            absent=[key for key in mandatory if not str(row.get(key,'')).strip()]
            if absent:
                errors.append({'line':idx,'problem':'MISSING_INDEPENDENT_REVIEW_METADATA','keys':absent});continue
            if row['source_kind'] not in SOURCE_KINDS or not row['evidence_uri'].startswith(('http://','https://','file://')):
                errors.append({'line':idx,'problem':'INVALID_SOURCE_KIND_OR_EVIDENCE_URI'});continue
            if str(row.get('derived_from_hybrid_label','')).strip().lower()=='true':
                errors.append({'line':idx,'problem':'SELF_REFERENTIAL_HYBRID_FIELD'});continue
            claim={key:row[key] for key in mandatory};claim['value']=TRUTH[raw];claim['derived_from_hybrid_label']=False
            try:
                if row.get('site_x') and row.get('site_y'):
                    x=float(row['site_x']);y=float(row['site_y'])
                    if not math.isfinite(x) or not math.isfinite(y):raise ValueError
                    claim['site_location']={'x':x,'y':y}
            except (TypeError,ValueError):
                errors.append({'line':idx,'problem':'INVALID_COORDINATES'});continue
            verified.append(claim)
    if errors:
        return {'status':'INVALID_INPUT_NOT_WRITTEN','errors':errors,'unknown_rows':unknown,'reviewed_rows':0}
    out=Path(output);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in verified))
    return {'status':'EXPORTED_UNVERIFIED_SOURCE_CLAIMS','reviewed_rows':len(verified),'unknown_rows':unknown,
            'manual_independent_evidence_check_still_required':True}


def main():
    p=argparse.ArgumentParser();p.add_argument('--review_csv',required=True);p.add_argument('--output_jsonl',required=True)
    a=p.parse_args();r=convert(a.review_csv,a.output_jsonl);print(json.dumps(r,ensure_ascii=False));
    if r['status'].startswith('INVALID'):raise SystemExit(2)
if __name__=='__main__':main()
