#!/usr/bin/env python3
"""Science V9: non-destructive, evidence-typed reconstruction of real PUDO candidates.

Operations:
  sites: resolve real map coordinates of audited hybrid sites (NOT PUDO truth)
  fetch: collect bounded public ArcGIS candidate features and locally saved external data
  triage: produce field-specific evidence gaps and a source-review worksheet
  verdict: derive research branch without treating missing/nearby data as false/true

External geometry is a *lead* only. No downloaded point automatically sets legal_stop,
curb_connector, passenger boarding or vehicle deployment to True.
"""
from __future__ import annotations
import argparse, csv, datetime, hashlib, json, math, os, sys, time, urllib.parse, urllib.request
from collections import Counter,defaultdict
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from capplan.data.gis_fusion import CoordinateTransformer

CITIES={'boston':(-72.2,41.5,-70.2,43.3),'pittsburgh':(-81.0,39.6,-78.9,41.1),
        'vegas':(-116.4,35.5,-114.4,37.2),'singapore':(103.4,1.05,104.3,1.55)}
# Each dataset is an *evidence lead* only. The taxonomy below is not a legal inference rule.
SOURCES={
 'boston_sidewalk':{'city':'boston','kind':'sidewalk_geometry','layer':'https://gisportal.boston.gov/arcgis/rest/services/Infrastructure/OpenData/MapServer/0','radius_m':35,'url':'https://gisportal.boston.gov/arcgis/rest/services/Infrastructure/OpenData/MapServer/0'},
 'boston_ramp':{'city':'boston','kind':'curb_ramp_record','layer':'https://gisportal.boston.gov/arcgis/rest/services/Infrastructure/OpenData/MapServer/3','radius_m':25,'url':'https://gisportal.boston.gov/arcgis/rest/services/Infrastructure/OpenData/MapServer/3'},
 'boston_curb':{'city':'boston','kind':'curb_geometry','layer':'https://gisportal.boston.gov/arcgis/rest/services/Infrastructure/OpenData/MapServer/13','radius_m':25,'url':'https://gisportal.boston.gov/arcgis/rest/services/Infrastructure/OpenData/MapServer/13'},
 'nevada_dot_ramp':{'city':'vegas','kind':'ada_facility_record','layer':'https://gis.dot.nv.gov/arcgis/rest/services/ArcGISOnline/ADA_ROW_Inventory/MapServer/0','radius_m':30,'url':'https://gis.dot.nv.gov/arcgis/rest/services/ArcGISOnline/ADA_ROW_Inventory/FeatureServer'},
 'nevada_dot_discontinuity':{'city':'vegas','kind':'reported_ada_discontinuity','layer':'https://gis.dot.nv.gov/arcgis/rest/services/ArcGISOnline/ADA_ROW_Inventory/FeatureServer/3','radius_m':30,'url':'https://gis.dot.nv.gov/arcgis/rest/services/ArcGISOnline/ADA_ROW_Inventory/FeatureServer/3'},
 'clark_ramp':{'city':'vegas','kind':'county_ramp_inventory','layer':'https://maps.clarkcountynv.gov/arcgis/rest/services/PW/pwEditLayers/FeatureServer/1','radius_m':30,'url':'https://maps.clarkcountynv.gov/arcgis/rest/services/PW/pwEditLayers/FeatureServer/1'},
 'pittsburgh_steps':{'city':'pittsburgh','kind':'stairway_inventory','local_only':True,'radius_m':30,'url':'https://data.wprdc.org/dataset/city-steps'},
 'pittsburgh_crosswalk':{'city':'pittsburgh','kind':'crosswalk_inventory','local_only':True,'radius_m':30,'url':'https://data.wprdc.org/dataset/city-of-pittsburgh-markings'},
 'pittsburgh_street_closures':{'city':'pittsburgh','kind':'historical_permit_relevant_only_if_time_matched','local_only':True,'radius_m':50,'url':'https://data.wprdc.org/dataset/street-closures'},
 'singapore_taxi_stands':{'city':'singapore','kind':'taxi_only_facility_with_reported_bfa','local_only':True,'radius_m':35,'url':'https://datamall2.mytransport.sg/ltaodataservice/TaxiStands'},
 'singapore_passenger_pickup_bay':{'city':'singapore','kind':'designated_public_loading_candidate','local_only':True,'radius_m':35,'url':'https://datamall.lta.gov.sg/'},
}
FACTS=('sidewalk_to_curb_continuity','crossing_access','legal_stopping','deployment_region','stopping_pose')

def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def sha(b):return hashlib.sha256(b).hexdigest()
def write_json(path,data):
 p=Path(path);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(data,indent=2,ensure_ascii=False,sort_keys=True)+'\n',encoding='utf8')
def write_jsonl(path,records):
 p=Path(path);p.parent.mkdir(parents=True,exist_ok=True)
 with p.open('w',encoding='utf8') as f:
  for r in records:f.write(json.dumps(r,ensure_ascii=False,sort_keys=True)+'\n')
def read_jsonl(path):
 with Path(path).open(encoding='utf8') as f:
  for line in f:
   if line.strip():yield json.loads(line)
def load_anchors(path):
 """Stream anchors, including a ZIP containing huge pudo_anchors.jsonl."""
 if str(path).lower().endswith('.zip'):
  import zipfile
  with zipfile.ZipFile(path) as z:
   entries=[x for x in z.namelist() if x.endswith('pudo_anchors.jsonl')]
   if len(entries)!=1:raise ValueError('Expected a single pudo_anchors.jsonl in ZIP')
   with z.open(entries[0]) as f:
    for b in f:
     if b.strip():yield json.loads(b)
 else:yield from read_jsonl(path)
def get_pose(r):
 p=r.get('curb_pose') or r.get('stop_pose') or {}
 if p.get('frame')!='map':return None
 try:
  x,y=float(p['x']),float(p['y'])
  return (x,y) if all(math.isfinite(v) for v in (x,y)) else None
 except (TypeError,ValueError,KeyError):return None

def sites(chain,anchors,georef_dir,output):
 chain=json.loads(Path(chain).read_text()); chosen={}
 for r in chain['sites']:
  key=(r['city'],r['physical_site_key'])
  chosen.setdefault(key,{'city':r['city'],'physical_site_key':r['physical_site_key'],
   'example_site_id':r['site_id'],'site_ids':[],'episode_ids':[],'records':[]})
  s=chosen[key];s['site_ids'].append(r['site_id']);s['episode_ids'].append(r['episode_id'])
 for r in load_anchors(anchors):
  site_key=r.get('hybrid_physical_site_key')
  # Physical site keys in hybrid data are unique per city; never join by proximity alone.
  if not site_key:continue
  city=site_key.split('|',1)[0]
  s=chosen.get((city,site_key))
  if s is None:continue
  pos=get_pose(r)
  if pos and len(s['records'])<200:s['records'].append({'x':pos[0],'y':pos[1],'anchor_id':r.get('anchor_id')})
 results=[];stats=Counter()
 for (city,key),s in sorted(chosen.items()):
  s['episode_ids']=sorted(set(s['episode_ids']));s['site_ids']=sorted(set(s['site_ids']))
  rec=s.pop('records');s['matching_anchor_rows_retained']=len(rec)
  s['map_xy']=None;s['wgs84']=None;s['coordinate_status']='MISSING_ANCHOR'
  if rec:
   x=sum(p['x'] for p in rec)/len(rec);y=sum(p['y'] for p in rec)/len(rec)
   dispersion=max(math.hypot(x-p['x'],y-p['y']) for p in rec)
   s['anchor_pose_dispersion_m']=round(dispersion,4)
   if dispersion<=2:
    ref=Path(georef_dir)/f'{city}.json'
    if not ref.exists():ref=ROOT/'configs'/f'georeference.{city}.json'
    config=json.loads(ref.read_text())
    if not config.get('projected_map_frame') or not config.get('local_crs'):
     s['coordinate_status']='UNSUPPORTED_GEOREFERENCE'
    else:
     lon,lat=CoordinateTransformer.from_file(ref).local_to_wgs84(x,y)
     lo,la,hi,hj=CITIES[city]
     if math.isfinite(lon) and math.isfinite(lat) and lo<=lon<=hi and la<=lat<=hj:
      s['map_xy']={'x':x,'y':y,'crs':config['local_crs']}
      s['wgs84']={'lon':round(lon,8),'lat':round(lat,8)}
      s['coordinate_status']='PROJECTED_MAP_POSE_NEEDS_CONTROL_POINT_AUDIT' if not config.get('validated') else 'MAP_POSE_COORDINATE_READY_NEEDS_SITE_AUDIT'
      s['georef_file_sha256']=sha(ref.read_bytes())
     else:s['coordinate_status']='CITY_BOUNDS_MISMATCH'
   else:s['coordinate_status']='CONFLICTING_POSES_FOR_SITE_KEY'
  stats[s['coordinate_status']]+=1;results.append(s)
 write_jsonl(output,results)
 report={'rows':len(results),'statuses':dict(stats),'source_anchor_counted':'matched physical-site keys only, not per-episode independent physical locations',
 'warning':'Projected map PUDO proxy != legal stopping pose; georeference configs may have validated=false; verify against control points.'}
 write_json(Path(output).with_suffix('.summary.json'),report)
 write_json(Path(output).with_suffix('.geojson'),{'type':'FeatureCollection','features':[
  {'type':'Feature','geometry':{'type':'Point','coordinates':[r['wgs84']['lon'],r['wgs84']['lat']]},
   'properties':{k:r[k] for k in ('city','physical_site_key','coordinate_status')}} for r in results if r['wgs84']]})
 return report

def haversine(lon1,lat1,lon2,lat2):
 r=6371008.8;dlat=math.radians(lat2-lat1);dlon=math.radians(lon2-lon1)
 a=math.sin(dlat/2)**2+math.cos(math.radians(lat1))*math.cos(math.radians(lat2))*math.sin(dlon/2)**2
 return 2*r*math.asin(min(1,math.sqrt(a)))

def coords(g):
 if not isinstance(g,dict):return []
 if g.get('type')=='Point':return [g.get('coordinates')]
 if g.get('type') in ('MultiPoint','LineString'):return g.get('coordinates') or []
 if g.get('type')=='MultiLineString':return [p for l in g.get('coordinates',[]) for p in l]
 if g.get('type')=='Polygon':return [p for l in g.get('coordinates',[]) for p in l]
 if g.get('type')=='MultiPolygon':return [p for o in g.get('coordinates',[]) for l in o for p in l]
 return []

def distance_to_feature(lon,lat,feature):
 g=feature.get('geometry') or {};cs=coords(g)
 valid=[]
 for p in cs:
  if isinstance(p,(list,tuple)) and len(p)>=2:
   try:
    x,y=float(p[0]),float(p[1])
    if math.isfinite(x) and math.isfinite(y) and -180<=x<=180 and -90<=y<=90:valid.append((x,y))
   except (TypeError,ValueError):pass
 if not valid:return None
 best=min(haversine(lon,lat,x,y) for x,y in valid)
 # Prefer exact shapely distance on a locally projected geometry if installed;
 # vertex distances alone can overestimate long sidewalk lines.
 try:
  from shapely.geometry import shape,Point
  from shapely.ops import transform
  from pyproj import Transformer
  utm=int((lon+180)//6)+1
  local=f'EPSG:{32600+utm if lat>=0 else 32700+utm}'
  tr=Transformer.from_crs('EPSG:4326',local,always_xy=True)
  return float(transform(tr.transform,shape(g)).distance(Point(*tr.transform(lon,lat))))
 except (ImportError,ValueError,TypeError):return best

def normalize_collection(payload):
 if isinstance(payload,list):raw=payload
 elif isinstance(payload,dict):raw=(payload.get('features') or payload.get('value') or payload.get('Value') or payload.get('records') or [])
 else:raw=[]
 feats=[]
 for r in raw:
  if not isinstance(r,dict):continue
  if r.get('type')=='Feature' and isinstance(r.get('geometry'),dict):
   feats.append(r);continue
  lon=r.get('Longitude',r.get('longitude',r.get('lon')))
  lat=r.get('Latitude',r.get('latitude',r.get('lat')))
  if lon is None or lat is None:continue
  try:
   lon,lat=float(lon),float(lat)
   if not (-180<=lon<=180 and -90<=lat<=90):continue
  except (ValueError,TypeError):continue
  feats.append({'type':'Feature','properties':r,'geometry':{'type':'Point','coordinates':[lon,lat]}})
 return feats

def fetch_json(url,params,timeout=25,retries=2):
 uri=url+'?'+urllib.parse.urlencode(params)
 last=None
 for i in range(retries):
  try:
   req=urllib.request.Request(uri,headers={'User-Agent':'CapPlan-V9 Research (bounded public GIS provenance audit)','Accept':'application/json'})
   with urllib.request.urlopen(req,timeout=timeout) as r:data=r.read(8*1024*1024)
   obj=json.loads(data)
   if obj.get('error'):raise RuntimeError(str(obj['error']))
   return obj,sha(data),uri,data
  except Exception as exc:last=exc;time.sleep(0.2*(i+1))
 raise RuntimeError(str(last))

def arcgis_features(src,lon,lat):
 # Small bbox in WGS84, no global layer harvesting and no per-field legal inference.
 radius=src['radius_m'];delta_lat=radius/111320;delta_lon=delta_lat/max(0.05,math.cos(math.radians(lat)))
 geom={'xmin':lon-delta_lon,'ymin':lat-delta_lat,'xmax':lon+delta_lon,'ymax':lat+delta_lat,'spatialReference':{'wkid':4326}}
 q=src['layer'].rstrip('/')+'/query'
 base={'where':'1=1','geometry':json.dumps(geom),'geometryType':'esriGeometryEnvelope',
       'inSR':4326,'spatialRel':'esriSpatialRelIntersects','outSR':4326,'f':'geojson','outFields':'*','returnGeometry':'true'}
 # ESRI GeoJSON may omit count by definition; cap per-location result & fail closed if exceeded.
 countbase={**base,'f':'json','returnCountOnly':'true'}
 count,_,_,_=fetch_json(q,countbase)
 cnt=int(count.get('count',0))
 if cnt>150:raise RuntimeError(f'TOO_MANY_LOCAL_MATCHES_{cnt}; smaller radius needed')
 payload,hashval,request_url,raw=fetch_json(q,base)
 feats=normalize_collection(payload)
 if len(feats)!=cnt:raise RuntimeError(f'TRUNCATED_OR_UNSUPPORTED_GEOJSON count={cnt}, returned={len(feats)}')
 return feats,hashval,request_url,raw

def read_local_source(path):
 path=Path(path)
 if path.suffix.lower() in ('.json','.geojson'):
  payload=json.loads(path.read_text(encoding='utf8'))
 elif path.suffix.lower()=='.jsonl':payload=list(read_jsonl(path))
 elif path.suffix.lower()=='.csv':
  with path.open(newline='',encoding='utf8') as f:payload=list(csv.DictReader(f))
 else:raise ValueError('source file must be JSON, GeoJSON, JSONL or CSV')
 return normalize_collection(payload),sha(path.read_bytes())

def collect(sites_path,out_dir,local_files=None,enable_network=False,only_sources=None,max_sites=None):
 out=Path(out_dir);out.mkdir(parents=True,exist_ok=True)
 local_files=dict(local_files or {});selected=set(only_sources or SOURCES)
 if selected-set(SOURCES):raise ValueError('unknown sources: '+str(selected-set(SOURCES)))
 sites=list(read_jsonl(sites_path));sites=sites[:max_sites] if max_sites else sites
 local_cache={};records=[];errors=[];counts=Counter()
 for sid,path in local_files.items():
  if sid not in SOURCES:raise ValueError('unknown local source '+sid)
  local_cache[sid]=read_local_source(path)
 for site in sites:
  p=site.get('wgs84');city=site.get('city')
  if not p or site.get('coordinate_status') in ('CITY_BOUNDS_MISMATCH','CONFLICTING_POSES_FOR_SITE_KEY'):
   counts['site_without_coordinates']+=1;continue
  for sid,src in SOURCES.items():
   if sid not in selected or src['city']!=city:continue
   if sid not in local_cache and (not enable_network or src.get('local_only')):
    counts['source_not_fetched']+=1;continue
   try:
    if sid in local_cache:feats,rawhash=local_cache[sid];uri=str(local_files[sid]);method='local_official_candidate_snapshot'
    else:
     feats,rawhash,uri,raw=arcgis_features(src,p['lon'],p['lat'])
     snapshot=out/'raw_source_snapshots'/f'{rawhash}.json'
     snapshot.parent.mkdir(parents=True,exist_ok=True)
     if not snapshot.exists():snapshot.write_bytes(raw)
     method='bounded_arcgis_query'
    matches=[]
    for f in feats:
     d=distance_to_feature(p['lon'],p['lat'],f)
     if d is None or d>src['radius_m']:continue
     props=f.get('properties') or {}
     feature_id=f.get('id') or props.get('OBJECTID') or props.get('GlobalID') or props.get('TaxiCode') or props.get('id')
     matches.append({'distance_m':round(d,2),'feature_id':str(feature_id) if feature_id is not None else 'NO_FEATURE_ID',
       'properties':props,'feature_geometry':f.get('geometry')})
    matches.sort(key=lambda x:x['distance_m']);matches=matches[:15]
    record={'site_key':site['physical_site_key'],'city':city,'source_id':sid,'source_kind':src['kind'],
      'matched_features':matches,'matched_feature_count':len(matches),'query_radius_m':src['radius_m'],
      'raw_sha256':rawhash,'source_url':src['url'],'retrieved_at_utc':now(),
      'geometry_match_scope':'CANDIDATE_ASSOCIATION_NOT_EXECUTABLE_CURB_LINK',
      'evidence_type':'EXTERNAL_SPATIAL_LEAD_NOT_APPROVED_PUDO_FACT',
      'data_reference':str(snapshot) if method=='bounded_arcgis_query' else uri,
      'source_request_url':uri if method=='bounded_arcgis_query' else src['url'],'collection_method':method,
      'temporal_caveat':'2026 snapshot cannot prove dynamic or static status in 2021 nuPlan scene'}
    records.append(record);counts['successful_spatial_queries']+=1;counts['candidate_features_nearby']+=len(matches)
   except Exception as ex:
    errors.append({'site_key':site['physical_site_key'],'source_id':sid,'error':str(ex)})
    counts['source_query_errors']+=1
 write_jsonl(out/'source_leads.jsonl',records)
 write_json(out/'source_collection_summary.json',{'counts':dict(counts),'errors':errors,'total_requested_sites':len(sites),
  'network_enabled':enable_network,'sources_requested':sorted(selected),'source_registry':SOURCES,
  'semantic_boundary':'Nearby facility record can NEVER automatically set full PUDO legality, boarding or vehicle stopping pose'})
 return {'counts':dict(counts),'errors':len(errors)}

def triage(sites,leads,out_dir):
 out=Path(out_dir);out.mkdir(parents=True,exist_ok=True)
 bysite=defaultdict(list)
 for r in read_jsonl(leads):bysite[r['site_key']].append(r)
 rows=[];summary=Counter();review_fields=[]
 for site in read_jsonl(sites):
  key=site['physical_site_key'];sources=bysite[key]
  recommendations=[]
  for r in sources:
   if r['matched_feature_count']:recommendations.append(r['source_id']+':'+str(r['matched_feature_count']))
  for fact in FACTS:
   # Spatial candidates never decide the truth. Critical manual review is always required.
   row={'city':site['city'],'physical_site_key':key,'fact':fact,'value':'unknown',
       'reviewer_id':'','source_id':'','source_kind':'','checked_at_utc':'','evidence_uri':'',
       'derived_from_hybrid_label':'False','site_x':(site.get('map_xy') or {}).get('x',''),
       'site_y':(site.get('map_xy') or {}).get('y',''),
       'suggested_source_leads':';'.join(recommendations),
       'site_wgs84_lon':(site.get('wgs84') or {}).get('lon',''),
       'site_wgs84_lat':(site.get('wgs84') or {}).get('lat','')}
   review_fields.append(row)
  if any(r.get('matched_feature_count') for r in sources):summary['sites_with_source_leads']+=1
  else:summary['sites_without_source_leads']+=1
  rows.append({'city':site['city'],'physical_site_key':key,'source_leads':recommendations,
               'coordinate_status':site['coordinate_status'],'evidence_state':'UNKNOWN_PENDING_INDEPENDENT_REVIEW',
               'physical_curb_verified':False,'legal_stop_verified':False,'boarding_verified':False,
               'hypothesis':'PUDO physical feasibility cannot be adjudicated from nearby geometry alone'})
 cols=list(review_fields[0]) if review_fields else []
 with (out/'physical_review_with_source_leads_v9.csv').open('w',newline='',encoding='utf8') as f:
  w=csv.DictWriter(f,fieldnames=cols);w.writeheader();w.writerows(review_fields)
 write_jsonl(out/'site_evidence_triage.jsonl',rows)
 write_json(out/'triage_summary.json',{'site_count':len(rows),'review_fact_rows':len(review_fields),
  'source_lead_coverage':dict(summary),'verified_legal_stops':0,'verified_handoffs':0,
  'reason':'No independent physical fact adjudication has been submitted; spatial association is not legal or access proof',
  'next_branch':'AUDIT_PHYSICAL_SITE_AND_REGULATION_SEMANTICS_BEFORE_RECURSIVE_PLANNING_CLAIMS'})
 return {'sites':len(rows),'with_leads':summary['sites_with_source_leads'],'review_rows':len(review_fields)}

def merge_leads(inputs,output):
    """Idempotent combination of available source-specific evidence; missing stays missing."""
    seen=set();merged=[];files=[]
    for name in inputs:
        path=Path(name)
        if not path.exists():continue
        files.append(str(path))
        for r in read_jsonl(path):
            key=(r.get('city'),r.get('site_key'),r.get('source_id'),r.get('raw_sha256'))
            if key in seen:continue
            seen.add(key);merged.append(r)
    write_jsonl(output,merged)
    return {'source_files':files,'records':len(merged),'output':str(output),
       'interpretation':'missing sources do not imply no facilities; raw source matches are not verified PUDO facts'}

def make_parser():
 p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='cmd',required=True)
 a=sub.add_parser('sites');a.add_argument('--chain',required=True);a.add_argument('--anchors',required=True,
  help='large pudo_anchors.jsonl or a ZIP containing exactly one such file');a.add_argument('--georef_dir',default=str(ROOT/'configs'));
 a.add_argument('--output',required=True)
 a=sub.add_parser('fetch');a.add_argument('--sites',required=True);a.add_argument('--out_dir',required=True)
 a.add_argument('--enable_network',action='store_true',help='Explicit opt-in to official ArcGIS spatial network queries')
 a.add_argument('--local_source',action='append',default=[],help='source_id=/path/to/GeoJSON-or-CSV-or-LTA-json')
 a.add_argument('--only_sources',default=None,help='comma separated source identifiers');a.add_argument('--max_sites',type=int)
 a=sub.add_parser('triage');a.add_argument('--sites',required=True);a.add_argument('--leads',required=True);a.add_argument('--out_dir',required=True)
 a=sub.add_parser('merge');a.add_argument('--inputs',nargs='+',required=True);a.add_argument('--output',required=True)
 a=sub.add_parser('sources')
 return p

def main():
 p=make_parser();a=p.parse_args()
 if a.cmd=='sites':res=sites(a.chain,a.anchors,a.georef_dir,a.output)
 elif a.cmd=='fetch':
  local={}
  for q in a.local_source:
   if '=' not in q:raise SystemExit('local_source must be source_id=/path')
   name,path=q.split('=',1);local[name]=path
  res=collect(a.sites,a.out_dir,local,a.enable_network,
    a.only_sources.split(',') if a.only_sources else None,a.max_sites)
 elif a.cmd=='triage':res=triage(a.sites,a.leads,a.out_dir)
 elif a.cmd=='merge':res=merge_leads(a.inputs,a.output)
 else:res=SOURCES
 print(json.dumps(res,indent=2,ensure_ascii=False))
if __name__=='__main__':main()
