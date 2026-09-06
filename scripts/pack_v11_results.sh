#!/usr/bin/env bash
set -euo pipefail
CAP_HOME="${CAP_HOME:-/home/senzeyu2/code/CapPlan}"
V11_ROOT="${V11_ROOT:-$CAP_HOME/outputs/eval/v11_fast_seed13}"
OUT_ZIP="${OUT_ZIP:-$CAP_HOME/outputs/eval/capplan_v11_results.zip}"
python - "$V11_ROOT" "$OUT_ZIP" <<'PY'
from pathlib import Path
import sys, zipfile
root=Path(sys.argv[1]); out=Path(sys.argv[2]); out.parent.mkdir(parents=True,exist_ok=True)
with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
    for p in sorted(root.rglob('*')):
        if p.is_file(): z.write(p,p.relative_to(root.parent))
print(out)
PY
