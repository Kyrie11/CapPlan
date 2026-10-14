#!/usr/bin/env python3
"""External physical-site evidence quality control; DOES NOT create field measurements.

Each JSONL row is an independently sourced claim and must refer to the exact
site key. Unknown/ambiguous provenance fails CLOSED. Reviewer declarations
are not independent confirmation until photographs/records are inspected.
"""
from __future__ import annotations
import argparse, hashlib, json, math
from collections import Counter, defaultdict
from pathlib import Path

REQUIRED=('sidewalk_to_curb_continuity','crossing_access','legal_stopping','deployment_region','stopping_pose')
ACCEPTED=('field_measurement','official_gis','municipal_record','time_stamped_site_imagery','independent_audit')

def read_rows(path):
    for n,line in enumerate(Path(path).read_text().splitlines(),1):
        if not line.strip():continue
        try:yield n,json.loads(line)
        except ValueError:yield n,{'_decode_error':True}

def validate_row(row):
    errs=[]
    for key in ('city','physical_site_key','reviewer_id','source_id','source_kind','checked_at_utc','evidence_uri','fact','value'):
        if row.get(key) in ('',None):errs.append('MISSING_'+key.upper())
    if row.get('source_kind') not in ACCEPTED:errs.append('NONINDEPENDENT_OR_UNKNOWN_SOURCE_KIND')
    if row.get('fact') not in REQUIRED:errs.append('UNSUPPORTED_FACT')
    if row.get('value') not in (True,False,'unknown'):errs.append('INVALID_TRISTATE')
    if not isinstance(row.get('evidence_uri'),str) or not (row['evidence_uri'].startswith(('https://','http://','file://'))):errs.append('INVALID_SOURCE_URI')
    # Physical checks must NOT be inferred from simulated hybrid metadata.
    if row.get('derived_from_hybrid_label') is True:errs.append('SELF_REFERENTIAL_HYBRID_TRUTH')
    coords=row.get('site_location')
    if coords is not None:
        try:
            if not all(math.isfinite(float(coords[k])) for k in ('x','y')):errs.append('NONFINITE_LOCATION')
        except (KeyError,ValueError,TypeError):errs.append('INVALID_LOCATION')
    return errs

def audit(rows):
    failures=[];group=defaultdict(dict);counts=Counter();bad=set()
    for number,row in rows:
        counts['submitted']+=1;err=validate_row(row)
        key=(row.get('city'),row.get('physical_site_key'))
        if err:failures.append({'line':number,'site':key,'reasons':err});counts['quarantined']+=1;bad.add(key);continue
        fact=row['fact'];prev=group[key].get(fact)
        if prev is not None and prev['value']!=row['value']:
            failures.append({'line':number,'site':key,'reasons':['CONTRADICTORY_SOURCE_CLAIMS']});bad.add(key)
        group[key][fact]=row;counts['source_claims_with_valid_metadata']+=1
    eligible=[]; incomplete=[]
    for key,facts in group.items():
        complete=all(k in facts and facts[k]['value'] is True for k in REQUIRED)
        if complete and key not in bad:eligible.append({'city':key[0],'site':key[1],'status':'ALL_SOURCE_CLAIMS_POSITIVE_REQUIRES_INDEPENDENT_INSPECTION','sources':[facts[k]['source_id'] for k in REQUIRED]})
        else:incomplete.append({'city':key[0],'site':key[1],'status':'UNKNOWN_OR_NEGATIVE_OR_CONFLICT','missing_facts':[k for k in REQUIRED if k not in facts],'negative_facts':[k for k in REQUIRED if k in facts and facts[k]['value'] is False]})
    return {'counts':dict(counts),'site_keys':len(group),'metadata_complete_positive_sites':len(eligible),'independently_confirmed_sites':0,'eligible_for_primary_source_manual_review':eligible,'unverified_or_negative':incomplete,'quarantine':failures,'warning':'URI + reviewer declaration is not validation of actual curb, compliance, time validity or occupant functional feasibility'}

def main():
    p=argparse.ArgumentParser();p.add_argument('--claims',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();d=audit(read_rows(a.claims));t=Path(a.output);t.parent.mkdir(parents=True,exist_ok=True);t.write_text(json.dumps(d,indent=2,ensure_ascii=False)+'\n');print(json.dumps(d['counts']))
if __name__=='__main__':main()
