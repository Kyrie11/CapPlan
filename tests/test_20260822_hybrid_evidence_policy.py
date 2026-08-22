from __future__ import annotations
import csv, json, subprocess, sys
from pathlib import Path

from capplan.data.schemas import PUDOAnchor, Pose2D, VehicleInterface
from capplan.planning.transition_generator import TransitionGenerator

ROOT=Path(__file__).resolve().parents[1]

def _write_jsonl(path: Path, rows):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text("".join(json.dumps(r)+"\n" for r in rows),encoding="utf-8")

def test_hybrid_pudo_demotes_unproven_fail_closed_legality_and_adds_provenance(tmp_path: Path):
    inp=tmp_path/'in.jsonl'; out=tmp_path/'out.jsonl'; audit=tmp_path/'audit.csv'; report=tmp_path/'r.json'
    rows=[]
    for i in range(2):
        rows.append({
            'anchor_id':f'a{i}','episode_id':'ep','kind':'pickup_dropoff','side':'right',
            'legal_stop':False,'legal_stop_source':'no_legality_evidence',
            'adjacent_ped_node_id':f'n{i}','curb_height_m':None,'sidewalk_width_m':None,
            'deployment_clearance_m':None,'blockage_risk':0.0,'source':'nuplan_route_candidate',
        })
    _write_jsonl(inp,rows)
    audit.write_text('audit_id,candidate_anchor_ids_train\n',encoding='utf-8')
    subprocess.check_call([sys.executable,str(ROOT/'scripts/build_hybrid_pudo_evidence.py'),
        '--input_pudo_jsonl',str(inp),'--output_pudo_jsonl',str(out),'--city','boston','--split','train',
        '--audit_worklist_csv',str(audit),'--seed','7','--min_positive_per_episode','2','--report_json',str(report)],cwd=ROOT)
    got=[json.loads(x) for x in out.read_text().splitlines() if x.strip()]
    assert len(got)==2 and all(r['hybrid_eligible'] for r in got)
    assert all(r['legal_stop'] is True for r in got)
    assert all(r['field_provenance']['legal_stop']['kind']=='simulated' for r in got)
    assert all(r['paper_eligible'] is False and r['paper_claim_allowed'] is False for r in got)
    assert all(r['deployment_clearance_semantics']=='available_environment_clear_space' for r in got)

def test_hybrid_accessibility_only_fills_missing_and_records_field_provenance(tmp_path: Path):
    inp=tmp_path/'g'; out=tmp_path/'gh'; inp.mkdir()
    _write_jsonl(inp/'ep.nodes.jsonl',[{'node_id':'n0','x':0,'y':0,'kind':'sidewalk'},{'node_id':'n1','x':1,'y':0,'kind':'sidewalk'}])
    _write_jsonl(inp/'ep.edges.jsonl',[{'edge_id':'e0','from_node':'n0','to_node':'n1','length_m':1.0,'width_m':2.25,'slope':None,'cross_slope':None,'surface':None,'curb_ramp':None,'step_free':None,'lighting':None,'shelter':None,'source':'official_gis'}])
    (inp/'ep.meta.json').write_text('{}')
    allow=tmp_path/'allow.txt'; allow.write_text('ep\n')
    report=tmp_path/'r.json'
    subprocess.check_call([sys.executable,str(ROOT/'scripts/build_hybrid_accessibility_overlay.py'),
        '--input_graph_dir',str(inp),'--output_graph_dir',str(out),'--city','vegas','--split','test','--episode_allowlist',str(allow),'--report_json',str(report)],cwd=ROOT)
    row=json.loads((out/'ep.edges.jsonl').read_text().splitlines()[0])
    assert row['width_m']==2.25
    assert row['metadata']['field_provenance']['width_m']['method']=='preexisting_graph_attribute'
    assert row['metadata']['field_provenance']['slope']['kind']=='simulated'
    assert row['metadata']['paper_claim_allowed'] is False

def test_environment_clearance_is_compared_to_vehicle_requirement():
    anchor=PUDOAnchor('a','ep','pickup_dropoff',Pose2D(0,0,0),Pose2D(0,0,0),'right',True,
        adjacent_ped_node_id='n',curb_height_m=0.01,sidewalk_width_m=2.0,deployment_clearance_m=1.0,
        deployment_clearance_semantics='available_environment_clear_space',source='hybrid')
    vehicle=VehicleInterface('v','ep',door_side='right',ramp=True,deployment_clearance_m=1.5)
    tg=TransitionGenerator()
    ok,reasons=tg._interface_valid(anchor,vehicle)
    assert not ok and 'insufficient_environment_deployment_clearance' in reasons
    ev=next(e for e in tg._interface_evidence(anchor,vehicle,'board') if e.resource_name=='deployment_clearance_m')
    assert ev.observed==1.0 and ev.required==1.5 and ev.value==1.0
