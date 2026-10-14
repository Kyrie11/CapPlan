import hashlib, json, sqlite3, zipfile
from pathlib import Path
import pytest
from scripts.audit_handoff_claims_v5 import inspect_zip
from scripts.audit_handoff_physical_v5 import audit,validate_row
from scripts.nuplan_logged_ego_proximity_v5 import probe
from scripts.export_handoff_log_sites_v5 import run as export_sites
from scripts.run_carla_controlled_handoff_v5 import offline_spec
from scripts.gate3_profile_time_sweep_v5 import evaluate_grid
from tests.test_handoff_science_v4 import graph, pair


def test_review_integrity_from_uploaded(tmp_path):
    p=Path(__file__).resolve().parents[2]/'input_review' # optional in developer package
    # Standalone in tree test: make small valid hash-bound review.
    z=tmp_path/'review.zip';b=b'{"counts":{"pairs":5}}'
    with zipfile.ZipFile(z,'w') as dst:
        dst.writestr('gate0_preflight.json',b)
        dst.writestr('REPRODUCIBILITY_MANIFEST.json',json.dumps({'files':{'gate0_preflight.json':hashlib.sha256(b).hexdigest()}}))
    r=inspect_zip(z)
    assert r['pass'] and r['scientific_claims']['real_scenario_verified']=='NOT_YET'


def test_hash_mismatch_blocks(tmp_path):
    z=tmp_path/'bad.zip'
    with zipfile.ZipFile(z,'w') as dst:
        dst.writestr('gate0_preflight.json',b'{}')
        dst.writestr('REPRODUCIBILITY_MANIFEST.json',json.dumps({'files':{'gate0_preflight.json':'0'*64}}))
    assert not inspect_zip(z)['pass']


def site_row(fact='sidewalk_to_curb_continuity',value=True):
    return {'city':'boston','physical_site_key':'s','reviewer_id':'reviewer','source_id':fact,'source_kind':'official_gis',
        'checked_at_utc':'2026-10-13T00:00:00Z','evidence_uri':'https://example.org/record','fact':fact,'value':value}


def test_unknown_review_not_true():
    d=audit([(1,site_row(value='unknown'))]);assert d['metadata_complete_positive_sites']==0
    assert d['independently_confirmed_sites']==0


def test_all_claimed_facts_still_not_verified():
    from scripts.audit_handoff_physical_v5 import REQUIRED
    d=audit([(n,site_row(fact)) for n,fact in enumerate(REQUIRED,1)])
    assert d['metadata_complete_positive_sites']==1
    assert d['independently_confirmed_sites']==0


def test_simulated_cannot_be_real_audit():
    row=site_row();row['source_kind']='simulated';assert 'NONINDEPENDENT_OR_UNKNOWN_SOURCE_KIND' in validate_row(row)


def test_repeated_conflicting_claims_quarantined():
    d=audit([(1,site_row(value=True)),(2,site_row(value=False))])
    assert d['quarantine'] and d['metadata_complete_positive_sites']==0


def test_nuplan_sqlite_proximity_is_not_stop(tmp_path):
    db=tmp_path/'mini.db'
    with sqlite3.connect(db) as cn:
        cn.execute('create table ego_pose(x real,y real,timestamp integer,vx real,vy real)')
        cn.execute('insert into ego_pose values (100,200,1000000,2,1)')
        cn.execute('create table log(location text,map_version text)')
        cn.execute("insert into log values('boston','us-ma-boston')")
    d=probe(db,[{'site_id':'s','pose':{'x':99,'y':200}}],25)
    a=d['site_results'][0]
    assert a['nearest_logged_ego']['center_distance_m']==1
    assert not a['matched_pudo_stop'] and not a['episode_identity_matched']


def test_nuplan_sqlite_schema_fail_closed(tmp_path):
    db=tmp_path/'broken.db'
    with sqlite3.connect(db) as cn:cn.execute('create table ego_pose(x real)')
    with pytest.raises(ValueError):probe(db,[{'site_id':'s','pose':{'x':1,'y':2}}])


def test_export_poses_preserves_proxy(tmp_path):
    p=tmp_path/'pairs.jsonl';row=pair();row['anchor_vehicle_poses']={'A_stop_pose':{'x':1,'y':2,'frame':'map'},'B_stop_pose':{'x':3,'y':4,'frame':'map'}}
    p.write_text(json.dumps(row)+'\n');out=tmp_path/'sites.jsonl'
    assert export_sites(p,out)['sites']==2
    assert 'UNVERIFIED_GIS_POSE_PROXY' in out.read_text()


def test_carla_metadata_excludes_site_truth():
    d=offline_spec();assert 'no passenger capability' in d['limits']
    assert d['output_truth_type']=='CARLA_CONTROLLED_VEHICLE_TRAJECTORY_ONLY'


def test_time_grid_applies_profile_distance_and_ack():
    p=pair();p['pedestrian_graph_nodes']['origin']='O'
    profiles=[{'profile_id':'tight','mobility':{'max_access_distance_m':70}},
              {'profile_id':'loose','mobility':{'max_access_distance_m':160}}]
    rows=list(evaluate_grid(p,graph(),profiles,[0,80],[1],[0]))
    assert len(rows)==4
    assert rows[0]['graph_possible'] is True # O->B 65 <= 70
    assert rows[1]['graph_possible'] is False # O->A 60 + A->B 75 > 70
    assert rows[3]['graph_possible'] is True
    assert all('GRAPH' in r['research_scope'] for r in rows)
