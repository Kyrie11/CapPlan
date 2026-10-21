"""Science v11 must make the V10 interaction falsifiable, not protect it."""
from __future__ import annotations
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
from capplan.handoff.commitment_model_v10 import FiniteCommitmentModel, GenericSupportSolver
from capplan.handoff.window_intersection_v11 import check_option
from scripts.run_handoff_h4_robustness_v11 import evaluate_factorial
from scripts.audit_handoff_all_alternatives_v8 import evaluate

ROOT=Path(__file__).resolve().parents[1]
SCENES=[json.loads(line) for line in (ROOT/'examples'/'handoff_finite_v10.jsonl').read_text().splitlines()]


def test_v10_interaction_does_not_survive_parallel_controls_or_one_wait_tick():
    rows,summary=evaluate_factorial(SCENES)
    assert len(rows)==32
    p={(b['max_wait_ticks'],b['allow_parallel_control_actions']):b for b in summary['protocols']}
    assert p[0,False]['winning_cells']==7
    assert p[0,False]['three_way_binary_contrast']==-1
    assert p[0,True]['winning_cells']==8
    assert p[0,True]['three_way_binary_contrast']==0
    assert p[1,False]['winning_cells']==8
    assert p[1,False]['three_way_binary_contrast']==0
    assert all(b['fully_explored'] for b in summary['protocols'])
    assert all(r['all_world_replay_pass'] is not False for r in rows)


def test_parallel_command_updates_both_agents_one_tick_without_peeking():
    s=deepcopy(next(x for x in SCENES if x['case_id']=='factor_w1.2_e1_vA'))
    s['allow_parallel_control_actions']=True
    m=FiniteCommitmentModel(s)
    assert 'guide:B|vehicle:B' in m.actions(m.initial)
    nxt,cost=m.successors(m.initial,'guide:B|vehicle:B')
    state=nxt[0][1]
    assert state.tick==1
    assert state.passenger=='J'
    assert state.vehicle=='B'
    assert state.walked==1
    assert state.support==m.initial.support  # neither action observes hidden world
    assert cost>0
    assert GenericSupportSolver(m).solve().status=='WIN'
    old=FiniteCommitmentModel(dict(s,allow_parallel_control_actions=False))
    assert 'guide:B|vehicle:B' not in old.actions(old.initial)
    assert GenericSupportSolver(old).solve().status=='LOSE'


def test_directed_mid_edge_reversal_cannot_be_invented():
    edge=lambda u,v,l:SimpleNamespace(from_node=u,to_node=v,length_m=l,
        obstacle=False,obstacle_state=None,step_free=True,width_m=2.,slope=0.,
        cross_slope=0.,confidence=1.,crossing_type='sidewalk')
    graph=SimpleNamespace(nodes=[SimpleNamespace(node_id=z) for z in ('O','M','A','B')],
                          edges=[edge('O','M',10.),edge('M','A',10.),edge('O','B',2.)])
    pair={'episode_id':'e','a_anchor':'A','b_anchor':'B',
          'distances_m':{'origin_to_a':20.},'reroute_total_m':100.,
          'screening_contract':{'distance_budget_m':20.,'already_consumed_m':0.,
                                'min_width_m':1.,'max_slope':.2,'max_cross_slope':.2},
          'paths':{'origin_to_a':{'node_ids':['O','M','A'],
                                  'edges':[{'length_m':10.},{'length_m':10.}]}}}
    anchors=[{'anchor_id':'B','kind':'pickup','legal_stop':True,'blockage_risk':0.,
              'adjacent_ped_node_id':'B','hybrid_physical_site_key':'another'}]
    optimistic=evaluate(pair,anchors,graph,5,speed_mps=1.,ack_s=0,require_curb=False)
    directed=evaluate(pair,anchors,graph,5,speed_mps=1.,ack_s=0,require_curb=False,respect_direction=True)
    assert optimistic['B_graph_feasible']
    assert not directed['B_graph_feasible']
    assert directed['pedestrian_direction_mode'].startswith('DIRECTED')


def test_time_overlap_has_multiple_windows_and_unknown_is_not_infeasible():
    packet=json.loads((ROOT/'examples'/'handoff_time_windows_v11.jsonl').read_text().splitlines()[0])
    a=check_option(packet)
    assert a['status']=='TEMPORAL_WITNESS_UNDER_ASSERTED_INPUTS'
    assert len(a['possible_start_windows_s'])==2
    assert not a['can_be_claimed_as_real_vehicle_boarding']
    unk=check_option({'option_id':'unreviewed'})
    assert unk['status']=='UNKNOWN_MISSING_INPUT'
    b=check_option(json.loads((ROOT/'examples'/'handoff_time_windows_v11.jsonl').read_text().splitlines()[1]))
    assert b['status']=='NO_TEMPORAL_OVERLAP_UNDER_ASSERTED_INPUTS'
