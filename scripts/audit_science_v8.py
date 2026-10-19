#!/usr/bin/env python3
"""Fail-closed Science V7/V8 research audit using *already executed* evidence.

Distinguishes model-predicted graph feasibility, simulated interface attributes,
and independently verified real passenger handoff. Does not estimate prevalence.
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import json
import math
from pathlib import Path


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def records(path):
    with Path(path).open(encoding='utf-8') as f:
        for line in f:
            if line.strip():
                row=json.loads(line)
                if not isinstance(row,dict): raise ValueError(f'{path}: expected JSON object')
                yield row


def sha256(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):
            h.update(b)
    return h.hexdigest()


def extract_windows(rows):
    """Observed grid crossings only: NEVER interpolate unobserved exact deadlines."""
    grouped=defaultdict(list)
    for r in rows:
        k=(r['episode_id'],r['a_anchor'],r['b_anchor'])
        t=float(r['notice_issued_at_s'])
        if not math.isfinite(t) or t<0:raise ValueError('invalid notice time')
        grouped[k].append((t,bool(r['can_redirect_to_B_in_graph']),r))
    output=[]
    for key, points in grouped.items():
        points.sort(key=lambda p:p[0]);seen={};inversion=False
        for t,yes,r in points:
            if t in seen and seen[t]!=yes:
                raise ValueError(f'conflicting outcomes for {key} at {t}')
            seen[t]=yes
        # This monotonic assumption holds for a static undirected graph and fixed
        # distance budget. If a future experimental setup violates it, REPORT it.
        inversion=any(not yes and later_yes for (_,yes,_),(_,later_yes,_) in zip(points,points[1:]))
        possible=[t for t,yes,_ in points if yes]
        impossible=[t for t,yes,_ in points if not yes]
        a=points[0][2]
        output.append({'episode_id':key[0], 'a_anchor':key[1], 'b_anchor':key[2],
            'city':a.get('city'), 'physical_site_pair':a.get('physical_site_pair'),
            'grid_n':len(seen), 'last_tested_possible_s':max(possible) if possible else None,
            'first_tested_impossible_s':min(impossible) if impossible else None,
            'monotonicity_violation':inversion,
            'all_grid_feasible':bool(possible and not impossible),
            'all_grid_infeasible':bool(impossible and not possible),
            'boundary_semantics':'observed grid bracket, not an exact critical time'})
    return output


def run(v7,output,reports=None):
    v7=Path(v7);o=Path(output);o.mkdir(parents=True,exist_ok=True)
    chain=read_json(v7/'evidence_chain.json')
    site_rows=chain.get('sites',[])
    unique_sites={(s.get('city'),s.get('physical_site_key')) for s in site_rows if s.get('physical_site_key')}
    controls={};control_trials=[]
    for speed in ('5','8'):
        path=v7/f'carla_paired_speed{speed}'/'paired_summary.json'
        if not path.is_file():continue
        trials=read_json(path).get('trials',[])
        statuses=Counter(t.get('status','NO_STATUS') for t in trials)
        clear=[]
        for t in trials:
            r=t.get('runs',{}).get('clear_A',{})
            trace_dir=Path(r['output_dir']) if r.get('output_dir') else path.parent/t.get('trial_id','')
            # Never rely on a server-specific absolute output_dir in packaged files.
            candidates=list(path.parent.glob(f"pair_{t.get('pair_id',-1):02d}_*/clear_A/execution_report.json"))
            raw=read_json(candidates[0]) if len(candidates)==1 else {}
            gap=raw.get('minimum_goal_gap_m')
            control_trials.append({'speed_kmh':float(speed),'pair_id':t.get('pair_id'),
                'status':t.get('status'),'stop_with_dwell':bool(r.get('stop_with_dwell')),
                'reached_goal_zone':r.get('goal_zone_entered'),
                'minimum_goal_gap_m':gap,'target_radius_m':t.get('parameters',{}).get('radius_m'),
                'minimum_gap_minus_radius_m':(float(gap)-float(t['parameters']['radius_m'])
                    if gap is not None and t.get('parameters',{}).get('radius_m') is not None else None),
                'minimum_gap_source':'CARLA vehicle-only trajectory; not curb distance'})
            clear.append(bool(r.get('stop_with_dwell')))
        controls[speed]={'trial_count':len(trials),'statuses':dict(statuses),
            'clear_A_stationary_control_passes':sum(clear),'controlled_causal_contrasts_supported':sum(
             1 for t in trials if t.get('status') in (
                  'CARLA_VEHICLE_ONLY_CONTROLLED_REDIRECTION',
                  'VEHICLE_REDIRECTION_OBSERVED_BUT_SIGNAL_PHASE_NOT_CONTROLLED'))}
    mid={};windows={}
    for mode in ('continue','pause'):
        p=v7/f'midroute_{mode}.jsonl'
        if not p.is_file(): continue
        rows=list(records(p))
        windows[mode]=extract_windows(rows)
        count=Counter()
        for r in rows:
            count[float(r['notice_issued_at_s'])]+=int(bool(r['can_redirect_to_B_in_graph']))
        mid[mode]={'row_count':len(rows),'distinct_pair_rows':len(windows[mode]),
                   'feasible_on_preselected_grid':{str(k):v for k,v in sorted(count.items())},
                   'monotonicity_violations':sum(w['monotonicity_violation'] for w in windows[mode]),
                   'meaning':'static pedestrian graph, fixed contract, not measured event frequency'}
    profiles=read_json(v7/'profile_path_audit.summary.json') if (v7/'profile_path_audit.summary.json').is_file() else {}
    pc=profiles.get('counts',{})
    provenance_count=Counter()
    for s in site_rows:
        for name,item in (s.get('hybrid_interface_fields') or {}).items():
            provenance_count[name+':'+str(item.get('provenance','unknown'))]+=1
    source_report={}
    if reports:
        reports=Path(reports)
        for city in ('boston','pittsburgh','vegas','singapore'):
            path=reports/f'hybrid_graph.test.{city}.json'
            if path.is_file():
                d=read_json(path)
                source_report[city]={
                   'edge_count':d.get('edges'),
                   'field_provenance_counts':d.get('field_provenance_kind_counts'),
                   'warning':'edge records heavily correlated; these are NOT independent measurements'}
    reported_real=chain.get('summary',{}).get('independently_confirmed_pudo_count',0)
    real_legal=sum(s.get('real_legal_pudo_verified') is True for s in site_rows)
    real_board=sum(s.get('real_boarding_verified') is True for s in site_rows)
    gates={
       'G1_independent_physical_PUDO':'PASS' if reported_real>0 and real_legal>0 else 'HOLD_MISSING_INDEPENDENT_EVIDENCE',
       'G2_valid_matched_CARLA_control':'PASS_VEHICLE_ONLY' if any(x['controlled_causal_contrasts_supported']>0 for x in controls.values()) else 'HOLD_CLEAR_A_INVALID_OR_NO_MATCH',
       'G3_midroute_graph_time_dependence':'SCREEN_ONLY' if any(x['distinct_pair_rows']>0 for x in mid.values()) else 'NOT_RUN',
       'G4_capability_sensitive_graph':'SCREEN_ONLY' if pc.get('hybrid_A_B_flip',0)>0 else 'NOT_ESTABLISHED',
       'G5_strong_published_belief_shield':'NOT_RUN',
       'G6_real_handoff_boarding':'PASS' if real_board>0 else 'NOT_VERIFIED'
    }
    files=[v7/'evidence_chain.json',v7/'profile_path_audit.summary.json']
    files += [v7/f'midroute_{mode}.jsonl' for mode in mid]
    files += [v7/f'carla_paired_speed{speed}'/'paired_summary.json' for speed in controls]
    summary={'schema':'capplan_science_v8_evidence_audit_v1',
       'evidence_gates':gates,
       'site_statistics':{'review_rows':len(site_rows),'unique_known_physical_site_keys':len(unique_sites),
            'independently_confirmed_pudo_count_declared':reported_real,
            'legal_stop_true_physically_verified_rows':real_legal,
            'physical_boarding_verified_rows':real_board,
            'provenance':dict(sorted(provenance_count.items()))},
       'carla':controls,'graph_midroute':mid,
       'capability_profiles':pc, 'graph_provenance_by_city':source_report,
       'input_sha256':{str(p.relative_to(v7)):sha256(p) for p in files if p.exists()},
       'publication_claim_limit': 'No natural incidence, no independent human boarding, no CARLA/nuPlan physical registration',
       'selection_bias':'preselected 32 pairs + 40 canonical profiles; do not treat trials as independent samples'}
    (o/'audit_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (o/'commitment_window_grid.json').write_text(json.dumps(windows,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    with (o/'carla_clear_control_diagnosis.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=['speed_kmh','pair_id','status','stop_with_dwell','reached_goal_zone','minimum_goal_gap_m','target_radius_m','minimum_gap_minus_radius_m','minimum_gap_source']);w.writeheader();w.writerows(control_trials)
    print(json.dumps({'gates':gates,'output':str(o),'controls':controls},ensure_ascii=False))
    return summary


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--v7',required=True,help='Directory containing evidence_chain.json and V7 experiment outputs')
    ap.add_argument('--reports',help='Directory containing hybrid_graph.test.<city>.json')
    ap.add_argument('--output',required=True)
    args=ap.parse_args();run(args.v7,args.output,args.reports)

if __name__=='__main__': main()
