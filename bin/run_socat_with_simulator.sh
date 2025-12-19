#!/bin/bash
set -euo pipefail

TTY0="/dev/ttyMODBUS0"
TTY1="/dev/ttyMODBUS1"
LOG_FILE="/tmp/socat.log"

echo "[INFO] Cleaning up previous virtual ports..."
sudo rm -f "$TTY0" "$TTY1" 2>/dev/null || true

echo "[INFO] Starting socat for single Modbus bus (RTU shared)..."
sudo socat -d -d \
  PTY,link="$TTY0",raw,echo=0,mode=666 \
  PTY,link="$TTY1",raw,echo=0,mode=666 \
  &> "$LOG_FILE" &
SOCAT_PID=$!

cleanup() {
  echo "[INFO] Cleaning up..."
  sudo kill "$SOCAT_PID" 2>/dev/null || true
  sudo wait "$SOCAT_PID" 2>/dev/null || true
  sudo rm -f "$TTY0" "$TTY1" 2>/dev/null || true
  echo "[INFO] Exit."
}
trap cleanup INT TERM EXIT

echo "[INFO] Waiting for virtual ports to be ready..."
for i in {1..20}; do
  if [[ -e "$TTY0" && -e "$TTY1" ]]; then
    break
  fi
  sleep 0.2
done

if [[ ! -e "$TTY0" || ! -e "$TTY1" ]]; then
  echo "[ERROR] socat failed to create virtual ports. Check log: $LOG_FILE"
  exit 1
fi

echo "[INFO] Virtual ports ready: $TTY0 <--> $TTY1"
echo "[INFO] $TTY0 → $(readlink -f "$TTY0")"
echo "[INFO] $TTY1 → $(readlink -f "$TTY1")"
echo "[INFO] Logs: $LOG_FILE"

export PYTHONUNBUFFERED=1

echo "[INFO] Launching simulator using main.py"
python3 src/main.py
