#!/usr/bin/env python3
"""Export ONLY selected per-episode pedestrian graphs and audit records.

No inference about curb legality is performed; missing graphs are reported.
"""
import argparse,hashlib,json,shutil,zipfile
from pathlib import Path

def main():
    p=argparse.ArgumentParser();p.add_argument('--sites',required=True)
    p.add_argument('--dataset_dir',required=True);p.add_argument('--output_zip',required=True)
    a=p.parse_args();eps=set()
    for line in Path(a.sites).open():
        if line.strip():eps.add(json.loads(line)['episode_id'])
    root=Path(a.dataset_dir)/'accessibility_graphs';out=Path(a.output_zip);out.parent.mkdir(parents=True,exist_ok=True)
    statuses={};hashes={}
    with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
        for ep in sorted(eps):
            found=[]
            for ext in ('nodes.jsonl','edges.jsonl','audit.jsonl'):
                f=root/f'{ep}.{ext}'
                if f.is_file():
                    name=f'accessibility_graphs/{f.name}';z.write(f,name)
                    hashes[name]=hashlib.sha256(f.read_bytes()).hexdigest();found.append(ext)
            statuses[ep]={'found':found,'required_nodes_edges_present':all(f in found for f in ('nodes.jsonl','edges.jsonl'))}
        man={'source_type':'REAL_GEOSPATIAL_TOPOLOGY_WITH_HYBRID_ATTRIBUTES','episode_count':len(eps),
             'graph_records':statuses,'hashes':hashes,'boundary':'Neither graph connectivity nor simulated curb fields constitute independent physical field truth'}
        z.writestr('MANIFEST.json',json.dumps(man,indent=2)+'\n')
    print(json.dumps({'exported_episodes':len(eps),'with_nodes_edges':sum(v['required_nodes_edges_present'] for v in statuses.values()),'output':str(out)}))
if __name__=='__main__':main()
