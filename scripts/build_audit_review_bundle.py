#!/usr/bin/env python
"""Build a small upload-friendly PUDO audit review bundle (no NPZ/full dataset)."""
from __future__ import annotations
import argparse, csv, json, shutil, zipfile
from pathlib import Path
from typing import List

CITIES=("boston","pittsburgh","vegas","singapore")
CSV_NAMES=(
 "new_evidence_required.csv","visual_review_required.csv","source_complete_review_candidates.csv",
 "machine_pass_explicit_authoritative.csv","machine_reject_invalid_or_ambiguous.csv","pudo_audit_machine_triage.csv",
)

def _copy_csv_sample(src: Path, dst: Path, n: int) -> int:
    if not src.exists(): return 0
    with src.open("r",encoding="utf-8-sig",newline="") as f:
        r=csv.DictReader(f); fields=list(r.fieldnames or []); rows=[]
        for i,row in enumerate(r):
            if n>0 and i>=n: break
            rows.append(row)
    if not fields: return 0
    dst.parent.mkdir(parents=True,exist_ok=True)
    with dst.open("w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=fields,extrasaction="ignore"); w.writeheader(); w.writerows(rows)
    return len(rows)

def main()->None:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--external_root",required=True); p.add_argument("--reports_root",required=True)
    p.add_argument("--output_zip",required=True); p.add_argument("--max_rows",type=int,default=100); p.add_argument("--max_images",type=int,default=24)
    args=p.parse_args(); ext=Path(args.external_root); reports=Path(args.reports_root)
    stage=reports/"audit_review_bundle"; shutil.rmtree(stage,ignore_errors=True); stage.mkdir(parents=True)
    manifest={"max_rows_per_csv":args.max_rows,"max_images_per_city":args.max_images,"cities":{}}
    # small JSON diagnostics
    for pat in ("pudo_audit_*.json","external.*.json","recommended_public_sources.json"):
        for src in reports.glob(pat):
            if src.is_file() and src.stat().st_size < 5_000_000:
                dst_dir = stage / "reports"
                dst_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst_dir / src.name)
    for city in CITIES:
        rec={"csv_samples":{},"images":[]}; city_dir=stage/"audits"/city
        for name in CSV_NAMES:
            n=_copy_csv_sample(ext/"audits"/city/name,city_dir/name,args.max_rows); rec["csv_samples"][name]=n
        image_sources=[ext/"audits"/city/"visual_packets", reports/"audit_packets"/city/"visual", reports/"audit_packets"/city/"evidence_gap"]
        copied=0
        for srcdir in image_sources:
            if not srcdir.exists(): continue
            idx=srcdir/"index.html"
            if idx.exists():
                dst=city_dir/"packets"/srcdir.name/"index.html"; dst.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(idx,dst)
            for img in sorted(srcdir.glob("*.png")):
                if args.max_images>0 and copied>=args.max_images: break
                dst=city_dir/"packets"/srcdir.name/img.name; dst.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(img,dst); rec["images"].append(str(dst.relative_to(stage))); copied+=1
            if args.max_images>0 and copied>=args.max_images: break
        manifest["cities"][city]=rec
    (stage/"README.txt").write_text(
        "CapPlan PUDO audit review bundle. CSV files are capped samples and PNGs are topology/context aids.\n"
        "A spatially nearby feature is not automatically a legal stop, deployment clearance measurement, or intended service entrance.\n",
        encoding="utf-8")
    (stage/"manifest.json").write_text(json.dumps(manifest,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    out=Path(args.output_zip); out.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(out,"w",zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for f in sorted(stage.rglob("*")):
            if f.is_file(): z.write(f,arcname=str(f.relative_to(stage)))
    print(json.dumps({"status":"PASS","output_zip":str(out),"bytes":out.stat().st_size,"staging_dir":str(stage)},indent=2)); print("PUDO_AUDIT_REVIEW_BUNDLE=PASS")
if __name__=="__main__": main()
