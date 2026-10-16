#!/usr/bin/env python3
"""Official third-party source snapshots for V9. Download source facts, NOT adjudicated PUDOs.

LTA: registered DataMall AccountKey required, never logged or saved.
WPRDC: CKAN package metadata discovers real resource URLs; no hardcoded resource IDs.
Every saved snapshot has a SHA256 and source timestamp, not an inferred 2021 state.
"""
from __future__ import annotations
import argparse,datetime,hashlib,json,os,urllib.request,urllib.parse,sys
from pathlib import Path

def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def get(url,headers=None,max_bytes=40_000_000):
 req=urllib.request.Request(url,headers={'User-Agent':'CapPlan-V9 academic research source snapshot',**(headers or {})})
 with urllib.request.urlopen(req,timeout=45) as r:
  raw=r.read(max_bytes+1)
 if len(raw)>max_bytes:raise RuntimeError('SOURCE_TOO_LARGE_NO_PARTIAL_WRITE')
 return raw

def save(out,raw,info):
 p=Path(out);p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
 h=hashlib.sha256(raw).hexdigest();info={'file':str(p),'sha256':h,'retrieved_at_utc':now(),
  'semantic_scope':'third-party documented data snapshot, NOT independent field review',**info}
 p.with_suffix(p.suffix+'.provenance.json').write_text(json.dumps(info,indent=2,ensure_ascii=False)+'\n')
 return info

def lta(output,key_env='LTA_ACCOUNT_KEY',max_pages=20):
 key=os.environ.get(key_env)
 if not key:raise RuntimeError('LTA AccountKey missing in environment '+key_env+'; register at DataMall')
 base='https://datamall2.mytransport.sg/ltaodataservice/TaxiStands'
 results=[];seen=set()
 for page in range(max_pages):
  url=base+'?'+urllib.parse.urlencode({'$skip':page*500})
  raw=get(url,{'AccountKey':key,'accept':'application/json'},max_bytes=5_000_000)
  payload=json.loads(raw)
  rows=payload.get('value') or payload.get('Value') or []
  if not isinstance(rows,list):raise RuntimeError('unexpected LTA response schema')
  if rows:
   fingerprint=hashlib.sha256(json.dumps(rows[:3],sort_keys=True).encode()).hexdigest()
   if fingerprint in seen:raise RuntimeError('LTA response repeated page, refusing silent truncation')
   seen.add(fingerprint);results.extend(rows)
  if len(rows)<500:break
 else:raise RuntimeError('LTA max_pages exceeded; refusing partial source download')
 raw=json.dumps({'value':results},ensure_ascii=False).encode()
 return save(output,raw,{'provider':'Singapore LTA','source_url':base,'row_count':len(results),
 'key_omitted':True,'scope':'Taxi facilities including reported Bfa and Type; no authorization inference for autonomous vehicles'})

def wprdc(package,out_dir,source_name=None):
 if package not in {'city-steps','city-of-pittsburgh-markings'}:
  raise ValueError('only reviewed WPRDC package identifiers are supported')
 u='https://data.wprdc.org/api/3/action/package_show?id='+urllib.parse.quote(package)
 meta=json.loads(get(u,max_bytes=2_000_000))
 if meta.get('success') is not True:raise RuntimeError('WPRDC CKAN package metadata not successful')
 resources=[r for r in meta['result'].get('resources',[]) if str(r.get('format','')).lower() in ('geojson','json')]
 if not resources:raise RuntimeError('no supported WPRDC GeoJSON resource')
 # Resource names must be inspected: some packages provide crosswalk + marking as separate layers.
 report=[]
 for idx,r in enumerate(resources):
  label=r.get('name','') or 'resource'
  if source_name and source_name.lower() not in label.lower():continue
  uri=r.get('url','')
  if not uri.startswith(('https://data.wprdc.org/','https://pghgishub-pittsburghpa.opendata.arcgis.com/','https://services6.arcgis.com/','https://opendata.arcgis.com/')):
   report.append({'resource_name':label,'status':'URL_DOMAIN_REQUIRES_MANUAL_REVIEW','uri':uri});continue
  try:
   raw=get(uri,max_bytes=40_000_000);payload=json.loads(raw)
   if not isinstance(payload,dict):raise ValueError('GeoJSON expected')
   file=Path(out_dir)/(package+'_'+str(idx)+'.geojson')
   report.append(save(file,raw,{'provider':'WPRDC','package':package,'resource_name':label,'source_url':uri,
    'format':r.get('format'),'source_last_modified':r.get('last_modified'),
    'scope':'historical geography, not validated wheelchair or PUDO reachability'}))
  except Exception as ex:report.append({'resource_name':label,'status':'DOWNLOAD_ERROR','error':str(ex)})
 return report

def main():
 p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='cmd',required=True)
 a=sub.add_parser('lta');a.add_argument('--output',required=True);a.add_argument('--key_env',default='LTA_ACCOUNT_KEY')
 a=sub.add_parser('wprdc');a.add_argument('--package',choices=['city-steps','city-of-pittsburgh-markings'],required=True)
 a.add_argument('--out_dir',required=True);a.add_argument('--resource_name_contains')
 a=p.parse_args()
 r=lta(a.output,a.key_env) if a.cmd=='lta' else wprdc(a.package,a.out_dir,a.resource_name_contains)
 print(json.dumps(r,indent=2,ensure_ascii=False))
if __name__=='__main__':main()
