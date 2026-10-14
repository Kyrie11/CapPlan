#!/usr/bin/env python3
"""Bind PUDO probes to nuPlan logs by episode.log_name and lidar_pc token.

Read-only. NEVER infer a counterfactual stop or physical curb from a logged ego
pose. Don't brute-force scan thousands of unrelated DB ego_pose tables.
"""
from __future__ import annotations
import argparse, json, math, sqlite3
from pathlib import Path
from collections import Counter, defaultdict


def records(path):
    with Path(path).open(encoding='utf-8') as f:
        for line in f:
            if line.strip():yield json.loads(line)

def classify_map(value):
    s=str(value or '').lower()
    for token, names in [('boston',('boston',)),('pittsburgh',('pittsburgh','hazelwood')),('vegas',('vegas',)),('singapore',('singapore','one-north'))]:
        if any(x in s for x in names):return token
    return None

def read_identity(conn):
    names={r[0] for r in conn.execute('SELECT name FROM sqlite_master WHERE type="table"')}
    if 'log' not in names:raise ValueError('NO_LOG_TABLE')
    columns={r[1] for r in conn.execute('PRAGMA table_info(log)')}
    fields=[x for x in ('logfile','location','map_version') if x in columns]
    row=conn.execute('SELECT '+','.join(fields)+' FROM log LIMIT 1').fetchone() if fields else None
    return dict(zip(fields,row)) if row else {}

def token_context(conn,scenario_token):
    if not scenario_token:return {'status':'NO_SCENARIO_TOKEN'}
    try: token=bytes.fromhex(str(scenario_token))
    except ValueError:return {'status':'INVALID_SCENARIO_TOKEN'}
    names={r[0] for r in conn.execute('SELECT name FROM sqlite_master WHERE type="table"')}
    if 'lidar_pc' not in names or 'ego_pose' not in names:return {'status':'MISSING_TABLES'}
    # nuPlan tokens are BLOB in production; allow TEXT hex in tiny test DBs.
    lp=conn.execute('SELECT timestamp,ego_pose_token FROM lidar_pc WHERE token=? OR token=? LIMIT 1',(token,str(scenario_token))).fetchone()
    if not lp:return {'status':'TOKEN_NOT_IN_MATCHED_LOG'}
    pose=conn.execute('SELECT x,y FROM ego_pose WHERE token=? LIMIT 1',(lp[1],)).fetchone()
    return {'status':'EXACT_LIDAR_TOKEN_BOUND','timestamp_us':int(lp[0]),'logged_ego_at_token':{'x':pose[0],'y':pose[1]} if pose else None}

def nearby_ego(conn,pose,ts_us,radius_m=25,window_s=10,max_rows=50000):
    if ts_us is None:return {'status':'NO_SCENE_TIMESTAMP','nearest':None}
    cols={r[1] for r in conn.execute('PRAGMA table_info(ego_pose)')}
    if not {'x','y','timestamp'}<=cols:return {'status':'MISSING_EGO_FIELDS','nearest':None}
    x=float(pose['x']);y=float(pose['y'])
    if not all(map(math.isfinite,(x,y))):return {'status':'INVALID_COORDINATES','nearest':None}
    lo=int(ts_us-window_s*1e6);hi=int(ts_us+window_s*1e6)
    sql=('SELECT x,y,timestamp FROM ego_pose WHERE timestamp BETWEEN ? AND ? '
         'AND x BETWEEN ? AND ? AND y BETWEEN ? AND ? LIMIT ?')
    rows=conn.execute(sql,(lo,hi,x-radius_m,x+radius_m,y-radius_m,y+radius_m,max_rows+1))
    best=None;count=0;exceeded=False
    for p in rows:
        count+=1
        if count>max_rows:exceeded=True;break
        dist=math.hypot(float(p[0])-x,float(p[1])-y)
        if best is None or dist<best['distance_m']:
            best={'distance_m':dist,'timestamp_us':int(p[2]),'x':p[0],'y':p[1]}
    return {'status':'SCENE_WINDOW_CONTEXT_ONLY','nearest':best,'rows_in_bbox':min(count,max_rows),'truncated':exceeded}

def bind(sites,episodes,db_root,split='test',radius_m=25,window_s=10,limit=None):
    selected=list(records(sites))
    if limit is not None:selected=selected[:limit]
    requested_ids={v['episode_id'] for v in selected}
    # Some episode metadata contains huge route polylines; retain identity only.
    eps={}
    for r in records(episodes):
        if r.get('episode_id') in requested_ids:
            eps[r['episode_id']]={k:r.get(k) for k in ('episode_id','log_name','scenario_token','map_name')}
    root=Path(db_root);search_root=root/split if split else root
    if not search_root.is_dir():raise FileNotFoundError(search_root)
    # Inventory names only (fast), never query ego_pose in unrelated databases.
    by_stem=defaultdict(list)
    for path in search_root.rglob('*.db'):by_stem[path.stem].append(path)
    if not by_stem:raise RuntimeError('NO_DB_FILES_FOUND')
    rows=[];counts=Counter()
    by_ep=defaultdict(list)
    for site in selected:by_ep[site['episode_id']].append(site)
    for ep_id,ep_sites in by_ep.items():
        e=eps.get(ep_id);stem=e.get('log_name') if e else None
        matches=by_stem.get(str(stem),[]) if stem else []
        reason=('EPISODE_NOT_FOUND' if e is None else 'EPISODE_MISSING_LOG_NAME' if not stem
                else 'LOG_DB_NOT_FOUND' if not matches else 'AMBIGUOUS_LOG_DB' if len(matches)>1 else None)
        report={'episode_id':ep_id,'log_name':stem,'db_candidates':[str(p) for p in matches],
                'status':reason,'site_results':[]}
        if reason:
            counts[reason]+=1;rows.append(report);continue
        db=matches[0]
        try:
            conn=sqlite3.connect('file:'+str(db.resolve())+'?mode=ro',uri=True)
            try:
                log=read_identity(conn)
                stored_city=classify_map(log.get('location'))
                expected_city=classify_map(e.get('map_name'))
                if stored_city and expected_city and stored_city!=expected_city:
                    report['status']='CITY_MISMATCH';report['log_identity']=log
                else:
                    scenario_token=e.get('scenario_token')
                    ctx=token_context(conn,scenario_token)
                    report.update(status=ctx['status'],log_identity=log,token_context=ctx,db_file=str(db))
                    if ctx['status']=='EXACT_LIDAR_TOKEN_BOUND':
                        for site in ep_sites:
                            near=nearby_ego(conn,site['pose'],ctx['timestamp_us'],radius_m,window_s)
                            report['site_results'].append({'site_id':site['site_id'],'physical_site_key':site.get('site_key'),
                              'site_pose_origin':site.get('evidence_status'), 'nearby':near,
                              'physical_pudo_legal_or_boardable':None,'scope':'HISTORICAL_EGO_CONTEXT_NOT_COUNTERFACTUAL_HANDOFF'})
            finally:conn.close()
        except (sqlite3.Error,ValueError,OSError) as ex:report['status']='DB_READ_ERROR';report['error']=str(ex)
        counts[report['status']]+=1;rows.append(report)
    return {'split':split,'db_root':str(search_root),'dbs_in_inventory':sum(map(len,by_stem.values())),
      'site_inputs':len(selected),'episode_inputs':len(by_ep),'counts':dict(counts),'episodes':rows,
      'research_limit':'Logged ego is not a vehicle replan, legal PUDO, passenger route, stop pose, or actual boarding.'}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--sites',required=True);p.add_argument('--episodes',required=True)
    p.add_argument('--db_root',required=True);p.add_argument('--split',default='test');p.add_argument('--output',required=True)
    p.add_argument('--radius_m',type=float,default=25);p.add_argument('--window_s',type=float,default=10);p.add_argument('--limit',type=int)
    a=p.parse_args()
    if a.radius_m<=0 or a.window_s<=0:p.error('radius/window must be positive')
    report=bind(a.sites,a.episodes,a.db_root,a.split,a.radius_m,a.window_s,a.limit)
    output=Path(a.output);output.parent.mkdir(parents=True,exist_ok=True);output.write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps({k:report[k] for k in ('dbs_in_inventory','site_inputs','counts')},ensure_ascii=False))
if __name__=='__main__':main()
