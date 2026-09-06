#!/usr/bin/env bash
set -euo pipefail
CAP_HOME="${CAP_HOME:-$(cd "$(dirname "$0")/.." && pwd)}"; V15_ROOT="${V15_ROOT:?set V15_ROOT}"; OUT_ZIP="${OUT_ZIP:-$CAP_HOME/outputs/eval/capplan_v15_results.zip}"
python - "$V15_ROOT" "$OUT_ZIP" <<'PY'
from pathlib import Path
import sys,zipfile
root=Path(sys.argv[1]); out=Path(sys.argv[2]); out.parent.mkdir(parents=True,exist_ok=True)
with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as z:
    for p in sorted(root.rglob('*')):
        if p.is_file(): z.write(p,p.relative_to(root.parent))
print(out)
PY
