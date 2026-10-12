#!/usr/bin/env python3
"""Archive obsolete historical experiment ENTRYPOINTS without touching code/data.

Core capplan modules, latest regression path, dataset-build scripts, data
configs, all tests and provenance documents are protected.  Dry-run by default.
Archive retains byte-identical originals and SHA256 for safe restoration.
"""
from __future__ import annotations
import argparse,hashlib,json,re,zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
ARCHIVE=ROOT/'legacy'/'historical_entrypoints.zip'
PATTERN=re.compile(r'(?:run|assess|pack|summarize|compare)_v(?:[1-9]|1[0-5])(?:_|\.)')


def candidates():
    return sorted(p for p in (ROOT/'scripts').iterdir() if p.is_file() and PATTERN.match(p.name))


def usage_guard(removed):
    # Dependency scan is conservative: failure to prove non-reference aborts.
    others=[p for d in ('capplan','tests','scripts') for p in (ROOT/d).rglob('*')
            if p.is_file() and p not in removed and p.suffix in ('.py','.sh')]
    problems=[]
    for p in removed:
        for user in others:
            if p.name in user.read_text(encoding='utf-8',errors='replace'):
                problems.append((str(p.relative_to(ROOT)),str(user.relative_to(ROOT))))
    return problems


def run(mode):
    if mode=='restore':
        if not ARCHIVE.is_file():raise FileNotFoundError(ARCHIVE)
        with zipfile.ZipFile(ARCHIVE) as z:
            manifest=json.loads(z.read('manifest.json'))
            for path, expected in manifest['sha256'].items():
                dest=ROOT/path
                if dest.exists():
                    if hashlib.sha256(dest.read_bytes()).hexdigest()!=expected:
                        raise ValueError(f'restore would overwrite changed file: {path}')
                    continue
                data=z.read(path)
                if hashlib.sha256(data).hexdigest()!=expected:
                    raise ValueError('archive hash mismatch: '+path)
                dest.parent.mkdir(parents=True,exist_ok=True);dest.write_bytes(data)
        return {'restored':len(manifest['sha256'])}
    paths=candidates()
    problems=usage_guard(set(paths))
    if problems:
        raise RuntimeError(f'active runtime references old entrypoints: {problems[:12]}')
    report={'mode':mode,'candidate_count':len(paths),'protected':['capplan/','tests/','configs/','docs/',
              'scripts/build_abilitybench_data0_20260817.sh','scripts/run_v16_*','scripts/run_handoff_funnel.sh'],
            'files':[str(x.relative_to(ROOT)) for x in paths]}
    if mode=='apply' and paths:
        ARCHIVE.parent.mkdir(exist_ok=True)
        hashes={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        if ARCHIVE.exists():raise FileExistsError('prior archive exists; restore or preserve before applying again')
        try:
            with zipfile.ZipFile(ARCHIVE,'w',zipfile.ZIP_DEFLATED) as z:
                for p in paths:z.write(p,p.relative_to(ROOT))
                z.writestr('manifest.json',json.dumps({'sha256':hashes},indent=2))
            with zipfile.ZipFile(ARCHIVE) as z:
                for path,expect in hashes.items():
                    if hashlib.sha256(z.read(path)).hexdigest()!=expect:
                        raise IOError('archive checksum error')
        except Exception:
            ARCHIVE.unlink(missing_ok=True)
            raise
        for p in paths:p.unlink()
        report['archive']=str(ARCHIVE.relative_to(ROOT))
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--apply',action='store_true');p.add_argument('--restore',action='store_true')
    a=p.parse_args()
    if a.apply and a.restore:p.error('choose one')
    print(json.dumps(run('restore' if a.restore else ('apply' if a.apply else 'dry_run')),indent=2))
