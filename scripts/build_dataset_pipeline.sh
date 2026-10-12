#!/usr/bin/env bash
# Stable external entry point. Dated build implementation remains pinned
# internally to avoid accidental provenance changes.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
exec bash scripts/build_abilitybench_data0_20260817.sh "$@"
