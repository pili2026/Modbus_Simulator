#!/bin/bash

set -euo pipefail

TTY0="/tmp/ttyV0"
TTY1="/tmp/ttyV1"
LOG_FILE="/tmp/socat.log"

echo "[INFO] Cleaning up previous virtual ports..."
rm -f "$TTY0" "$TTY1"

echo "[INFO] Starting socat for single Modbus bus (RTU shared)..."
socat -d -d PTY,link="$TTY0",raw,echo=0 PTY,link="$TTY1",raw,echo=0 &> "$LOG_FILE" &
SOCAT_PID=$!

cleanup() {
  echo "[INFO] Cleaning up..."
  kill "$SOCAT_PID" 2>/dev/null || true
  wait "$SOCAT_PID" 2>/dev/null || true
  echo "[INFO] Exit."
  exit 0
}

trap cleanup INT TERM EXIT

# Wait for socat to successfully create the virtual ports
echo "[INFO] Waiting for virtual ports to be ready..."
for i in {1..10}; do
  if [[ -e "$TTY0" && -e "$TTY1" ]]; then
    echo "[INFO] Virtual ports ready: $TTY0 <--> $TTY1"
    break
  fi
  sleep 0.5
done

# Show the actual device paths
echo "[INFO] $TTY0 → $(readlink -f "$TTY0")"
echo "[INFO] $TTY1 → $(readlink -f "$TTY1")"
echo "[INFO] Logs: $LOG_FILE"

echo "[INFO] Launching simulator using main.py"
python3 src/main.py 

# Keep the script running in the background
while true; do
  sleep 1
done
