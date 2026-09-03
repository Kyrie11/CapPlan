#!/usr/bin/env bash
set -euo pipefail
: "${CAP_HOME:=/home/senzeyu2/code/CapPlan}"
: "${SEED:=13}"
: "${V6_ROOT:=$CAP_HOME/outputs/eval/v6_fast_seed${SEED}}"
: "${OUT_ZIP:=$CAP_HOME/outputs/eval/capplan_v6_fast_seed${SEED}_results.zip}"
[[ -d "$V6_ROOT" ]] || { echo "missing V6_ROOT: $V6_ROOT" >&2; exit 2; }
cd "$(dirname "$V6_ROOT")"
rm -f "$OUT_ZIP"
zip -qr "$OUT_ZIP" "$(basename "$V6_ROOT")"
echo "$OUT_ZIP"
