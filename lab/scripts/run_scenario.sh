#!/usr/bin/env bash
# Run a single fault scenario and write its evidence.
#
# Usage:
#   lab/scripts/run_scenario.sh <scenario-id> [OUT_DIR]
#   e.g. lab/scripts/run_scenario.sh S2-conn-pool-exhaustion
set -euo pipefail

if [ "$#" -lt 1 ]; then
  echo "usage: $0 <scenario-id> [OUT_DIR]" >&2
  echo "available ids:"
  python3 - "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)" <<'PY'
import json, os, sys
lab = sys.argv[1]
import importlib.util
spec = importlib.util.spec_from_file_location("scen", os.path.join(lab, "sim", "scenarios.py"))
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
for sid in mod.ALL_IDS:
    print("  -", sid)
PY
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT_DIR="$(cd "$LAB_DIR/.." && pwd)"

SCENARIO="$1"
OUT="${2:-$ROOT_DIR/evidence}"

echo "[run_scenario] scenario : $SCENARIO"
echo "[run_scenario] out dir  : $OUT"
python3 "$LAB_DIR/sim/runner.py" --scenario "$SCENARIO" --out "$OUT"
