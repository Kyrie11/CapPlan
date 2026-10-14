#!/usr/bin/env python3
"""Apply canonical functional contracts to same real graph episodes.

This is a nonpopulation, independently-uniform stress test.  Every swept
contract is reported separately; contract feasibility is necessary, not
physical service success.  The pedestrian filter covers only distance, width,
slope, cross slope and step-free needs (NOT every contract clause).
"""
from __future__ import annotations
import argparse,importlib.util,json,random,sys
from pathlib import Path
from collections import Counter,defaultdict
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from capplan.data.accessibility_layer import load_accessibility_graph
from capplan.data.capability_contracts import ARCHETYPES,make_profile

sp=importlib.util.spec_from_file_location('handoff_pair_miner',Path(__file__).with_name('mine_handoff_recourse_pairs.py'))
miner=importlib.util.module_from_spec(sp);sp.loader.exec_module(miner)


def rows(path):
    with Path(path).open(encoding='utf-8') as f:
        for line in f:
            if line.strip():yield json.loads(line)


def run(dataset_dir,output_dir,episode_limit=50,seed=13,samples=3,already_consumed_m=0,
        event_time_s=90, profiles_jsonl=None,require_explicit_curb_connector=False):
    root=Path(dataset_dir)
    eps=list(rows(root/'episodes.jsonl'))
    random.Random(seed).shuffle(eps)
    eps=eps[:episode_limit]
    selected={e['episode_id'] for e in eps}
    anchors=defaultdict(list)
    for a in rows(root/'pudo_anchors.jsonl'):
        if a['episode_id'] in selected:anchors[a['episode_id']].append(a)
    # Consumes exactly the same suite written by build_handoff_profile_suite,
    # avoiding silently resampling a different profile for the sweep.
    if profiles_jsonl:
        all_profiles=list(rows(Path(profiles_jsonl)))
        if not all_profiles or any(p.get('evaluation_suite')!='canonical_counterfactual' for p in all_profiles):
            raise ValueError('profile sweep requires nonpopulation canonical suite')
        active_profiles=all_profiles
    else:
        active_profiles=[make_profile(f'canonical:{arch}:{i}',arch,seed+1009*i+41*ia)
                         for ia,arch in enumerate(ARCHETYPES) for i in range(samples)]
    out=Path(output_dir);out.mkdir(parents=True,exist_ok=True)
    result_count=Counter();denom=Counter();errors=[]
    with (out/'profile_witnesses.jsonl').open('w',encoding='utf-8') as f:
        for ep in eps:
            try:
                graph=load_accessibility_graph(root,ep['episode_id'])
                for p in active_profiles:
                        arch=p.get('archetype','UNKNOWN')
                        m=p['mobility']
                        args=SimpleNamespace(distance_budget_m=float(m['max_access_distance_m']),
                          already_consumed_m=float(already_consumed_m),
                          min_width_m=float(m['min_clear_width_m']),max_slope=float(m['max_slope']),
                          max_cross_slope=float(m['max_cross_slope']),
                          min_confidence=float(p['uncertainty']['min_map_confidence']),
                          require_step_free=bool(m['step_free_required']),
                          require_interface_complete=True,
                          require_explicit_curb_connector=require_explicit_curb_connector,
                          event_time_s=float(event_time_s))
                        pairs,counts=miner.mine_episode(ep,anchors[ep['episode_id']],graph,args,{})
                        denom[(arch,'evaluated_episode_contracts')]+=1
                        denom[(arch,'geometry_witnesses')]+=len(pairs)
                        denom[(arch,'episodes_with_witness')]+=int(bool(pairs))
                        for item in pairs:
                            item.update({'profile_id':p['profile_id'],'profile_archetype_design':arch,
                              'profile_suite':'canonical_counterfactual','population_weight':None,
                              'contract_parameters_from':p['profile_id'],
                              'uncertainty_note':'independent uniform design, not an empirical capability distribution',
                              'tested_clause_subset':['access_distance','step_free','min_width','slope','cross_slope','min_map_confidence'],
                              'untested_contract_clauses':['curb_ramp','surface','max_single_segment','turn_radius',
                                                          'door_side','boarding_interface','wait','ride']})
                            f.write(json.dumps(item,ensure_ascii=False)+'\n')
                            result_count[arch]+=1
            except (FileNotFoundError,KeyError,ValueError) as exc:
                errors.append({'episode_id':ep['episode_id'],'error':repr(exc)})
    summary={'selected_episodes':len(eps),'samples_per_archetype':samples if not profiles_jsonl else None,
      'profile_count_per_episode':len(active_profiles),'profile_input':profiles_jsonl,
      'require_explicit_curb_connector':require_explicit_curb_connector,'seed':seed,
      'population_statistics_permitted':False,
      'by_design_archetype':{a:{key:v for (kind,key),v in denom.items() if kind==a} for a in ARCHETYPES},
      'errors':errors,
      'scope':'necessary geometric witness stratified by synthetic functional contracts; no physical vehicle/board certification'}
    (out/'profile_sweep.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'by_archetype':dict(result_count),'errors':len(errors)},ensure_ascii=False))
    return 2 if errors else 0

if __name__=='__main__':
    a=argparse.ArgumentParser()
    a.add_argument('--dataset_dir',required=True);a.add_argument('--output_dir',required=True)
    a.add_argument('--episode_limit',type=int,default=50);a.add_argument('--seed',type=int,default=13)
    a.add_argument('--samples',type=int,default=3);a.add_argument('--already_consumed_m',type=float,default=0)
    a.add_argument('--event_time_s',type=float,default=90)
    a.add_argument('--profiles_jsonl')
    a.add_argument('--require_explicit_curb_connector',action='store_true')

    opts=a.parse_args()
    raise SystemExit(run(**vars(opts)))
