FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /tmp

# EntryPoint：
# 1. Verify if /tmp/ttyV1 exists (it will be included by docker-compose using devices).
# 2. If it exists, execute simulator main.py directly.
ENTRYPOINT ["/bin/bash", "-c", "\
  set -euo pipefail; \
  if [ ! -e /tmp/ttyV1 ]; then \
    echo '[ERROR] /tmp/ttyV1 not found in container.'; \
    echo '        Confirm：'; \
    echo '        1) host executed create_modbus_bus.sh make /tmp/ttyV0 / /tmp/ttyV1'; \
    echo '        2) docker-compose have devices: - \"/tmp/ttyV1:/tmp/ttyV1\"'; \
    ls -l /tmp /dev || true; \
    exit 1; \
  fi; \
  echo '[SIMULATOR] Using RTU port: /tmp/ttyV1'; \
  python src/main.py \
"]
