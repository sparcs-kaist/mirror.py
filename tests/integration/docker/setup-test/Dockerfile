FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        rsync lftp ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir bandersnatch

COPY dist/mirror_py-*.whl /tmp/
RUN pip install --no-cache-dir /tmp/mirror_py-*.whl
