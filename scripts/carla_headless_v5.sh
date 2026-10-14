#!/usr/bin/env bash
# Starts CARLA 0.9.15 on a headless Linux server; logs PID and stderr.
set -euo pipefail
CARLA_ROOT="${CARLA_ROOT:-/data0/senzeyu2/dataset/CRADrive/CARLA_0.9.15}"
PORT="${CARLA_PORT:-2000}"
OUT="${CARLA_LOG_DIR:-$PWD/outputs/carla_handoff_v5}"
mkdir -p "$OUT"
SERVER="$CARLA_ROOT/CarlaUE4.sh"
if [[ ! -f "$SERVER" ]]; then echo "CARLA launcher not found: $SERVER" >&2; exit 2; fi
if ! command -v nvidia-smi >/dev/null; then echo 'WARN: nvidia-smi missing; offscreen Vulkan may not start' >&2; fi
if command -v nvidia-smi >/dev/null; then nvidia-smi --query-gpu=name,memory.free --format=csv || true; fi
if [[ -f "$OUT/server.pid" ]] && kill -0 "$(cat "$OUT/server.pid")" 2>/dev/null; then echo "CARLA already running as PID $(cat "$OUT/server.pid")"; exit 0; fi
cd "$CARLA_ROOT"
export SDL_VIDEODRIVER="${SDL_VIDEODRIVER:-offscreen}"
nohup bash "$SERVER" -RenderOffScreen -quality-level=Low -carla-rpc-port="$PORT" -nosound > "$OUT/server.log" 2>&1 &
echo $! > "$OUT/server.pid"
echo "Launched CARLA PID $(cat "$OUT/server.pid") RPC port $PORT. Inspect $OUT/server.log and run client probe before experiments."
