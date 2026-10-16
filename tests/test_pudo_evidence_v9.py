"""Science V9: negative evidence controls and geospatial metadata boundary."""
import json,os,sys,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.pudo_evidence_v9 import (sites,collect,triage,normalize_collection,
 distance_to_feature,arcgis_features,read_jsonl,SOURCES)
from scripts.study_pudo_evidence_v9 import analyze
import pytest

def test_taxi_bfa_is_only_source_lead(tmp_path):
 site=tmp_path/'sites.jsonl'
 site.write_text(json.dumps({'city':'singapore','physical_site_key':'s1','coordinate_status':'PROJECTED_MAP_POSE_NEEDS_CONTROL_POINT_AUDIT',
  'wgs84':{'lon':103.775,'lat':1.3}})+'\n')
 sample=tmp_path/'taxi.json'
 sample.write_text(json.dumps({'value':[{'TaxiCode':'S1','Latitude':1.30003,'Longitude':103.77501,'Bfa':'Yes','Type':'Stop'}]}))
 out=tmp_path/'out'
 ret=collect(site,out,{'singapore_taxi_stands':str(sample)})
 assert ret['counts']['candidate_features_nearby']==1
 leads=list(read_jsonl(out/'source_leads.jsonl'))
 assert leads[0]['matched_features'][0]['properties']['Bfa']=='Yes'
 assert leads[0]['evidence_type']=='EXTERNAL_SPATIAL_LEAD_NOT_APPROVED_PUDO_FACT'
 triage(site,out/'source_leads.jsonl',out/'triage')
 t=json.loads((out/'triage'/'triage_summary.json').read_text())
 assert t['verified_legal_stops']==0 and t['verified_handoffs']==0
 report=analyze(site,out/'source_leads.jsonl',out/'report.json')
 assert report['hypotheses']['H1']=='PHYSICAL_EXECUTABLE_PUDO_NOT_ESTABLISHED'


def test_empty_official_layer_not_negative(tmp_path):
 s=tmp_path/'sites.jsonl';s.write_text(json.dumps({'city':'boston','physical_site_key':'k','coordinate_status':'PROJECTED_MAP_POSE_NEEDS_CONTROL_POINT_AUDIT',
 'wgs84':{'lon':-71.05,'lat':42.35}})+'\n')
 empty=tmp_path/'empty.geojson';empty.write_text(json.dumps({'type':'FeatureCollection','features':[]}))
 o=tmp_path/'out';collect(s,o,{'boston_ramp':str(empty)})
 triage(s,o/'source_leads.jsonl',o/'triage')
 row=list(read_jsonl(o/'triage'/'site_evidence_triage.jsonl'))[0]
 assert row['evidence_state']=='UNKNOWN_PENDING_INDEPENDENT_REVIEW'
 assert row['physical_curb_verified'] is False


def test_polyline_distance_and_geojson_normalization():
 x={'geometry':{'type':'LineString','coordinates':[[-71.0,42.0],[-70.999,42.0]]}}
 assert distance_to_feature(-71.0,42.0,x)<0.01
 assert normalize_collection({'value':[{'Latitude':1.3001,'Longitude':103.77}]})[0]['geometry']['type']=='Point'
 assert normalize_collection({'features':[]})==[]


def test_arcgis_query_fails_on_truncated_response(monkeypatch):
 from scripts import pudo_evidence_v9 as m
 def fake(url,params,*args,**kwargs):
  if params.get('returnCountOnly'):return {'count':2},'hash','url',b'{}'
  return {'type':'FeatureCollection','features':[{'type':'Feature','properties':{},'geometry':{'type':'Point','coordinates':[-71,42]}}]},'hash','url',b'{}'
 monkeypatch.setattr(m,'fetch_json',fake)
 with pytest.raises(RuntimeError,match='TRUNCATED'):arcgis_features(SOURCES['boston_ramp'],-71,42)


def test_no_hybrid_truth_promotion_with_review_metadata(tmp_path):
 s=tmp_path/'sites.jsonl';s.write_text(json.dumps({'city':'vegas','physical_site_key':'k','wgs84':{'lon':-115.1,'lat':36.1}})+'\n')
 leads=tmp_path/'leads.jsonl';leads.write_text('')
 r=analyze(s,leads,tmp_path/'report.json')
 assert r['independent_review']['independently_confirmed_sites']==0
 assert r['hypotheses']['H3'].startswith('NOT_ESTABLISHED')
