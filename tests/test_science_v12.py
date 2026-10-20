"""Science V12 correctness, missing-evidence semantics and heuristic anti-leak tests."""
from __future__ import annotations
from pathlib import Path
import json
from scripts.audit_science_v12 import audit
from scripts.audit_anchor_provenance_v12 import classify_anchor,audit as audit_prov
from scripts.benchmark_handoff_heuristics_v12 import study
from scripts.fetch_public_arcgis_v12 import validate_bbox,to_feature
ROOT=Path(__file__).resolve().parents[1]

def mkrow(episode,t=0,strict=0,optimistic=2):
    def field(k):return {'graph_candidate_count':k,'anchors':[{'anchor_id':str(i)} for i in range(k)],
       'filter_reasons':{'no_explicit_valid_typed_curb_edge':2 if k==0 else 0}}
    return {'episode_id':episode,'a_anchor':episode+'A','b_anchor':episode+'B',
        'physical_site_pair':[episode+'|a',episode+'|b'],'notice_issued_at_s':t,
        'strict':field(strict),'optimistic':field(optimistic)}

def test_cluster_denominators_and_unknown():
    r=audit([mkrow('e1',0),mkrow('e1',30),mkrow('e2',0)])
    assert r['counts']['independent_nuplan_episodes']==2
    assert r['counts']['episode_and_physical_site_pair_clusters']==2
    assert r['counts']['number_time_rows']==3
    assert r['claim_gates']['true_physical_handoff_infeasibility']=='NOT_ESTABLISHED'

def test_reject_inconsistent_candidate_counts():
    bad=mkrow('e1');bad['strict']['graph_candidate_count']=1
    try:audit([bad])
    except ValueError:pass
    else:assert False,'must reject truncated/inconsistent candidates'

def test_missing_fields_not_certified():
    a={'anchor_id':'x','episode_id':'e','field_provenance':{'sidewalk_width_m':'measured'},'sidewalk_width_m':None,
       'truth_mode':'simulated'}
    c=classify_anchor(a)
    assert c['sidewalk_width_m']=='missing'
    assert c['curb_height_m']=='missing'
    assert c['side']=='missing'

def test_provenance_stream_and_review(tmp_path):
    p=tmp_path/'pudo_anchors.jsonl';p.write_text(json.dumps({'episode_id':'e','anchor_id':'a',
         'hybrid_physical_site_key':'vegas|site0','legal_stop':True,'truth_mode':'simulated'})+'\n')
    csv=tmp_path/'review.csv';out=audit_prov([str(p)],str(csv))
    assert out['counts']['anchors_total']==1
    assert out['provenance_by_field']['legal_stop']['simulated']==1
    assert out['provenance_by_field']['curb_height_m']['missing']==1
    assert out['audit_sample_size']==1

def test_finite_baseline_does_not_claim_real_world():
    cs=[]
    for line in (ROOT/'examples/handoff_finite_v10.jsonl').read_text().splitlines():
        c=json.loads(line)
        if c['case_id']=='fixed_safe_B':cs=[c];break
    rows,summary=study(cs,max_states=5000,depths=(2,))
    assert summary['cases']==1
    assert rows[0]['oracle_exact']
    assert 'robust_depth_2' in rows[0]['baseline']
    assert 'synthetic' in summary['interpretation']

def test_public_source_bbox_geojson_guard():
    assert validate_bbox([-115.2,36.1,-115.0,36.3])==[-115.2,36.1,-115.0,36.3]
    f=to_feature({'geometry':{'x':-115.,'y':36.},'attributes':{'foo':'bar'}})
    assert f['geometry']['coordinates']==[-115.,36.]
    try:validate_bbox([-115.0,36.0,-115.2,36.3])
    except ValueError:pass
    else:assert False
