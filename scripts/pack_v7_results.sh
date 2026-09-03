#!/usr/bin/env bash
set -euo pipefail
CAP_HOME="${CAP_HOME:-/home/senzeyu2/code/CapPlan}"
V7_ROOT="${V7_ROOT:-$CAP_HOME/outputs/eval/v7_fast_seed13}"
OUT_ZIP="${OUT_ZIP:-$CAP_HOME/outputs/eval/capplan_v7_fast_seed13_results.zip}"
mkdir -p "$(dirname "$OUT_ZIP")"
cd "$(dirname "$V7_ROOT")"
base="$(basename "$V7_ROOT")"
rm -f "$OUT_ZIP"
zip -qr "$OUT_ZIP" "$base"
echo "$OUT_ZIP"
