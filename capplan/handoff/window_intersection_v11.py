"""Evidence-aware local rendezvous *screen*, not a real boarding certificate.

Computes continuous-time intersections across arrival, parking/dwell, and
observation-validity intervals. All numbers and geometry are asserted by the
caller; a simulated stop or a GIS proxy cannot be silently upgraded to a
physical/vehicle attestation. This necessary-condition screen supports
multiple disconnected safe windows; there is no global single deadline.

This module does NOT establish robust policy existence across hidden worlds,
passenger consent/obedience, observed accessibility, or vehicle motion safety.
"""
from __future__ import annotations
import math


def _intervals(items, name):
    if items is None:
        return None
    if not isinstance(items,list):
        raise ValueError(f'{name} must be a list of [start,end] intervals')
    result=[]
    for span in items:
        if not isinstance(span,(list,tuple)) or len(span)!=2:
            raise ValueError(f'{name} must contain two-element intervals')
        a,b=span
        if (not isinstance(a,(int,float)) or isinstance(a,bool) or
            not isinstance(b,(int,float)) or isinstance(b,bool) or
            not math.isfinite(a) or not math.isfinite(b) or a>b):
            raise ValueError(f'{name} has an invalid interval {span}')
        result.append((float(a),float(b)))
    return sorted(result)


def check_option(row:dict):
    """Evaluate one PUDO option; NEVER claim a completed/physically verified handoff.

    Required:
      passenger_arrival_window_s [earliest,latest] as derived from progress,
      already consumed budgets, response and directional path constraints;
      passenger_wait_remaining_s, boarding_duration_s;
      vehicle_stop_windows_s [[start,end],...]: legal/static stop pose window;
      evidence_validity_windows_s [[start,end],...]: report freshness windows.

    The latter are *asserted* windows, NOT proof that a dynamic curb actually
    stays unblocked. Additional provenance/independent motion evidence is
    required before any real-world claim.
    """
    result={'option_id':row.get('option_id'),'scope':'LOCAL_NECESSARY_CONDITION_ONLY',
            'physical_verification':'NOT_ESTABLISHED_BY_THIS_CODE',
            'not_a_robust_policy_or_boarding_certificate':True}
    pedestrian=_intervals([row['passenger_arrival_window_s']] if row.get('passenger_arrival_window_s') is not None else None,
                          'passenger_arrival_window_s')
    vehicle=_intervals(row.get('vehicle_stop_windows_s'), 'vehicle_stop_windows_s')
    evidence=_intervals(row.get('evidence_validity_windows_s'), 'evidence_validity_windows_s')
    required=(pedestrian,vehicle,evidence)
    if any(x is None for x in required) or row.get('passenger_wait_remaining_s') is None or row.get('boarding_duration_s') is None:
        result.update(status='UNKNOWN_MISSING_INPUT',possible_start_windows_s=[],
                      interpretation='Missing evidence cannot be used as a zero-length window or as physical failure')
        return result
    wait=float(row['passenger_wait_remaining_s']);boarding=float(row['boarding_duration_s'])
    if not all(math.isfinite(v) for v in (wait,boarding)) or min(wait,boarding)<0 or boarding==0:
        raise ValueError('waiting and positive boarding duration must be finite and nonnegative')
    assert len(pedestrian)==1
    p_start,p_end=pedestrian[0]
    # Waiting is residual resource. Passenger may arrive anywhere within the
    # supplied feasible arrival interval; the service start must occur by the
    # actual arrival plus remaining waiting allowance.
    p_service=(p_start,p_end+wait)
    intervals=[]
    for va,vb in vehicle:
        for ea,eb in evidence:
            lo=max(p_service[0],va,ea)
            hi=min(p_service[1],vb-boarding,eb-boarding)
            if lo<=hi+1e-9:
                intervals.append((lo,hi))
    if intervals:
        intervals.sort()
        merged=[]
        for lo,hi in intervals:
            if merged and lo<=merged[-1][1]+1e-9:
                merged[-1]=(merged[-1][0],max(merged[-1][1],hi))
            else:merged.append((lo,hi))
        result.update(status='TEMPORAL_WITNESS_UNDER_ASSERTED_INPUTS',
                      possible_start_windows_s=[[round(lo,4),round(hi,4)] for lo,hi in merged],
                      disconnected_windows=len(merged),
                      interpretation='Only time-overlap is witnessed; requires independent directed path, stop-pose, interface, actual event and observation attestation')
    else:
        result.update(status='NO_TEMPORAL_OVERLAP_UNDER_ASSERTED_INPUTS',possible_start_windows_s=[],
                      disconnected_windows=0,
                      interpretation='No overlap for this option; does not exclude third PUDO, changed vehicle action or assisted continuation')
    missing=[name for name in ('directed_pedestrian_path','legal_stop','vehicle_trajectory_stop_pose',
                                'deployment_region','dynamic_observation')
             if not isinstance(row.get('attestations',{}).get(name),dict) or
             not row['attestations'][name].get('independent_source_ref')]
    result['missing_independent_attestations']=missing
    result['can_be_claimed_as_real_vehicle_boarding']=False
    return result
