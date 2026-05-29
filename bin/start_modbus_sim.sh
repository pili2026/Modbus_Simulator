#!/bin/bash

set -euo pipefail

PROJECT_DIR="/home/eversource/modbus_device_simulator"

TTY0="/tmp/ttyV0"
TTY1="/tmp/ttyV1"
LOG_FILE="/tmp/socat.log"

SOCAT_BIN="/usr/bin/socat"
PYTHON_BIN="/usr/bin/python3"

PYTHON_BIN="/home/eversource/venv/simulator/bin/python"

SOCAT_PID=""

cleanup() {
  exit_code=$?

  echo "[INFO] Cleaning up..."

  if [[ -n "${SOCAT_PID}" ]]; then
    kill "${SOCAT_PID}" 2>/dev/null || true
    wait "${SOCAT_PID}" 2>/dev/null || true
  fi

  rm -f "$TTY0" "$TTY1"

  echo "[INFO] Exit with code ${exit_code}."
  exit "$exit_code"
}

trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT

cd "$PROJECT_DIR"

echo "[INFO] Cleaning up previous virtual ports..."
rm -f "$TTY0" "$TTY1"

echo "[INFO] Starting socat for single Modbus bus RTU shared..."
"$SOCAT_BIN" -d -d \
  PTY,link="$TTY0",raw,echo=0 \
  PTY,link="$TTY1",raw,echo=0 \
  > "$LOG_FILE" 2>&1 &

SOCAT_PID=$!

echo "[INFO] Waiting for virtual ports to be ready..."

for i in {1..20}; do
  if [[ -e "$TTY0" && -e "$TTY1" ]]; then
    echo "[INFO] Virtual ports ready: $TTY0 <--> $TTY1"
    break
  fi

  if ! kill -0 "$SOCAT_PID" 2>/dev/null; then
    echo "[ERROR] socat exited unexpectedly."
    cat "$LOG_FILE" || true
    exit 1
  fi

  sleep 0.5
done

if [[ ! -e "$TTY0" || ! -e "$TTY1" ]]; then
  echo "[ERROR] Virtual ports were not created."
  cat "$LOG_FILE" || true
  exit 1
fi

echo "[INFO] $TTY0 -> $(readlink -f "$TTY0")"
echo "[INFO] $TTY1 -> $(readlink -f "$TTY1")"
echo "[INFO] Logs: $LOG_FILE"

echo "[INFO] Launching simulator using main.py with args: $*"

"$PYTHON_BIN" src/main.py "$@"