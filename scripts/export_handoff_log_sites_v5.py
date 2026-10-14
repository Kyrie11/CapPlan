#!/usr/bin/env python3
"""Export nuPlan MAP-FRAME stop poses without claiming they have been verified."""
import argparse,json
from pathlib import Path

def run(input_path,output,max_pairs=32):
    count=0;seen=set();p=Path(output);p.parent.mkdir(parents=True,exist_ok=True)
    with p.open('w') as f:
        for line in Path(input_path).open():
            if not line.strip():continue
            row=json.loads(line)
            for side in ('A','B'):
                k=(row['episode_id'],side)
                if k in seen:continue
                pose=row.get('anchor_vehicle_poses',{}).get(side+'_stop_pose')
                if not pose or pose.get('frame')!='map':continue
                seen.add(k)
                d={'site_id':f'{row["episode_id"]}:{side}','episode_id':row['episode_id'], 'city':row.get('map_name'),
                   'scenario_token':row.get('scenario_token'),'pose':{'x':pose['x'],'y':pose['y']},
                   'evidence_status':'UNVERIFIED_GIS_POSE_PROXY','site_key':(row.get('physical_site_pair') or [None,None])[0 if side=='A' else 1]}
                f.write(json.dumps(d)+'\n')
            count+=1
            if count>=max_pairs:break
    return {'pairs':count,'sites':len(seen)}
if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('--pairs',required=True);a.add_argument('--output',required=True);a.add_argument('--limit',type=int,default=32)
    args=a.parse_args();print(run(args.pairs,args.output,args.limit))
