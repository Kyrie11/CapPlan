#!/usr/bin/env bash
set -euo pipefail
: "${CAP_HOME:=/home/senzeyu2/code/CapPlan}"
: "${V1_ROOT:=$CAP_HOME/outputs/eval/v1}"
: "${OUT_ZIP:=$CAP_HOME/outputs/eval/capplan_v1_results.zip}"
cd "$(dirname "$V1_ROOT")"
rm -f "$OUT_ZIP"
zip -qr "$OUT_ZIP" "$(basename "$V1_ROOT")"
echo "$OUT_ZIP"
