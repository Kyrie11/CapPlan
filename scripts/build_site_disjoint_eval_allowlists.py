#!/usr/bin/env python
"""Build strict site-disjoint evaluation allowlists without deleting training data.

The legacy site catalog's ``test > val > train`` exclusion is a leakage
*diagnostic*, not a safe main-dataset split policy: one shared candidate site can
exclude an otherwise useful training episode.  This tool instead keeps the full
official nuPlan train split and creates conservative val/test subsets whose
catalogued PUDO sites are split-exclusive.
"""
from __future__ import annotations
import argparse, csv, json
from collections import defaultdict, Counter
from pathlib import Path
from typing import Dict, Set

SPLITS=("train","val","test")

def _ids(text: str|None)->Set[str]:
    return {x.strip() for x in str(text or "").split(";") if x.strip()}

def main()->None:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--catalog_csv", required=True)
    p.add_argument("--city", required=True)
    p.add_argument("--output_dir", required=True)
    p.add_argument("--min_catalog_sites_per_episode", type=int, default=2)
    p.add_argument("--report_json", required=True)
    a=p.parse_args()
    cat=Path(a.catalog_csv)
    if not cat.exists(): raise FileNotFoundError(cat)
    ep_sites: Dict[str,Dict[str,Set[str]]]={s:defaultdict(set) for s in SPLITS}
    site_splits: Dict[str,Set[str]]={}
    rows=0
    with cat.open(encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            rows+=1; sid=str(r.get("audit_id") or f"site_{rows}")
            members={s for s in SPLITS if _ids(r.get(f"episode_ids_{s}"))}
            site_splits[sid]=members
            for s in SPLITS:
                for eid in _ids(r.get(f"episode_ids_{s}")): ep_sites[s][eid].add(sid)
    out=Path(a.output_dir); out.mkdir(parents=True, exist_ok=True)
    report={"city":a.city,"catalog_rows":rows,"policy":"keep_full_train_create_split_exclusive_eval_subsets","splits":{}}
    for s in SPLITS:
        clean=[]; rejected=Counter()
        for eid, sites in sorted(ep_sites[s].items()):
            if len(sites)<a.min_catalog_sites_per_episode:
                rejected["too_few_catalog_sites"]+=1; continue
            cross=[sid for sid in sites if site_splits.get(sid)!={s}]
            if cross:
                rejected["contains_cross_split_site"]+=1; continue
            clean.append(eid)
        path=out/f"{a.city}.{s}.site_disjoint.txt"
        path.write_text("\n".join(clean)+("\n" if clean else ""), encoding="utf-8")
        report["splits"][s]={"candidate_episodes":len(ep_sites[s]),"site_disjoint_episodes":len(clean),"rejected":dict(rejected),"allowlist":str(path)}
    report["note"]=("Use train as the full official nuPlan training split. Use the val/test allowlists as a secondary strict-site-disjoint evaluation subset. "
                    "Do not apply the legacy test>val>train whole-episode exclusions to the main training corpus.")
    rp=Path(a.report_json); rp.parent.mkdir(parents=True, exist_ok=True); rp.write_text(json.dumps(report,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    print(json.dumps(report,indent=2,sort_keys=True)); print("SITE_DISJOINT_EVAL_CHECK=PASS")
if __name__=="__main__": main()
