#!/usr/bin/env bash
# Stable wrapper: implementation-specific historical entrypoint is intentionally
# hidden; the pinned experiment signature and baseline remain reproducible.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
if [[ "${REGRESSION_SCOPE:-fast}" == full ]]; then
  exec bash scripts/run_v16_full_experiments.sh
else
  exec bash scripts/run_v16_fast_experiments.sh
fi
