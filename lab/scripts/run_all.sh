#!/usr/bin/env bash
# Run every fault scenario and write evidence to <repo>/evidence.
#
# Usage:
#   lab/scripts/run_all.sh [OUT_DIR]
#
# 100% standard-library Python: no docker / kubectl / third-party packages
# required. Each scenario runs 240-300 ticks of 1 simulated second each and
# completes in a few seconds.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT_DIR="$(cd "$LAB_DIR/.." && pwd)"

OUT="${1:-$ROOT_DIR/evidence}"

echo "[run_all] lab dir : $LAB_DIR"
echo "[run_all] out dir : $OUT"
python3 "$LAB_DIR/sim/runner.py" --all --out "$OUT"
