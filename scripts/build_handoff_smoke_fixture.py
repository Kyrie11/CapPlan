#!/usr/bin/env python3
"""Create a tiny SYNTHETIC DATASET SCHEMA TEST; never use it as nuPlan evidence."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from dataclasses import asdict
from capplan.data.schemas import AccessibilityGraph,AccessibilityEdge,AccessibilityNode


def write(path,records):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in records),encoding='utf-8')


def create(root,n=6):
    root=Path(root)
    episodes=[];anchors=[]
    for i in range(n):
        eid=f'SYNTHETIC_FIXTURE_{i}'
        episodes.append({'episode_id':eid,'origin_anchor':'O',
                         'map_name':'synthetic_only','scenario_token':eid,'split':'smoke'})
        for anchor,x in [('A',130),('B',170)]:
            anchors.append({'episode_id':eid,'anchor_id':anchor,'kind':'pickup',
              'legal_stop':True,'side':'right','sidewalk_width_m':1.5,
              'deployment_clearance_m':1.0,'adjacent_ped_node_id':'ped_'+anchor,
              'hybrid_physical_site_key':f'fake_site_{i}_{anchor}',
              'stop_pose':{'x':float(x),'y':10.0,'heading':0.0,'frame':'fixture'},
              'curb_pose':{'x':float(x),'y':0.0,'heading':0.0,'frame':'fixture'},
              'evidence_kind':'synthetic_fixture',
              'truth_mode':'synthetic_fixture_not_real'})
        ns=[AccessibilityNode(x,0,0,'entrance' if x=='O' else 'pudo',source='synthetic_fixture')
            for x in ['O','ped_A','ped_B']]
        def edge(name,a,b,length):
            return AccessibilityEdge(name,a,b,length,width_m=1.5,slope=0.01,cross_slope=0.01,
                step_free=True,curb_ramp=True,confidence=1.0,crossing_type='curb',
                source='synthetic_fixture',metadata={'source':'explicit_synthetic_fixture'})
        es=[edge('oa','O','ped_A',130),edge('ob','O','ped_B',170),edge('ab','ped_A','ped_B',120)]
        graph=AccessibilityGraph(eid,ns,es,{'mode':'synthetic_fixture_only'})
        write(root/'accessibility_graphs'/f'{eid}.jsonl',[asdict(graph)])
    write(root/'episodes.jsonl',episodes)
    write(root/'pudo_anchors.jsonl',anchors)
    (root/'SYNTHETIC_FIXTURE_NOT_RESEARCH_DATA.txt').write_text(
        'These geometries, profiles and event traces are artificial smoke tests only.\n',encoding='utf-8')
    return n

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output_dir',required=True)
    p.add_argument('--episodes',type=int,default=6);a=p.parse_args()
    if a.episodes<=0:p.error('--episodes must be positive')
    print(json.dumps({'synthetic_only':True,'episodes':create(a.output_dir,a.episodes)}))
