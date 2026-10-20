#!/usr/bin/env python3
"""Download an *existing* public ArcGIS accessibility point layer by WGS84 bbox.

No site is synthesized; output is raw GeoJSON plus provenance manifest. Data
from NDOT/DC/other sources never implies safe autonomous boarding, legal PUDO,
current ramp availability, deployment space, or complete curb interface.
Avoid placing retrieved records into ground-truth labels without manual schema
mapping and temporal / coordinate / licensing verification.
"""
from __future__ import annotations
import argparse,datetime,hashlib,json,time
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

SOURCES={
 'boston_ramp_inventory':{
  'url':'https://gisportal.boston.gov/arcgis/rest/services/Infrastructure/OpenData/MapServer/3',
  'publisher':'City of Boston','scope':'Municipal ramp inventory; check whether an equivalent source is already in external/ before merging'},
 'nevada_dot_ada_ramps':{
  'url':'https://gis.dot.nv.gov/arcgis/rest/services/ArcGISOnline/ADA_ROW_Inventory/MapServer/0',
  'publisher':'Nevada DOT','scope':'ADA ramp inventory; jurisdiction/coverage must be checked'},
 'dc_ada_curbramps':{
  'url':'https://maps2.dcgis.dc.gov/dcgis/rest/services/DCGIS_DATA/Transportation_ADA_WebMercator/MapServer/3',
  'publisher':'DCGIS','scope':'2016 DC ADA ramp condition inventory; out-of-city transfer audit only'},
}

def validate_bbox(b):
    if len(b)!=4:raise ValueError('bbox requires west,south,east,north')
    w,s,e,n=map(float,b)
    if not (-180<=w<e<=180 and -90<=s<n<=90):raise ValueError('invalid WGS84 bounds')
    return [w,s,e,n]

def to_feature(obj):
    if obj.get('type')=='Feature':return obj
    if obj.get('geometry') is None:raise ValueError('missing ArcGIS feature geometry')
    g=obj['geometry'];typ='Point'
    if 'x' in g and 'y' in g:geom={'type':'Point','coordinates':[g['x'],g['y']]}
    else:raise ValueError('Only point layers supported; do not silently convert non-point geometry')
    return {'type':'Feature','geometry':geom,'properties':obj.get('attributes',{})}

def fetch(url,bbox,limit=5000,page=500,seconds=30):
    bbox=validate_bbox(bbox);out=[];offset=0
    while offset<limit:
        qs=urlencode({'where':'1=1','geometry':','.join(map(str,bbox)),
           'geometryType':'esriGeometryEnvelope','spatialRel':'esriSpatialRelIntersects',
           'inSR':4326,'outSR':4326,'outFields':'*','returnGeometry':'true',
           'f':'geojson','resultOffset':offset,'resultRecordCount':min(page,limit-offset)})
        with urlopen(url.rstrip('/')+'/query?'+qs,timeout=seconds) as res:
            raw=json.load(res)
        if raw.get('error'):raise ValueError(f'ArcGIS service error: {raw["error"]}')
        if raw.get('type')=='FeatureCollection':chunk=raw.get('features',[])
        elif 'features' in raw:chunk=[to_feature(f) for f in raw['features']]
        else:raise ValueError('Unrecognized ArcGIS response schema')
        out.extend(chunk)
        if len(chunk)<min(page,limit-offset):break
        offset+=len(chunk)
        if not chunk:break
    return {'type':'FeatureCollection','features':out},len(out)>=limit

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',required=True,choices=sorted(SOURCES))
    p.add_argument('--bbox',nargs=4,type=float,required=True,metavar=('W','S','E','N'))
    p.add_argument('--out',required=True)
    p.add_argument('--max_features',type=int,default=5000)
    p.add_argument('--page_size',type=int,default=500)
    a=p.parse_args()
    if not (1<=a.max_features<=100000 and 1<=a.page_size<=5000):p.error('invalid limits')
    src=SOURCES[a.source]
    result,truncated=fetch(src['url'],a.bbox,a.max_features,a.page_size)
    out=Path(a.out);out.parent.mkdir(parents=True,exist_ok=True)
    data=json.dumps(result,ensure_ascii=False,separators=(',',':')).encode('utf8')
    out.write_bytes(data)
    manifest={'source':a.source,'publisher':src['publisher'],'query_url':src['url'],
     'area_wgs84':validate_bbox(a.bbox),'retrieved_at_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),
     'sha256':hashlib.sha256(data).hexdigest(),'count':len(result['features']),
     'possible_truncation':truncated,'scope':src['scope'],
     'field_truth_contract':'Raw source features only. NO inference of legal PUDO, full connector or AV boarding.'}
    out.with_suffix('.manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'features':manifest['count'],'possible_truncation':truncated,'file':str(out)}))
if __name__=='__main__':main()
