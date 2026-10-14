"""Optional *audited-only* curb interface augmentation for the handoff miner.

The original hybrid dataset is never edited. A sidecar may add a new curb
endpoint and an explicit pedestrian->curb edge only when a separately reviewed
record provides coordinates, geometry, measurements and supporting source.
Passing schema checks is NOT the same as independently confirming a site.
"""
from __future__ import annotations
import hashlib
import math
from copy import deepcopy
from capplan.data.schemas import AccessibilityEdge, AccessibilityNode

MANDATORY = ('episode_id','anchor_id','ped_node_id','physical_site_key',
             'curb_x','curb_y','connector_length_m','width_m','slope','cross_slope',
             'step_free','curb_ramp','evidence_basis','source_reference',
             'reviewer_id','reviewed_at','review_decision')


def apply_reviewed_connectors(episode, anchors, graph, records):
    """Return a modified in-memory graph/anchors plus audit counts.

    Prevents silently invented connector geometry and physical-site mismatches.
    Reports invalid records by raising ValueError rather than cherry-picking
    only ones that happen to yield a feasible route.
    """
    if not records:
        return anchors, graph, {'review_records_applied':0}
    graph=deepcopy(graph)
    anchors=deepcopy(anchors)
    graph_nodes={n.node_id:n for n in graph.nodes}
    by_anchor={a['anchor_id']:a for a in anchors}
    used=set();count=0
    for rec in records:
        missing=[k for k in MANDATORY if rec.get(k) in (None,'')]
        if missing: raise ValueError(f'audited connector missing {missing}')
        if rec['episode_id']!=episode['episode_id']:
            raise ValueError('reviewed connector episode mismatch')
        if rec['anchor_id'] in used:
            raise ValueError('duplicate reviewed connector anchor')
        used.add(rec['anchor_id'])
        anchor=by_anchor.get(rec['anchor_id'])
        if anchor is None:raise ValueError('reviewed connector unknown anchor')
        if str(anchor.get('hybrid_physical_site_key'))!=str(rec['physical_site_key']):
            raise ValueError('reviewed physical-site key mismatch')
        if (anchor.get('adjacent_ped_node_id') or anchor['anchor_id'])!=rec['ped_node_id']:
            raise ValueError('reviewed connector pedestrian node mismatch')
        if rec['ped_node_id'] not in graph_nodes:
            raise ValueError('reviewed connector pedestrian node absent from graph')
        if rec['review_decision']!='PASS' or rec['evidence_basis'] not in ('observed','derived_from_authoritative_geometry'):
            raise ValueError('no approved independent evidence for curb connector')
        if not rec['source_reference'] or not rec['reviewer_id'] or not rec['reviewed_at']:
            raise ValueError('provenance incomplete')
        vals=[float(rec[k]) for k in ('curb_x','curb_y','connector_length_m','width_m','slope','cross_slope')]
        if not all(math.isfinite(x) for x in vals):
            raise ValueError('nonfinite reviewed curb measurement')
        cx,cy,dist,width,slope,cross=vals
        ped=graph_nodes[rec['ped_node_id']]
        straight=math.hypot(ped.x-cx,ped.y-cy)
        if not (0.05<=dist<=60 and 0<width<=10 and abs(slope)<=1 and abs(cross)<=1):
            raise ValueError('reviewed curb measurements physically implausible')
        if dist + 0.5 < straight:
            raise ValueError('reviewed connector length shorter than endpoint separation')
        curb=anchor.get('curb_pose') or {}
        if curb and all(x in curb for x in ('x','y')):
            if math.hypot(cx-float(curb['x']),cy-float(curb['y']))>2.0:
                raise ValueError('reviewed interface position differs from PUDO curb pose')
        if type(rec['step_free']) is not bool or type(rec['curb_ramp']) is not bool:
            raise ValueError('step-free and curb-ramp must be explicit reviewed booleans')
        code=hashlib.sha256((str(rec['physical_site_key'])+'|'+str(rec['anchor_id'])).encode()).hexdigest()[:16]
        nid='audited_curb:'+code
        if nid in graph_nodes: raise ValueError('audited curb node id collision')
        graph.nodes.append(AccessibilityNode(nid,cx,cy,'audited_curb_interface',
                            confidence=float(rec.get('confidence',1.0)),
                            source=str(rec['source_reference'])))
        graph.edges.append(AccessibilityEdge('reviewed_curb:'+code,rec['ped_node_id'],nid,
                            dist,width,slope,cross,rec.get('surface'),rec['curb_ramp'],
                            rec['step_free'],False,None,None,
                            confidence=float(rec.get('confidence',1.0)),
                            geometry=[[ped.x,ped.y],[cx,cy]],crossing_type='curb',
                            source='externally_reviewed_curb_connector',
                            metadata={'evidence_basis':rec['evidence_basis'],
                                      'source_reference':rec['source_reference'],
                                      'reviewer_id':rec['reviewer_id'],
                                      'reviewed_at':rec['reviewed_at'],
                                      'reviewed_record_not_independent_ground_truth':True}))
        anchor['adjacent_ped_node_id']=nid
        graph_nodes[nid]=graph.nodes[-1]
        count+=1
    graph.metadata={**graph.metadata,'reviewed_curb_connectors_added':count,
                    'unmodified_base_dataset':True}
    return anchors,graph,{'review_records_applied':count}
