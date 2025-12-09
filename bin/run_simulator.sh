#!/bin/bash
set -euo pipefail

TTY_SIM="${MODBUS_PORT:-/dev/ttyMODBUS1}"

echo "[INFO] Using Modbus RTU port: $TTY_SIM"

export PYTHONUNBUFFERED=1
export MODBUS_PORT="$TTY_SIM"

echo "[INFO] Launching simulator using main.py"
python3 src/main.py
