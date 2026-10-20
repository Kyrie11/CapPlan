#!/usr/bin/env python3
"""Read-only bounded inventory of /data0/.../CapPlan/data, including prepared/.

No DB content access, no directory mutations, no symlink traversal. Produces a
small JSON manifest with sampled schema keys; never publishes precise vehicle
coordinates or user-identifying episode payloads. Does not modify any file
under the dataset root.
"""
from __future__ import annotations
import argparse,json,os,sys
from collections import Counter,defaultdict
from pathlib import Path


def preview(path:Path):
    try:
        if path.suffix=='.jsonl':
            with path.open(encoding='utf8') as f:
                for line in f:
                    if line.strip():
                        obj=json.loads(line);break
                else:return {'empty':True}
        elif path.suffix=='.json' and path.stat().st_size<4_000_000:
            obj=json.loads(path.read_text(encoding='utf8'))
        else:return {}
        if isinstance(obj,dict):
            return {'schema_keys':sorted(obj)[:90],
                    'field_provenance_example_keys':sorted(obj.get('field_provenance',{}))[:30] if isinstance(obj.get('field_provenance'),dict) else [],
                    'nested_dict_keys':{k:sorted(v)[:18] for k,v in obj.items() if isinstance(v,dict) and k in
                     ('metadata','manifest','field_provenance','source_policy','counts')}}
        return {'json_root_type':type(obj).__name__}
    except (ValueError,OSError,UnicodeDecodeError) as exc:
        return {'sample_error':str(exc)[:200]}


def inspect(root:Path,max_nodes:int=25000,depth:int=5):
    root=root.resolve(strict=True); data={'root':str(root),'scan_depth':depth,'max_nodes':max_nodes}
    folders=Counter();exts=Counter();largest=[];schemas=[]; visited=0;cutoff=False
    for top in ('outputs','external','nuplan'):
        base=root/top
        if not base.exists():continue
        if base.is_symlink():continue
        stack=[(base,1)]
        while stack:
            directory,d=stack.pop();folders[str(directory.relative_to(root))]+=1
            try: entries=sorted(os.scandir(directory),key=lambda x:x.name)
            except OSError:continue
            for ent in entries:
                visited+=1
                if visited>max_nodes:cutoff=True;break
                if ent.is_symlink():continue
                rel=str(Path(ent.path).relative_to(root))
                if ent.is_dir(follow_symlinks=False):
                    if d<depth:stack.append((Path(ent.path),d+1))
                    else:folders[rel]+=-1
                elif ent.is_file(follow_symlinks=False):
                    size=ent.stat(follow_symlinks=False).st_size
                    exts[(top,Path(ent.name).suffix.lower())]+=1
                    largest.append((size,rel))
                    if len(largest)>30:largest.sort(reverse=True);largest=largest[:20]
                    # Safe file examples / root datasets / prepared; do not sample
                    # large raw GIS, DB, images, private coordinates.
                    parts=Path(rel).parts
                    if (Path(ent.name).suffix.lower() in ('.json','.jsonl') and
                        (('outputs' in parts and len(parts)<6) or
                         ('prepared' in parts and len(parts)<6) or
                         ent.name in ('dataset_manifest.json','validation_report.json'))):
                        if len(schemas)<140:
                            schemas.append({'path':rel,'size_bytes':size,**preview(Path(ent.path))})
            if cutoff:break
        if cutoff:break
    data.update({'nodes_seen':visited,'cutoff':cutoff,
                 'files_by_top_and_extension':{f'{a}:{b}':v for (a,b),v in exts.items()},
                 'directory_markers':sorted(folders)[:500],
                 'largest_files':[{'bytes':n,'path':p} for n,p in sorted(largest,reverse=True)[:20]],
                 'json_schema_previews':schemas,
                 'rule':'read-only filenames, sizes, and schema keys only; no nuPlan SQLite reads'} )
    return data


def main():
    p=argparse.ArgumentParser();p.add_argument('--data_root',required=True)
    p.add_argument('--output',required=True);p.add_argument('--max_nodes',type=int,default=25000)
    p.add_argument('--depth',type=int,default=6)
    a=p.parse_args()
    if not 3<=a.depth<=12 or a.max_nodes<100:p.error('invalid limits')
    o=inspect(Path(a.data_root),a.max_nodes,a.depth)
    dest=Path(a.output);dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(json.dumps(o,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps({'nodes_seen':o['nodes_seen'],'cutoff':o['cutoff'],'previewed_schemas':len(o['json_schema_previews'])}))
if __name__=='__main__':main()
