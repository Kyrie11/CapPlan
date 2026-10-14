#!/usr/bin/env python3
"""Read-only, time-bounded nuPlan SQLite ego-position probe.

This is NOT a simulated counterfactual trajectory or a verified PUDO stop.
Request-scene association must be externally demonstrated: site coordinates
can occur in a different log, date, city or timestamp.
"""
from __future__ import annotations
import argparse, json, math, sqlite3
from pathlib import Path

def table_columns(conn,name):
    return {r[1] for r in conn.execute(f'PRAGMA table_info({name})')}

def probe(db,sites,max_distance_m=25,time_start_us=None,time_end_us=None,max_rows_per_site=200000):
    db=Path(db)
    if not db.is_file():raise FileNotFoundError(db)
    conn=sqlite3.connect(f'file:{db.resolve()}?mode=ro',uri=True)
    try:
        cols=table_columns(conn,'ego_pose');needed={'x','y','timestamp'}
        if not needed.issubset(cols):raise ValueError(f'ego_pose missing required fields: {needed-cols}')
        location=[]
        if {'location','map_version'}.issubset(table_columns(conn,'log')):
            location=[{'location':r[0],'map_version':r[1]} for r in conn.execute('SELECT DISTINCT location,map_version FROM log LIMIT 20')]
        result=[]
        for site in sites:
            p=site['pose'];x=float(p['x']);y=float(p['y'])
            if not (math.isfinite(x) and math.isfinite(y)):raise ValueError('invalid pose coordinates')
            sql='SELECT x,y,timestamp'+(',vx,vy' if {'vx','vy'}<=cols else '')+' FROM ego_pose WHERE x BETWEEN ? AND ? AND y BETWEEN ? AND ?'
            args=[x-max_distance_m,x+max_distance_m,y-max_distance_m,y+max_distance_m]
            if time_start_us is not None:
                sql+=' AND timestamp >= ?';args.append(int(time_start_us))
            if time_end_us is not None:
                sql+=' AND timestamp <= ?';args.append(int(time_end_us))
            sql+=' ORDER BY timestamp LIMIT ?';args.append(max_rows_per_site+1)
            rows=list(conn.execute(sql,args));truncated=len(rows)>max_rows_per_site;rows=rows[:max_rows_per_site]
            nearest=None
            for row in rows:
                r=math.hypot(float(row[0])-x,float(row[1])-y)
                if nearest is None or r<nearest['center_distance_m']:
                    nearest={'center_distance_m':r,'timestamp_us':row[2], 'ego_x':row[0],'ego_y':row[1]}
                    if len(row)>=5:nearest['ego_logged_speed_mps']=math.hypot(float(row[3]),float(row[4]))
            result.append({'site_id':site['site_id'],'samples_in_bbox':len(rows),'query_truncated':truncated,
                           'nearest_logged_ego':nearest,'matched_pudo_stop':False,
                           'episode_identity_matched':False,'evidence_class':'UNMATCHED_LOG_SPATIAL_CONTEXT_ONLY'})
        return {'db':str(db),'db_locations':location,'site_results':result,
                'time_window_us':[time_start_us,time_end_us],
                'scope':'observation of recorded ego trajectories; not counterfactual vehicle reachability, curb legality or nuPlan scenario binding'}
    finally:conn.close()

def main():
    p=argparse.ArgumentParser();p.add_argument('--db',required=True);p.add_argument('--sites_jsonl',required=True)
    p.add_argument('--output',required=True);p.add_argument('--radius_m',type=float,default=25)
    p.add_argument('--start_us',type=int);p.add_argument('--end_us',type=int)
    p.add_argument('--max_rows_per_site',type=int,default=200000)
    a=p.parse_args();sites=[json.loads(x) for x in Path(a.sites_jsonl).read_text().splitlines() if x.strip()]
    if not 0<a.radius_m<=1000: p.error('radius_m invalid')
    d=probe(a.db,sites,a.radius_m,a.start_us,a.end_us,a.max_rows_per_site)
    out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True);out.write_text(json.dumps(d,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps({'sites':len(sites),'db_locations':d['db_locations']}))
if __name__=='__main__':main()
