#!/usr/bin/env bash
set -euo pipefail
CAP_HOME="${CAP_HOME:-/home/senzeyu2/code/CapPlan}"
SEED="${SEED:-13}"
V12_ROOT="${V12_ROOT:-$CAP_HOME/outputs/eval/v12_fast_seed${SEED}}"
OUT_ZIP="${OUT_ZIP:-$CAP_HOME/outputs/eval/capplan_v12_results.zip}"
[[ -d "$V12_ROOT" ]] || { echo "missing V12_ROOT: $V12_ROOT" >&2; exit 2; }
mkdir -p "$(dirname "$OUT_ZIP")"
python - "$V12_ROOT" "$OUT_ZIP" <<'PY'
from pathlib import Path
import sys, zipfile
root=Path(sys.argv[1]).resolve(); out=Path(sys.argv[2]).resolve()
with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
    for p in sorted(root.rglob('*')):
        if p.is_file(): z.write(p,arcname=str(Path(root.name)/p.relative_to(root)))
print(out)
PY
echo "V12_RESULTS_ZIP=$OUT_ZIP"
