"""Science V13: tri-valued, provenance-aware LOCAL PUDO interface screen.

No continuous motion, pedestrian route, or field truth is silently certified.
The declared benchmark and independent-observation tracks are intentionally
separate. This is an audit of input candidates, NOT a passenger-complete oracle.
"""
from __future__ import annotations
from collections import Counter
from typing import Any
import math

PASS, FAIL, UNKNOWN = 'PASS', 'FAIL', 'UNKNOWN'


def conj(*values: str) -> str:
    if FAIL in values: return FAIL
    if all(x == PASS for x in values): return PASS
    return UNKNOWN


def indicator(v: Any) -> str:
    if v is True: return PASS
    if v is False: return FAIL
    return UNKNOWN


def numeric_ge(v: Any, threshold: float) -> str:
    if isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v):
        return UNKNOWN
    return PASS if v >= threshold else FAIL


def numeric_le(v: Any, threshold: float) -> str:
    if isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v):
        return UNKNOWN
    return PASS if v <= threshold else FAIL


def provenance_kind(anchor: dict, key: str) -> str:
    """Evidence kind may be at field_provenance[key].kind, not .source.

    'observed' means *claimed observed*; it is not verified independently.
    """
    p = anchor.get('field_provenance', {})
    p = p.get(key) if isinstance(p, dict) else None
    parts=[]
    if isinstance(p, dict):
        for field in ('kind', 'provenance', 'evidence_kind', 'source', 'note'):
            if field in p and p[field] is not None: parts.append(str(p[field]))
    elif p is not None:parts.append(str(p))
    if not parts:
        return 'unlabeled'
    s=' '.join(parts).lower()
    if any(t in s for t in ('simulat', 'synthetic', 'bootstrap')):return 'simulated'
    if any(t in s for t in ('derived','proxy','inferred','imputed')):return 'derived_or_proxy'
    if any(t in s for t in ('observed','measured','survey','audited','municipal','official')):
        return 'observed_claim_unverified'
    return 'other_or_unlabeled'


def local_predicates(anchor: dict, *, min_width=1.2, min_clearance=1.2,
                     max_curb=0.05, max_blockage=0.85) -> dict:
    """Simplified *screening thresholds*, not disability-specific legal standards."""
    a=anchor
    pedestrian=conj(numeric_ge(a.get('sidewalk_width_m'),min_width),
                    indicator(a.get('curb_ramp')),
                    numeric_le(a.get('curb_height_m'),max_curb))
    vehicle=conj(indicator(a.get('legal_stop')),
                 numeric_le(a.get('blockage_risk'),max_blockage-1e-10))
    interface=numeric_ge(a.get('deployment_clearance_m'),min_clearance)
    # External continuous motion/vehicle pose/boarding is never present as a
    # trusted boolean oracle in this field audit.
    local_joint=conj(pedestrian,vehicle,interface)
    fields=('sidewalk_width_m','curb_ramp','curb_height_m','legal_stop',
            'blockage_risk','deployment_clearance_m','stop_pose')
    return {'pedestrian':pedestrian,'vehicle':vehicle,'interface':interface,
            'declared_local_joint':local_joint,
            'certified_full_handoff':UNKNOWN,
            'field_provenance':{k:provenance_kind(a,k) for k in fields},
            'limitations':['no route-to-curb reachability proof','no independently verified stop legality',
                           'no continuous vehicle motion checker','no independent boarding geometry']}


def episode_signature(anchors: list[dict], **kwargs) -> dict:
    states=[local_predicates(a,**kwargs) for a in anchors]
    counts={k:dict(Counter(s[k] for s in states)) for k in
            ('pedestrian','vehicle','interface','declared_local_joint')}
    possible_p=any(s['pedestrian']==PASS for s in states)
    possible_v=any(s['vehicle']==PASS for s in states)
    possible_i=any(s['interface']==PASS for s in states)
    joint=any(s['declared_local_joint']==PASS for s in states)
    # Existential feasibility in different sites cannot be composed into one
    # service. Demonstrates a structural screen, not a real-world failure.
    independent_existential_gap=possible_p and possible_v and possible_i and not joint
    return {'anchors':len(anchors),'predicates':counts,
            'independent_component_feasible':{'passenger':possible_p,'vehicle':possible_v,'interface':possible_i},
            'declared_local_joint_exists':joint,
            'independent_existential_gap':independent_existential_gap,
            'full_physical_service_certified':False,
            'truth_level':'HYBRID_FIELD_SCREEN_ONLY'}
