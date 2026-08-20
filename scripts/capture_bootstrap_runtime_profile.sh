#!/usr/bin/env bash
set -euo pipefail

INTERVAL="${1:-30}"
OUTPUT="${2:-bootstrap_runtime_profile.log}"
IDLE_GRACE_SAMPLES="${CAP_PROFILE_IDLE_GRACE_SAMPLES:-3}"
AUTO_STOP="${CAP_PROFILE_AUTO_STOP:-1}"
CAP_DATA="${CAP_DATA:-${DATA_ROOT:-/data0/senzeyu2/dataset/CapPlan/data}}"
[[ "$INTERVAL" =~ ^[1-9][0-9]*$ ]] || { echo "interval must be integer seconds" >&2; exit 2; }
[[ "$IDLE_GRACE_SAMPLES" =~ ^[1-9][0-9]*$ ]] || { echo "CAP_PROFILE_IDLE_GRACE_SAMPLES must be a positive integer" >&2; exit 2; }
[[ "$AUTO_STOP" == "0" || "$AUTO_STOP" == "1" ]] || { echo "CAP_PROFILE_AUTO_STOP must be 0 or 1" >&2; exit 2; }

# Match only the actual build/orchestrator processes, not this sampler.
PATTERN='scripts/(prepare_abilitybench_external.py|extract_nuplan_scenes.py|build_accessibility_graphs.py|build_pudo_evidence.py)|scripts/build_abilitybench_data0_20260817.sh'
seen_active=0
idle_after_active=0

{
  echo "# CapPlan bootstrap runtime profile started $(date -Is) interval=${INTERVAL}s host=$(hostname)"
  echo "# auto_stop=${AUTO_STOP} idle_grace_samples=${IDLE_GRACE_SAMPLES}"
  echo "# The parser considers only CAPPLAN_ACTIVE=1 samples; post-build idle samples are excluded."
  echo "# storage context"
  findmnt -T "$CAP_DATA" 2>/dev/null || true
  df -T "$CAP_DATA" 2>/dev/null || true
  lsblk -o NAME,TYPE,SIZE,FSTYPE,MOUNTPOINTS,ROTA,MODEL 2>/dev/null || true
} | tee "$OUTPUT"

while true; do
  process_lines="$(ps -eo pid,ppid,psr,pcpu,pmem,rss,etime,stat,cmd --sort=-pcpu \
      | grep -E "$PATTERN" | grep -v grep || true)"
  if [[ -n "$process_lines" ]]; then
    active=1
    seen_active=1
    idle_after_active=0
  else
    active=0
    if (( seen_active )); then
      idle_after_active=$((idle_after_active + 1))
    fi
  fi

  {
    echo
    echo "===== SAMPLE $(date -Is) ====="
    echo "CAPPLAN_ACTIVE=$active SEEN_ACTIVE=$seen_active IDLE_AFTER_ACTIVE=$idle_after_active"
    echo "--- load / memory ---"
    uptime || true
    free -h || true
    echo "--- CapPlan processes ---"
    if [[ -n "$process_lines" ]]; then
      echo "    PID    PPID PSR %CPU %MEM   RSS     ELAPSED STAT CMD"
      echo "$process_lines"
    fi
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

  if [[ "$AUTO_STOP" == "1" ]] && (( seen_active )) && (( idle_after_active >= IDLE_GRACE_SAMPLES )); then
    echo "# CapPlan profiler auto-stop $(date -Is): build absent for ${IDLE_GRACE_SAMPLES} consecutive samples." | tee -a "$OUTPUT"
    break
  fi
  sleep "$INTERVAL"
done
