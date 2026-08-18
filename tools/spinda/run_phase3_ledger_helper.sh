#!/usr/bin/env bash
set -euo pipefail

# Linux helper-node launcher. It claims lanes from the coordinator ledger, then
# runs only the native Phase 3 CLI worker path. Qt is intentionally unused.
#
# Variables before `--` below are consumed by the ledger client. Variables after
# `--` are forwarded to native_phase3_worker_pool.py for each claimed batch.
# `--output-dir` is passed only to the ledger client; that client forwards it to
# the worker pool, and also uses it to decide whether claimed ZIPs finished.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd)"

PYTHON_EXE="${PYTHON_EXE:-python3}"
COORDINATOR_URL="${COORDINATOR_URL:-http://127.0.0.1:235}"
DEVICE_ID="${DEVICE_ID:-$(hostname)}"
WORKER_ID="${WORKER_ID:-linux-ledger-worker}"
LANES="${LANES:-0x0000-0xFFFF}"
WORKERS="${WORKERS:-6}"
BATCH_SIZE="${BATCH_SIZE:-24}"
BUNDLE_SIZE="${BUNDLE_SIZE:-2}"
LEASE_SECONDS="${LEASE_SECONDS:-21600}"
HEARTBEAT_SECONDS="${HEARTBEAT_SECONDS:-60}"
STATUS_WRITE_SECONDS="${STATUS_WRITE_SECONDS:-30}"

PHASE3_CLI_EXE="${PHASE3_CLI_EXE:-$ROOT/build-linux-spinda-cli/mgba-spinda-phase3}"
ROM="${ROM:-$ROOT/inputs/lg.gba}"
PHASE2_DIR="${PHASE2_DIR:-$ROOT/Phase2PickupStates}"
SECONDHALF_CSV="${SECONDHALF_CSV:-$ROOT/inputs/secondhalf.csv}"
OUTPUT_DIR="${OUTPUT_DIR:-$ROOT/Phase3SpindaBlocks}"
CACHE_DIR="${CACHE_DIR:-$OUTPUT_DIR/_cache}"

if [[ ! -x "$PHASE3_CLI_EXE" ]]; then
    echo "Phase 3 CLI missing or not executable: $PHASE3_CLI_EXE" >&2
    echo "Build it with: bash tools/spinda/build_phase3_cli_linux.sh" >&2
    exit 2
fi
if [[ ! -f "$ROM" ]]; then
    echo "ROM missing: $ROM" >&2
    exit 2
fi
if [[ ! -d "$PHASE2_DIR" ]]; then
    echo "Phase 2 state folder missing: $PHASE2_DIR" >&2
    exit 2
fi
if [[ ! -f "$SECONDHALF_CSV" ]]; then
    echo "secondhalf.csv missing: $SECONDHALF_CSV" >&2
    exit 2
fi

mkdir -p "$OUTPUT_DIR" "$CACHE_DIR"

echo "Linux Phase 3 helper node"
echo "Coordinator: $COORDINATOR_URL"
echo "Device: $DEVICE_ID"
echo "Lanes: $LANES"
echo "Workers: $WORKERS"
echo "Batch size: $BATCH_SIZE"
echo "Bundle size: $BUNDLE_SIZE"
echo "CLI: $PHASE3_CLI_EXE"
echo "Output: $OUTPUT_DIR"

exec "$PYTHON_EXE" "$ROOT/tools/spinda/phase3_ledger_worker_client.py" \
    --coordinator-url "$COORDINATOR_URL" \
    --device-id "$DEVICE_ID" \
    --worker-id "$WORKER_ID" \
    --lanes "$LANES" \
    --batch-size "$BATCH_SIZE" \
    --workers "$WORKERS" \
    --bundle-size "$BUNDLE_SIZE" \
    --lease-seconds "$LEASE_SECONDS" \
    --heartbeat-seconds "$HEARTBEAT_SECONDS" \
    --output-dir "$OUTPUT_DIR" \
    -- \
    --runner cli \
    --phase3-cli-exe "$PHASE3_CLI_EXE" \
    --rom "$ROM" \
    --phase2-dir "$PHASE2_DIR" \
    --secondhalf-csv "$SECONDHALF_CSV" \
    --cache-dir "$CACHE_DIR" \
    --status-write-seconds "$STATUS_WRITE_SECONDS" \
    "$@"
