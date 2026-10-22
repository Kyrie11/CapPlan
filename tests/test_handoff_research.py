"""Stable handoff research tests: no CARLA client or private nuPlan DB required."""
import copy
import json
from pathlib import Path
from types import SimpleNamespace
import pytest

from capplan.handoff.research import (
    inspect_graph_pair, select_pairs, classify_carla_triplet, _read_jsonl,
)
from scripts.handoff_study import main


def edge(u, v, length, label):
    return SimpleNamespace(from_node=u, to_node=v, length_m=length, edge_id=label,
       obstacle=False, obstacle_state='clear', step_free=True, width_m=2.,
       slope=0., cross_slope=0., confidence=1.)


def pair():
    return {'episode_id': 'one', 'map_name':'us-nv-las-vegas-strip',
            'a_anchor':'A','b_anchor':'B',
            'pedestrian_graph_nodes':{'origin':'O','A':'A','B':'B'},
            'physical_site_pair':['site-A','site-B'],
            'screening_contract':{'min_width_m':1,'max_slope':.1,
              'max_cross_slope':.1,'require_step_free':True,'min_confidence':0,
              'distance_budget_m':160,'already_consumed_m':0},
            'distances_m':{'origin_to_a':100,'origin_to_b':130,'a_to_b':200},
            # The historical path had P->O reversed; must not be trusted.
            'paths':{'origin_to_a':{'node_ids':['O','P','A'],
                'edges':[{'edge_id':'op','length_m':50},{'edge_id':'pa','length_m':50}]}}}


def test_fresh_directional_paths_recomputed_despite_old_reverse_witness():
    g=SimpleNamespace(edges=[edge('O','P',50,'op'),edge('P','A',50,'pa'),
                              edge('O','B',130,'ob'),edge('P','B',70,'pb'),
                              edge('A','B',200,'ab')])
    p=pair()
    r=inspect_graph_pair(p,g,[20,90],speed_mps=1,ack_s=0)
    assert r['status']=='DIRECTED_TERMINAL_AB_ASYMMETRY_EXISTS'
    assert r['path_O_A_node_ids']==['O','P','A']
    assert r['midroute'][0]['B_within_distance_budget']
    assert not r['midroute'][1]['B_within_distance_budget']
    assert r['real_physical_handoff_verified'] is False


def test_only_reverse_arc_encoded_yields_explicit_invalidity_and_optimistic_sensitivity():
    g=SimpleNamespace(edges=[edge('P','O',50,'op'),edge('P','A',50,'pa'),
                              edge('O','B',130,'ob'),edge('A','B',200,'ab')])
    r=inspect_graph_pair(pair(),g,[0],speed_mps=1,ack_s=0, directed=True)
    assert r['status']=='NO_TWO_DIRECT_PATHS_UNDER_ENCODING'
    r2=inspect_graph_pair(pair(),g,[0],speed_mps=1,ack_s=0,directed=False)
    assert r2['status']=='DIRECTED_TERMINAL_AB_ASYMMETRY_EXISTS'
    assert r2['direction_mode']=='OPTIMISTIC_SYMMETRIZED'


def test_third_pudo_prevents_global_stranding_claim():
    g=SimpleNamespace(edges=[edge('O','P',50,'op'),edge('P','A',50,'pa'),
                              edge('O','B',130,'ob'),edge('P','B',70,'pb'),
                              edge('A','B',200,'ab'),edge('P','C',20,'pc')])
    others=[{'anchor_id':'C','adjacent_ped_node_id':'C','legal_stop':True,'kind':'pickup'}]
    r=inspect_graph_pair(pair(),g,[90],speed_mps=1,ack_s=0,other_anchors=others)
    assert not r['midroute'][0]['B_within_distance_budget']
    assert r['midroute'][0]['third_graph_feasible_count']==0 # at P->A no reverse
    reverse=SimpleNamespace(edges=g.edges+[edge('A','P',50,'ap')])
    result=inspect_graph_pair(pair(),reverse,[90],speed_mps=1,ack_s=0,other_anchors=others)
    assert result['midroute'][0]['third_graph_feasible_count']==1
    assert not result['real_physical_handoff_verified']


def test_delay_consumes_distance_not_reset():
    g=SimpleNamespace(edges=[edge('O','P',50,'op'),edge('P','A',50,'pa'),
                              edge('O','B',130,'ob'),edge('P','B',70,'pb'),
                              edge('A','B',200,'ab')])
    early=inspect_graph_pair(pair(),g,[20],speed_mps=1,ack_s=0)
    delayed=inspect_graph_pair(pair(),g,[20],speed_mps=1,ack_s=35)
    assert early['midroute'][0]['B_within_distance_budget']
    assert not delayed['midroute'][0]['B_within_distance_budget']
    assert delayed['midroute'][0]['distance_consumed_m']>early['midroute'][0]['distance_consumed_m']


def test_selected_pairs_deduplicated_by_physical_site_and_bounded_memory(tmp_path):
    src=tmp_path/'pairs.jsonl'
    with src.open('w') as f:
        for j in range(100):
            p=pair();p['episode_id']=f'ep{j}'
            p['physical_site_pair']=['site-'+str(j//20),'B']
            f.write(json.dumps(p)+'\n')
    a=select_pairs(src,per_city=3)
    b=select_pairs(src,per_city=3)
    assert len(a)==3
    assert [v['episode_id'] for v in a] == [v['episode_id'] for v in b]
    assert len({tuple(sorted(v['physical_site_pair'])) for v in a})==3


def write_report(root,mode,goal,*,target_B=False,redirect_flag=None):
    p=root/mode/'trial0';p.mkdir(parents=True)
    cfg={'mode':mode,'map':'Town03','seed':13,'a_steps':2,'b_steps':8,'spawn_index':0,
      'service_stop_hold_s':2.,'stop_speed_mps':.5, 'blocker_blueprint':'vehicle.tesla.model3',
      'redirect_at_s':8}
    rows=[{'elapsed_s':t,'distance_to_goal_m':3,'speed_mps':0,
           'target':('B' if target_B and t>=1 else 'A')} for t in [0,1,2,3]]
    (p/'trajectory.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    d={'mode':mode,'goal':goal,'carla_map':'Carla/Maps/Town03','seed':13,
       'status':'COMPLETED','collisions':0,'experiment_config':cfg,
       'geometry_parameters':{'arrival_radius_m':6,'spawn_index':0,'a_steps':2,'b_steps':8},
       'carla_locations':{'start':[0,0,0],'A':[10,0,0],'B':[30,0,0]},
       'service_stop_hold_s_required':2,'stop_speed_threshold_mps':.5,'sample_dt_s':1}
    if redirect_flag is not None: d['redirect_command_applied']=redirect_flag
    (p/'execution_report.json').write_text(json.dumps(d))


def test_old_redirect_A_completion_not_counted_as_B(tmp_path):
    write_report(tmp_path,'clear_A','A')
    write_report(tmp_path,'blocked_A','A')
    write_report(tmp_path,'redirect_B','A',target_B=False)
    d=classify_carla_triplet(tmp_path)
    assert d['counts']['clear_A_stop_proxy']==1
    assert d['counts']['redirect_B_stop_proxy']==0
    assert d['trials'][0]['redirect_command_executed'] is False


def test_new_redirect_requires_tracked_B_and_goal_B(tmp_path):
    write_report(tmp_path,'clear_A','A')
    write_report(tmp_path,'blocked_A','A')
    write_report(tmp_path,'redirect_B','B',target_B=True,redirect_flag=True)
    d=classify_carla_triplet(tmp_path)
    assert d['counts']['redirect_command_executed']==1
    assert d['counts']['redirect_B_stop_proxy']==1
    assert d['counts']['matched_control_configuration']==1


def test_legacy_planner_does_not_halt_early_when_redirect_pending():
    text=(Path(__file__).resolve().parents[1]/'scripts/run_carla_controlled_handoff_v5.py').read_text()
    # Regression guard until the CARLA agent is isolated behind a mockable API.
    assert 'redirect_command_applied' in text
    assert 'redirect_applied' in text
    assert "if args.mode in ('blocked_A_redirect_B','clear_A_redirect_B') and not redirect_applied" in text
    assert 'braking_to_stop=False;stopped_dwell_s=0.0;best_dwell_s=0.0' in text


def test_stable_cli_temporal_check(tmp_path):
    root=Path(__file__).resolve().parents[1]
    output=tmp_path/'out.jsonl'
    code=main(['temporal-check','--cases',str(root/'examples/handoff_temporal_v12.jsonl'),
                  '--output',str(output)])
    assert code==0
    d=json.loads(output.with_suffix('.summary.json').read_text())
    assert d['n']==5 and d['mismatch_count']==0
    assert d['published_pomdp_shield_reproduced'] is False


def test_independent_physical_packet_cannot_auto_upgrade_unknown(tmp_path):
    from capplan.handoff.research import site_attestation_audit
    p=tmp_path/'pending.jsonl'
    fields=['directed_pedestrian_path','legal_stop','vehicle_trajectory_stop_pose',
            'deployment_region','dynamic_observation']
    packet={'option_id':'z','attestations':{f:{'review_status':'UNKNOWN','independent_source_ref':None}
                                         for f in fields}}
    p.write_text(json.dumps(packet)+'\n')
    r=site_attestation_audit(p)
    assert r['status_counts']['UNKNOWN_MISSING_EVIDENCE']==1
    assert r['verified_real_handoff_count']==0
    assert r['missing_field_counts']['legal_stop']==1


def test_legacy_inventory_never_deletes_scripts():
    from capplan.handoff.research import legacy_research_inventory
    root=Path(__file__).resolve().parents[1]
    r=legacy_research_inventory(root)
    assert r['versioned_script_count']>=40
    assert r['no_files_deleted']
    assert all(x['migration_policy']=='MANUAL_REVIEW_REQUIRED_DO_NOT_AUTO_DELETE'
               for x in r['records'])
