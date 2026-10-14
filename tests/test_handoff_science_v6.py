"""Regression checks on evidence gating and nuPlan exact-log binding."""
import json,sqlite3,hashlib,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.nuplan_log_bind_v6 import bind,classify_map
from scripts.prepare_physical_review_v6 import create
from scripts.audit_handoff_dataset_sample_v6 import audit
from scripts.summarize_science_v6 import summarize
from scripts.audit_carla_controls_v6 import audit as audit_carla


def jlines(path,rows):
    Path(path).write_text(''.join(json.dumps(r)+'\n' for r in rows))


def test_exact_log_binding_and_no_bruteforce(tmp_path):
    base=tmp_path/'splits'/'test';base.mkdir(parents=True)
    blob=bytes.fromhex('19c235d5ca2b547e');stamp=1660000000000000
    conn=sqlite3.connect(base/'matching.db')
    conn.execute('CREATE TABLE log (location TEXT, logfile TEXT, map_version TEXT)')
    conn.execute('INSERT INTO log VALUES (?,?,?)',('us-ma-boston','matching','v1'))
    conn.execute('CREATE TABLE ego_pose (token BLOB,x REAL,y REAL,timestamp INTEGER)')
    conn.execute('CREATE TABLE lidar_pc (token BLOB,timestamp INTEGER,ego_pose_token BLOB)')
    conn.execute('INSERT INTO ego_pose VALUES (?,?,?,?)',(b'ego',100.,200.,stamp))
    conn.execute('INSERT INTO lidar_pc VALUES (?,?,?)',(blob,stamp,b'ego'))
    conn.commit();conn.close()
    epi=tmp_path/'episodes.jsonl';jlines(epi,[{'episode_id':'e1','log_name':'matching',
        'scenario_token':blob.hex(),'map_name':'us-ma-boston'}])
    sites=tmp_path/'sites.jsonl';jlines(sites,[{'episode_id':'e1','site_id':'e1:A','pose':{'x':101,'y':200},'site_key':'p1'}])
    result=bind(sites,epi,tmp_path/'splits')
    assert result['dbs_in_inventory']==1
    assert result['counts']=={'EXACT_LIDAR_TOKEN_BOUND':1}
    assert abs(result['episodes'][0]['site_results'][0]['nearby']['nearest']['distance_m']-1)<1e-6
    assert result['episodes'][0]['site_results'][0]['physical_pudo_legal_or_boardable'] is None


def test_missing_log_never_searches_all_db(tmp_path):
    (tmp_path/'test').mkdir()
    (tmp_path/'test'/'a.db').touch()
    epi=tmp_path/'episodes';jlines(epi,[{'episode_id':'e','log_name':'missing','map_name':'us-ma-boston'}])
    s=tmp_path/'sites';jlines(s,[{'episode_id':'e','site_id':'e:A','pose':{'x':0,'y':0}}])
    d=bind(s,epi,tmp_path)
    assert d['counts']=={'LOG_DB_NOT_FOUND':1}


def test_review_template_unverified(tmp_path):
    s=tmp_path/'sites.jsonl';jlines(s,[{'city':'boston','site_key':'k','site_id':'e:A','pose':{'x':1.,'y':2.}},
      {'city':'boston','site_key':'k','site_id':'e:A','pose':{'x':1.,'y':2.}}])
    d=create(s,tmp_path/'f.csv',tmp_path/'f.jsonl')
    assert d['fact_rows']==5 and d['confirmed_physical_sites']==0
    assert all(json.loads(row)['value']=='unknown' for row in (tmp_path/'f.jsonl').read_text().splitlines())


def test_status_guards():
    report=summarize({'clear_A_control_valid':False,'redirect_B_goal_zone_observed':True},
      {'counts':{'EXACT_LIDAR_TOKEN_BOUND':1}},None,{'grid_rows':92160},{'dataset_name':'abilitybench_av_val'})
    assert report['H1_physical_handoff_joint_feasibility']['state']=='UNVERIFIED'
    assert report['H3_evidence_timed_joint_decision']['state']=='NOT_SHOWN_NECESSARY'
    assert report['next_branch']=='REPAIR_CARLA_CLEAR_CONTROL_AND_COLLECT_AUDITED_PUDO'


def test_carla_upload_baseline_gate_if_present():
    p=Path('/mnt/data/handoff_science_v5(1).zip')
    if p.exists():
        d=audit_carla(p)
        assert d['clear_A_control_valid'] is False
        assert d['blocked_A_causal_effect_established'] is False


def test_supplied_dataset_bootstrap_flag_if_present():
    p=Path('/mnt/data/abilitybench_av_val.zip')
    if p.exists():
        d=audit(p,max_anchors=10)
        assert d['appears_hybrid'] is False
        assert d['usable_as_handoff_hybrid_test_truth'] is False
