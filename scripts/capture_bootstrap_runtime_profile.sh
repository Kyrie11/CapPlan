#!/usr/bin/env bash
set -euo pipefail

INTERVAL="${1:-30}"
OUTPUT="${2:-bootstrap_runtime_profile.log}"
[[ "$INTERVAL" =~ ^[1-9][0-9]*$ ]] || { echo "interval must be integer seconds" >&2; exit 2; }

echo "# CapPlan bootstrap runtime profile started $(date -Is) interval=${INTERVAL}s host=$(hostname)" | tee "$OUTPUT"
echo "# Stop with Ctrl-C. This sampler does not modify the running build." | tee -a "$OUTPUT"
while true; do
  {
    echo
    echo "===== SAMPLE $(date -Is) ====="
    echo "--- load / memory ---"
    uptime || true
    free -h || true
    echo "--- CapPlan processes ---"
    ps -eo pid,ppid,psr,pcpu,pmem,rss,etime,stat,cmd --sort=-pcpu \
      | grep -E 'PID|prepare_abilitybench_external|extract_nuplan_scenes|build_accessibility_graphs|build_pudo_evidence|build_abilitybench_data0' \
      | grep -v grep || true
    echo "--- vmstat ---"
    vmstat 1 2 2>/dev/null | tail -n 2 || true
    if command -v iostat >/dev/null 2>&1; then
      echo "--- iostat ---"
      iostat -dx 1 2 2>/dev/null | tail -n 40 || true
    fi
    if command -v numastat >/dev/null 2>&1; then
      echo "--- numa ---"
      numastat -m 2>/dev/null | grep -E 'MemTotal|MemFree|Active|Inactive|FilePages' || true
    fi
  } | tee -a "$OUTPUT"
  sleep "$INTERVAL"
done
