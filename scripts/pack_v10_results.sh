#!/usr/bin/env bash
set -euo pipefail
CAP_HOME="${CAP_HOME:-/home/senzeyu2/code/CapPlan}"
V10_ROOT="${V10_ROOT:-$CAP_HOME/outputs/eval/v10_fast_seed13}"
OUT_ZIP="${OUT_ZIP:-$CAP_HOME/outputs/eval/capplan_v10_results.zip}"
mkdir -p "$(dirname "$OUT_ZIP")"
python - "$V10_ROOT" "$OUT_ZIP" <<'PY'
import sys,zipfile
from pathlib import Path
root=Path(sys.argv[1]); out=Path(sys.argv[2])
if not root.exists(): raise SystemExit(f'missing {root}')
with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED,compresslevel=6) as z:
    for p in sorted(root.rglob('*')):
        if p.is_file(): z.write(p,Path(root.name)/p.relative_to(root))
print(out)
PY
