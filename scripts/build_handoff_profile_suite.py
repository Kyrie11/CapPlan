#!/usr/bin/env python3
"""Two strictly separate profile suites: nonpopulation canonical and real empirical.

This script NEVER fabricates an empirical distribution from archetypes.
"""
from __future__ import annotations
import argparse,csv,json,random,sys,copy,math
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from capplan.data.capability_contracts import ARCHETYPES,make_profile,load_profile_config


def canonical(seed, per_archetype):
    return [dict(make_profile(f'canonical:{a}:{i}',a,seed=seed+10001*i+13*j),
                evaluation_suite='canonical_counterfactual',sampling_weight=None,
                population_representative=False,parameter_uncertainty='uniform_design_not_empirical',
                sampling_method='independent_uniform_intervals_for_algorithms_only')
            for j,a in enumerate(ARCHETYPES) for i in range(per_archetype)]



# Contract-nested stress tests are NOT a population model or a measured joint
# distribution. Only directions whose ordering is unambiguous are coupled.
_UPPER_FIELDS = {
    ('mobility','max_access_distance_m'),('mobility','max_egress_distance_m'),
    ('mobility','max_slope'),('mobility','max_cross_slope'),
    ('wait','max_wait_exposure_s'),('interface','max_dwell_time_s'),
    ('uncertainty','max_blockage_risk'),('uncertainty','max_deployment_risk'),
}
_LOWER_FIELDS = {
    ('mobility','min_clear_width_m'),('interface','min_deployment_clearance_m'),
    ('interface','min_door_width_m'),('uncertainty','min_map_confidence'),
}


def coupled_quantile(seed, per_archetype):
    """Produce nested threshold designs, not synthetic demographic samples.

    q=0 is most restrictive; q=1 most permissive on selected scalar axes.
    It preserves categorical choices, and does not claim real correlations.
    """
    cfg=load_profile_config()['archetypes']
    rows=[]
    for j,arch in enumerate(ARCHETYPES):
        template=cfg[arch]
        for i in range(per_archetype):
            q=(i+0.5)/per_archetype
            p=make_profile(f'canonical:coupled:{arch}:{i}',arch,seed=seed+41*j)
            dimensions=[]
            for section,key in sorted(_UPPER_FIELDS|_LOWER_FIELDS):
                value=template.get(section,{}).get(key)
                if not isinstance(value,list) or len(value)!=2 or not all(
                        isinstance(v,(int,float)) and math.isfinite(v) for v in value):
                    continue
                lo,hi=sorted(map(float,value))
                # Larger lower bounds impose stronger restrictions, so invert.
                fraction=(1-q) if (section,key) in _LOWER_FIELDS else q
                p[section][key]=round(lo+fraction*(hi-lo),4)
                dimensions.append(f'{section}.{key}')
            p.update(evaluation_suite='canonical_counterfactual',sampling_weight=None,
                     population_representative=False,
                     parameter_uncertainty='paired_monotone_threshold_stress_not_population',
                     sampling_method='nested_coupled_quantile_design',
                     severity_order_index=i,permissiveness_quantile=q,
                     controlled_dimensions=dimensions,
                     design_warning='Only explicit monotone threshold axes; not demographic correlation')
            rows.append(p)
    return rows

def empirical(path):
    source=Path(path)
    if not source.exists():raise ValueError('empirical profiles require provided consented study records')
    records=[]
    for idx,line in enumerate(source.read_text(encoding='utf-8').splitlines(),1):
        if not line.strip():continue
        row=json.loads(line)
        required=('profile_id','consent_scope','sampling_frame','collection_protocol','source_reference',
                  'measurement_uncertainty','sampling_weight','mobility','wait','interface')
        missing=[k for k in required if row.get(k) is None]
        if missing:raise ValueError(f'row {idx} missing mandatory empirical fields: {missing}')
        if not isinstance(row['sampling_weight'],(float,int)) or row['sampling_weight']<=0:
            raise ValueError('weight must be positive and study-derived')
        if row.get('sampling_method')=='independent_uniform_intervals_for_algorithms_only':
            raise ValueError('synthetic canonical records cannot be relabelled empirical')
        row['evaluation_suite']='empirical_evaluation'
        row['population_representative']=bool(row.get('sampling_frame_represents_target_population',False))
        records.append(row)
    if not records:raise ValueError('empty empirical source')
    return records


def main():
    p=argparse.ArgumentParser();p.add_argument('--suite',required=True,choices=['canonical','empirical']);
    p.add_argument('--output',required=True);p.add_argument('--empirical_input');p.add_argument('--seed',type=int,default=13)
    p.add_argument('--per_archetype',type=int,default=16)
    p.add_argument('--design',choices=['independent_uniform','coupled_quantile'],default='independent_uniform')
    a=p.parse_args()
    if a.suite=='empirical' and not a.empirical_input:p.error('empirical suite requires --empirical_input')
    if a.per_archetype<=0:p.error('per_archetype must be positive')
    records=(empirical(a.empirical_input) if a.suite=='empirical' else
             coupled_quantile(a.seed,a.per_archetype) if a.design=='coupled_quantile' else
             canonical(a.seed,a.per_archetype))
    out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(''.join(json.dumps(row,ensure_ascii=False)+'\n' for row in records),encoding='utf-8')
    manifest={'suite':a.suite,'profile_count':len(records),'seed':a.seed if a.suite=='canonical' else None,
              'design':a.design if a.suite=='canonical' else 'consented_data',
              'population_estimation_allowed':bool(a.suite=='empirical' and all(r.get('population_representative') for r in records)),
              'warning':'Canonical threshold designs are stress-test contracts, not people samples or evidence of correlated disability distributions' if a.suite=='canonical' else
                        'Weights and population claims require independent protocol review'}
    out.with_suffix('.manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(manifest,ensure_ascii=False))

if __name__=='__main__':main()
