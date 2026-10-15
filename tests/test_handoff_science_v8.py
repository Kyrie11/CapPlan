"""V8 scientific guardrails; no CARLA server required."""
import hashlib, importlib.util, io, json, zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def load(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/f'{name}.py')
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    return mod

def test_graph_directionality_and_physical_status():
    m=load('audit_handoff_physical_chain_v8')
    nodes=b'\n'.join(json.dumps({'node_id':v}).encode() for v in ['ped:1:2','ped:3:4'])
    edges=json.dumps({'from_node':'ped:1:2','to_node':'ped:3:4','length_m':10.,'crossing_type':'sidewalk',
                      'metadata':{'field_provenance':{'width_m':{'kind':'simulated'}}}}).encode()
    ss=[{'site_id':'x:A','physical_site_key':'a|ped:1:2|no_lane'}, {'site_id':'x:B','physical_site_key':'a|ped:3:4|no_lane'}]
    r,g=m.per_graph(nodes,edges,ss)
    assert r['_AB']['directed_A_to_B_m']==10
    assert r['_AB']['directed_B_to_A_m'] is None
    assert r['x:A']['typed_curb_incident_count']==0
    assert g['attribute_provenance']['width_m:simulated']==1

def test_graph_zip_checks_authenticity(tmp_path):
    m=load('audit_handoff_physical_chain_v8')
    site={'site_id':'x:A','episode_id':'x','physical_site_key':'a|ped:1:2','city':'a'}
    chain=tmp_path/'chain.json';chain.write_text(json.dumps({'sites':[site]}))
    nodes=b'{"node_id":"ped:1:2"}\n';edges=b''
    with zipfile.ZipFile(tmp_path/'g.zip','w') as z:
        z.writestr('accessibility_graphs/x.nodes.jsonl',nodes)
        z.writestr('accessibility_graphs/x.edges.jsonl',edges)
        z.writestr('MANIFEST.json',json.dumps({'hashes':{'accessibility_graphs/x.nodes.jsonl':'bad',
                                                        'accessibility_graphs/x.edges.jsonl':hashlib.sha256(edges).hexdigest()}}))
    try:m.audit(chain,tmp_path/'g.zip',tmp_path/'out.json')
    except ValueError as e:assert 'hash mismatch' in str(e)
    else:assert False,'tampered graph accepted'

def test_carla_independent_negative_baseline():
    m=load('audit_carla_paired_v8')
    data=[{'elapsed_s':i*0.05,'distance_to_goal_m':10-i,'speed_mps':2.0,'x':0,'y':0,'scripted_braking':False} for i in range(5)]
    raw=b''.join(json.dumps(x).encode()+b'\n' for x in data)
    r={'trajectory_file_sha256':hashlib.sha256(raw).hexdigest(),'geometry_parameters':{'arrival_radius_m':4},
       'reached_goal_zone':False}
    a=m.check_trace(r,raw)
    assert a['valid'] and not a['goal_zone_entered_recomputed'] and not a['scripted_brake_ever_active']

def test_preview_only_tuning_not_truth():
    m=load('run_carla_controlled_handoff_v5')
    assert not m.preview_brake_required(5,2,4,0,True)
    assert not m.preview_brake_required(5,2,4,2,False)
    assert m.preview_brake_required(5,2,4,2,True)
    assert not m.preview_brake_required(15,2,4,2,True)

def test_study_fail_closed(tmp_path):
    m=load('study_handoff_evidence_v8')
    chain=tmp_path/'c.json';graph=tmp_path/'g.json';carla=tmp_path/'ca.json'
    chain.write_text(json.dumps({'sites':[{'episode_id':'a','city':'X','site_id':'a:A','physical_site_key':'X|ped:1:2'}]}))
    graph.write_text(json.dumps({'summary':{'sites':1,'independently_confirmed_legal_stops':0,'independently_confirmed_boarding':0}}))
    carla.write_text(json.dumps({'summary':{'BASELINE_CLEAR_SERVICE_STOP_NOT_ESTABLISHED':4}}))
    result=m.report(chain,graph,carla,tmp_path/'out')
    assert result['H1']=='NOT_ESTABLISHED_PHYSICAL_PUDO_AND_BOARDING'
    assert result['H3']=='NOT_ESTABLISHED_NO_FAIR_FULL_ACTION_BELIEF_BASELINES'
    assert result['unique_site_keys_to_review']==1

def test_review_unknown_does_not_manufacture_ground_truth(tmp_path):
    import csv
    m=load('import_physical_review_v8')
    fields=['city','physical_site_key','fact','value','reviewer_id','source_id','source_kind','checked_at_utc','evidence_uri']
    p=tmp_path/'r.csv'
    with p.open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        writer.writerow({'city':'x','physical_site_key':'site','fact':'legal_stopping','value':'unknown'})
    out=tmp_path/'claims.jsonl';r=m.convert(p,out)
    assert r['reviewed_rows']==0 and r['unknown_rows']==1
    assert out.exists() and not out.read_bytes()
