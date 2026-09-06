#!/usr/bin/env bash
set -euo pipefail
CAP_HOME="${CAP_HOME:-/home/senzeyu2/code/CapPlan}"
SEED="${SEED:-13}"
V13_ROOT="${V13_ROOT:-$CAP_HOME/outputs/eval/v13_fast_seed${SEED}}"
OUT_ZIP="${OUT_ZIP:-$CAP_HOME/outputs/eval/capplan_v13_results.zip}"
[[ -d "$V13_ROOT" ]] || { echo "missing V13_ROOT: $V13_ROOT" >&2; exit 2; }
mkdir -p "$(dirname "$OUT_ZIP")"
python - "$V13_ROOT" "$OUT_ZIP" <<'PY'
from pathlib import Path
import sys,zipfile
root=Path(sys.argv[1]).resolve(); out=Path(sys.argv[2]).resolve()
with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
    for p in sorted(root.rglob('*')):
        if p.is_file(): z.write(p,arcname=str(Path(root.name)/p.relative_to(root)))
print(out)
PY
echo "V13_RESULTS_ZIP=$OUT_ZIP"
