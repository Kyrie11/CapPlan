"""Regression gates for schema plumbing, known observation schedule, and packaging."""
from __future__ import annotations
import importlib.util,json,tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
def script(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/f'{name}.py')
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_fixture_graph_with_adjacent_ped_node_id():
    fixture=script('build_handoff_smoke_fixture')
    miner=script('mine_handoff_recourse_pairs')
    from capplan.data.accessibility_layer import load_accessibility_graph
    with tempfile.TemporaryDirectory() as folder:
        fixture.create(folder,1)
        path=Path(folder)
        episode=json.loads((path/'episodes.jsonl').read_text().strip())
        anchors=[json.loads(s) for s in (path/'pudo_anchors.jsonl').read_text().splitlines()]
        args=SimpleNamespace(distance_budget_m=200,already_consumed_m=0,min_width_m=.9,
             max_slope=.08,max_cross_slope=.04,min_confidence=0,require_step_free=True,
             require_interface_complete=True,require_explicit_curb_connector=True,event_time_s=90)
        items,counts=miner.mine_episode(episode,anchors,load_accessibility_graph(path,episode['episode_id']),args,{})
        assert len(items)==1
        assert items[0]['pedestrian_graph_nodes']['A']=='ped_A'
        assert items[0]['explicit_curb_connector_evidence']['A']
        assert items[0]['global_no_recovery_claim'] is False


def test_early_vs_privileged_scheduling_never_reveals_future():
    from capplan.handoff.finite_oracle import FiniteHandoffOracle,evaluate_baselines
    b=script('build_handoff_temporal_overlays')
    pair={'episode_id':'fixture','origin_anchor':'O','a_anchor':'A','b_anchor':'B',
          'distances_m':{'origin_to_a':130,'origin_to_b':170,'a_to_b':120},
          'screening_contract':{'distance_budget_m':200,'already_consumed_m':0}}
    early=b.build_overlay(pair,0,90,5,3,.5,0,'fixed',15,1,'immediate')
    upper=b.build_overlay(pair,0,90,5,3,.5,0,'fixed',15,1,'event_aligned_upper_bound')
    assert early['observation_model']['scheduled_observation_request_s']==0
    assert upper['observation_model']['scheduled_observation_request_s']==90
    a=FiniteHandoffOracle(early)
    assert a.status('a_fails_after_commitment','A',a.verify_time(a.initial))
    assert evaluate_baselines(a)['verify_then_guide']['weighted_candidate_success_rate']==.5
    assert evaluate_baselines(FiniteHandoffOracle(upper))['verify_then_guide']['weighted_candidate_success_rate']==1
    assert upper['provenance']['privileged_schedule_upper_bound'] is True


def test_pack_manifest_is_bounded():
    p=script('pack_handoff_results')
    with tempfile.TemporaryDirectory() as tmp:
        base=Path(tmp);result=base/'result';(result/'mining').mkdir(parents=True)
        (result/'mining'/'mining_audit.json').write_text('{"selected_episodes":5}')
        (result/'mining'/'recourse_pairs.jsonl').write_text(''.join('{"id":%s}\n'%i for i in range(8)))
        target=base/'out.zip'
        m=p.pack(result,target,examples=2)
        assert m['files']['mining/recourse_pairs.jsonl']['truncated']
        import zipfile
        with zipfile.ZipFile(target) as z:
            assert len(z.read('mining/recourse_pairs.jsonl').splitlines())==2
