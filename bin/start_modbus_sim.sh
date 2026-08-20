#!/bin/bash

set -Eeuo pipefail

ROOT="/home/eversource/modbus_device_simulator"
PYTHON="/home/eversource/venv/simulator/bin/python"
SOCAT="/usr/bin/socat"

TTY0="/tmp/ttyV0"
TTY1="/tmp/ttyV1"
SOCAT_LOG="/tmp/modbus-sim-socat.log"

CONFIG="res/sites/first_enterprise.yml"
SCENARIO="res/scenarios/first_enterprise_happy_path.yml"

SOCAT_PID=""
SIM_PID=""

cleanup() {
  trap - EXIT INT TERM

  echo "[INFO] Cleaning up First Enterprise simulator..."

  if [[ -n "${SIM_PID}" ]]; then
    kill "${SIM_PID}" 2>/dev/null || true
    wait "${SIM_PID}" 2>/dev/null || true
  fi

  if [[ -n "${SOCAT_PID}" ]]; then
    kill "${SOCAT_PID}" 2>/dev/null || true
    wait "${SOCAT_PID}" 2>/dev/null || true
  fi

  rm -f "${TTY0}" "${TTY1}"

  echo "[INFO] Cleanup complete."
}

handle_signal() {
  echo "[INFO] Stop signal received."
  cleanup
  exit 0
}

trap handle_signal INT TERM
trap cleanup EXIT

cd "${ROOT}"

echo "[INFO] Cleaning stale virtual ports..."
rm -f "${TTY0}" "${TTY1}"

echo "[INFO] Starting socat..."
"${SOCAT}" -d -d \
  PTY,link="${TTY0}",raw,echo=0 \
  PTY,link="${TTY1}",raw,echo=0 \
  >"${SOCAT_LOG}" 2>&1 &

SOCAT_PID=$!

echo "[INFO] Waiting for virtual ports..."

for i in {1..20}; do
  if [[ -e "${TTY0}" && -e "${TTY1}" ]]; then
    break
  fi

  if ! kill -0 "${SOCAT_PID}" 2>/dev/null; then
    echo "[ERROR] socat exited before PTYs were created."
    cat "${SOCAT_LOG}" || true
    exit 1
  fi

  sleep 0.25
done

if [[ ! -e "${TTY0}" || ! -e "${TTY1}" ]]; then
  echo "[ERROR] Failed to create ${TTY0} and ${TTY1}."
  cat "${SOCAT_LOG}" || true
  exit 1
fi

echo "[INFO] Virtual ports ready:"
echo "[INFO] ${TTY0} -> $(readlink -f "${TTY0}")"
echo "[INFO] ${TTY1} -> $(readlink -f "${TTY1}")"

echo "[INFO] Starting First Enterprise Modbus simulator..."
echo "[INFO] Config: ${CONFIG}"
echo "[INFO] Scenario: ${SCENARIO}"

"${PYTHON}" src/main.py \
  --config "${CONFIG}" \
  --scenario "${SCENARIO}" &

SIM_PID=$!

# Service 必須跟著 socat / simulator 的生命週期走。
# 任一 child 意外退出，就讓 service failure，交給 systemd restart。
set +e
wait -n "${SOCAT_PID}" "${SIM_PID}"
RC=$?
set -e

echo "[ERROR] socat or simulator exited unexpectedly (rc=${RC})."

# child 即使 rc=0，對這個 long-running service 仍屬異常退出。
if [[ "${RC}" -eq 0 ]]; then
  RC=1
fi

exit "${RC}"