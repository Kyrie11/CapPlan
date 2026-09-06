#!/usr/bin/env bash
set -euo pipefail
CAP_HOME="${CAP_HOME:-$(cd "$(dirname "$0")/.." && pwd)}"
SEED="${SEED:-13}"
V14_ROOT="${V14_ROOT:-$CAP_HOME/outputs/eval/v14_fast_seed${SEED}}"
MODEL_ROOT="${MODEL_ROOT:-$CAP_HOME/outputs/models/v14_cqhpt_seed${SEED}}"
TEACHER_ROOT="${TEACHER_ROOT:-$CAP_HOME/outputs/teachers/v14_cqhpt_seed${SEED}}"
OUT_ZIP="${OUT_ZIP:-$CAP_HOME/outputs/eval/capplan_v14_fast_seed${SEED}_results.zip}"
[[ -d "$V14_ROOT" ]] || { echo "missing V14_ROOT: $V14_ROOT" >&2; exit 2; }
mkdir -p "$(dirname "$OUT_ZIP")"
python - "$V14_ROOT" "$MODEL_ROOT" "$TEACHER_ROOT" "$OUT_ZIP" <<'PY'
from pathlib import Path
import sys, zipfile
root=Path(sys.argv[1]).resolve(); models=Path(sys.argv[2]).resolve(); teachers=Path(sys.argv[3]).resolve(); out=Path(sys.argv[4]).resolve()
with zipfile.ZipFile(out,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as z:
    for p in sorted(root.rglob('*')):
        if p.is_file(): z.write(p,arcname=str(Path(root.name)/p.relative_to(root)))
    # Keep the result bundle small: training summaries and teacher summaries are
    # sufficient for attribution; checkpoints/teacher rows stay on the server.
    for mode in ('full','late_fusion','neutral_query','query_only'):
        p=models/mode/'training_summary.json'
        if p.exists(): z.write(p,arcname=str(Path('training_summaries')/mode/'training_summary.json'))
    if teachers.exists():
        for p in sorted(teachers.glob('*.summary.json')):
            z.write(p,arcname=str(Path('teacher_summaries')/p.name))
print(out)
PY
echo "V14_RESULTS_ZIP=$OUT_ZIP"
