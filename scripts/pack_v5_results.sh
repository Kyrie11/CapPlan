#!/usr/bin/env bash
set -euo pipefail
: "${CAP_HOME:=/home/senzeyu2/code/CapPlan}"
: "${SEED:=13}"
: "${V5_ROOT:=$CAP_HOME/outputs/eval/v5_fast_seed${SEED}}"
: "${OUT_ZIP:=$CAP_HOME/outputs/eval/capplan_v5_fast_seed${SEED}_results.zip}"
[[ -d "$V5_ROOT" ]] || { echo "missing V5_ROOT: $V5_ROOT" >&2; exit 2; }
cd "$(dirname "$V5_ROOT")"
rm -f "$OUT_ZIP"
zip -qr "$OUT_ZIP" "$(basename "$V5_ROOT")"
echo "$OUT_ZIP"
