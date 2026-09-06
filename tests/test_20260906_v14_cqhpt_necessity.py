from __future__ import annotations

from pathlib import Path
import tempfile
import torch

from capplan.data.schemas import (
    AccessibilityEdge, AccessibilityGraph, AccessibilityNode, CandidateTransition,
    PUDOAnchor, Pose2D, TransitionTests, VehicleInterface,
)
from capplan.models.cqhpt import CQHPTConfig, CQHPTGuide, CQHPTModel, save_cqhpt_checkpoint
from capplan.models.cqhpt_features import (
    build_raw_evidence_tokens, cqhpt_query_feature_names, RAW_TOKEN_DIM,
)
from capplan.planning.planner import CapPlanPlanner, PlannerConfig


def _scene_objects():
    nodes=[AccessibilityNode('n0',0,0,'sidewalk'),AccessibilityNode('n1',10,0,'sidewalk')]
    edge=AccessibilityEdge('e0','n0','n1',10.0,width_m=0.7,slope=0.32,cross_slope=0.08,
                           curb_ramp=False,step_free=False,geometry=[[0,0],[10,0]],source='derived_dem')
    graph=AccessibilityGraph('ep',nodes,[edge])
    p=PUDOAnchor('p0','ep','pickup_dropoff',Pose2D(10,0),Pose2D(10,1),'right',True,
                 adjacent_ped_node_id='n1',map_confidence=0.9,dynamic_confidence=0.8)
    v=VehicleInterface('v','ep',door_side='right',ramp=True,low_floor=True)
    t=CandidateTransition('t0','ep','o','p0','origin','access','access',[],1.0,0.9,{}, {},1.0,
                          tests=TransitionTests(),metadata={'path_edge_ids':['e0'],'adjacent_ped_node_id':'n1'})
    return graph,p,v,t


def test_v14_raw_evidence_does_not_leak_processed_path_resources():
    graph,p,v,t=_scene_objects()
    a=build_raw_evidence_tokens(transition=t,graph=graph,pudos=[p],vehicle=v,scene={})
    # Change verifier-level processed resources only; lower-level geometry/source
    # is unchanged, so the raw-evidence branch must remain identical.
    graph.edges[0].width_m=4.2; graph.edges[0].slope=0.01; graph.edges[0].cross_slope=0.0
    graph.edges[0].curb_ramp=True; graph.edges[0].step_free=True
    b=build_raw_evidence_tokens(transition=t,graph=graph,pudos=[p],vehicle=v,scene={})
    assert [(x['type_id'],x['raw'],x['rel_xy']) for x in a] == [(x['type_id'],x['raw'],x['rel_xy']) for x in b]


def test_v14_neutral_query_control_masks_passenger_specific_fields_exactly():
    torch.manual_seed(2)
    cfg=CQHPTConfig(hidden_dim=32,num_heads=4,token_layers=1,mode='neutral_query')
    model=CQHPTModel(cfg).eval()
    q=torch.zeros(2,cfg.query_dim)
    q[:,0]=1.0; q[:,len(cqhpt_query_feature_names())-1]=torch.tensor([0.0,3.0])
    base_raw=torch.randn(1,3,RAW_TOKEN_DIM); raw=base_raw.repeat(2,1,1); typ=torch.zeros(2,3,dtype=torch.long); rel=torch.zeros(2,3,2); mask=torch.ones(2,3,dtype=torch.bool)
    with torch.inference_mode(): scores,_=model(q,raw,typ,rel,mask)
    assert torch.allclose(scores[0],scores[1],atol=1e-7,rtol=0)


def test_v14_cqhpt_checkpoint_and_request_context_score_successors():
    graph,p,v,t=_scene_objects()
    cfg=CQHPTConfig(hidden_dim=32,num_heads=4,token_layers=1,mode='full')
    model=CQHPTModel(cfg)
    with tempfile.TemporaryDirectory() as td:
        ck=Path(td)/'checkpoint.pt'; save_cqhpt_checkpoint(ck,model)
        guide=CQHPTGuide(ck,device='cpu')
        guide.prepare_request(graph=graph,pudos=[p],vehicle=v,scene={},transitions=[t])
        class L:
            phase='access'; resource_ledger={}; history=[]
        class C:
            clauses=[]; groups=[]
        scores=guide.score_successors([(L(),t)],C(),None)
        assert len(scores)==1 and torch.isfinite(torch.tensor(scores[0]))
        d=guide.diagnostics(); assert d['cqhpt_scored_successors']==1


def test_v14_planner_freezes_exact_sncpk_and_disables_v12_v13_replay_optimizers():
    p=CapPlanPlanner(PlannerConfig(algorithm_version='V14',evidence_grounded_runtime=True,no_cqhpt=True))
    assert p.searcher.config.use_semnaive_projected_acceptance_kernel
    assert p.searcher.config.capability_projection
    assert p.searcher.config.packed_frontier_dominance
    assert p.diagnostic_searcher is not None
    assert not p._shared_diagnostic_semantic_cache_enabled
    assert not p._compiled_diagnostic_transition_program_enabled
    assert p.searcher.config.lambda_learned_feasibility == 0.0
    assert p.searcher.config.lambda_edge_validity == 0.0


def test_v14_legacy_static_guidance_is_only_a_control():
    p=CapPlanPlanner(PlannerConfig(algorithm_version='V14',evidence_grounded_runtime=True,no_cqhpt=True,v14_legacy_static_guidance=True))
    assert p.searcher.config.lambda_learned_feasibility > 0.0
    assert p.searcher.config.lambda_edge_validity > 0.0
    assert p.searcher.frontier_ranker is None


def test_v14_late_fusion_uses_identical_neutral_routing_but_capability_aware_head():
    torch.manual_seed(7)
    cfg=CQHPTConfig(hidden_dim=32,num_heads=4,token_layers=1,mode='late_fusion')
    model=CQHPTModel(cfg).eval()
    q=torch.zeros(2,cfg.query_dim)
    q[:,0]=1.0
    # Change only one passenger-capability field.
    q[1,-1]=2.5
    raw=torch.randn(1,4,RAW_TOKEN_DIM).repeat(2,1,1)
    typ=torch.tensor([[0,1,3,4],[0,1,3,4]],dtype=torch.long)
    rel=torch.randn(1,4,2).repeat(2,1,1)
    mask=torch.ones(2,4,dtype=torch.bool)
    with torch.inference_mode():
        late_score,late_aux=model(q,raw,typ,rel,mask,mode='late_fusion')
        full_score,full_aux=model(q,raw,typ,rel,mask,mode='full')
    # Late fusion routes evidence with a passenger-neutral query, therefore the
    # attention map/context is invariant to the capability-only change.
    assert torch.allclose(late_aux['attention'][0],late_aux['attention'][1],atol=1e-7,rtol=0)
    assert torch.allclose(late_aux['context_embedding'][0],late_aux['context_embedding'][1],atol=1e-7,rtol=0)
    # Capability can still affect the late scoring head, while full CQ-HPT is
    # allowed to alter evidence routing itself.
    assert not torch.allclose(late_score[0],late_score[1],atol=1e-7,rtol=0)
    assert not torch.allclose(full_aux['attention'][0],full_aux['attention'][1],atol=1e-7,rtol=0)
