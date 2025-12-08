FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /tmp

# Enter Point：
# 1. Create symlink：/tmp/ttyV0 -> /dev/ttyMODBUS1
# 2. Excute simulator main.py
ENTRYPOINT ["/bin/bash", "-c", "\
  ln -sf /dev/ttyMODBUS1 /tmp/ttyV0 && \
  echo '[SIMULATOR] /tmp/ttyV0 -> /dev/ttyMODBUS1' && \
  python3 src/main.py \
"]
