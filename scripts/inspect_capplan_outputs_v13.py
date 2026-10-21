#!/usr/bin/env python3
"""Bounded, read-only local CapPlan datasets/prepared directory census.

No heavy graph parsing, no raw episode/agent coordinates uploaded, no recursive
size walk. Results describe only the bounded directory prefix actually read.
"""
from __future__ import annotations
import argparse,json,os
from pathlib import Path


def safe_inventory(root,*,max_depth=2,max_entries_per_dir=200,small_json_bytes=1048576,count_episodes=False):
    root=Path(root)
    if not root.is_dir() or root.is_symlink():raise ValueError('outputs_root must be a non-symlink directory')
    result={'schema':'capplan_outputs_readonly_v13','root':str(root.resolve()),
            'inventory_semantics':'BOUNDED_PREFIX_NOT_FULL_RECURSIVE_SIZE',
            'max_depth':max_depth,'max_entries_per_dir':max_entries_per_dir,
            'missing_top_level_dirs':[],'directories':[],
            'symlinks_skipped':[],'warnings':[]}
    def inspect_dir(path,rel,depth):
        entry={'relative_path':str(rel) if str(rel)!='.' else '.', 'depth':depth,
               'entries_scanned':0,'entries_truncated':False,'file_examples':[],
               'subdirectories':[],'manifest_keys':{},'episodes_jsonl_line_count':None}
        result['directories'].append(entry)
        items=[]
        # Iterate at most max_entries+1 rather than materializing huge graph dirs.
        with os.scandir(path) as it:
            for item in it:
                items.append(item)
                if len(items)>max_entries_per_dir:entry['entries_truncated']=True;break
        items=sorted(items[:max_entries_per_dir],key=lambda e:e.name)
        entry['entries_scanned']=len(items)
        for item in items:
            relative=rel/item.name
            if item.is_symlink():
                result['symlinks_skipped'].append(str(relative));continue
            if item.is_dir(follow_symlinks=False):
                entry['subdirectories'].append(item.name)
                if depth<max_depth:inspect_dir(Path(item.path),relative,depth+1)
            elif item.is_file(follow_symlinks=False):
                stat=item.stat(follow_symlinks=False)
                entry['file_examples'].append({'filename':item.name,'bytes':stat.st_size})
                if item.name.endswith('.json') and stat.st_size<=small_json_bytes and any(k in item.name.lower() for k in ('manifest','quality','audit','summary','metadata')):
                    try:
                        data=json.loads(Path(item.path).read_text(encoding='utf8'))
                        entry['manifest_keys'][item.name]=sorted(str(k) for k in data.keys()) if isinstance(data,dict) else ['NON_OBJECT_JSON']
                    except Exception as exc:entry['manifest_keys'][item.name]=['UNREADABLE:'+type(exc).__name__]
                if item.name=='episodes.jsonl' and count_episodes:
                    with open(item.path,'rb') as fp:
                        entry['episodes_jsonl_line_count']=sum(bool(line.strip()) for line in fp)
        if entry['entries_truncated']:
            result['warnings'].append(f"TRUNCATED_DIRECTORY:{relative if not items else entry['relative_path']}")
    for kind in ('datasets','prepared'):
        p=root/kind
        if p.is_dir() and not p.is_symlink():inspect_dir(p,Path(kind),0)
        else:result['missing_top_level_dirs'].append(kind)
    return result


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--outputs_root',required=True)
    p.add_argument('--output',required=True);p.add_argument('--max_depth',type=int,default=2)
    p.add_argument('--max_entries_per_dir',type=int,default=200)
    p.add_argument('--count_episodes',action='store_true')
    a=p.parse_args()
    if not (0<=a.max_depth<=4 and 10<=a.max_entries_per_dir<=5000):p.error('invalid bounds')
    data=safe_inventory(a.outputs_root,max_depth=a.max_depth,max_entries_per_dir=a.max_entries_per_dir,
                        count_episodes=a.count_episodes)
    out=Path(a.output);out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'directories_scanned':len(data['directories']),
        'missing_top_level_dirs':data['missing_top_level_dirs'],
        'truncations':len(data['warnings']),'output':str(out)}))
if __name__=='__main__':main()
