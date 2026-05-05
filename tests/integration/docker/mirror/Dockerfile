FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        rsync git supervisor ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

ARG MIRROR_PY_VERSION=1.0.0rc7
RUN pip install --no-cache-dir "mirror.py==${MIRROR_PY_VERSION}"

RUN mkdir -p /etc/mirror /var/run/mirror /var/lib/mirror /var/log/mirror /var/log/mirror/packages /srv/publish /var/www/mirror

COPY config.json /etc/mirror/config.json
COPY supervisord.conf /etc/supervisor/conf.d/mirror.conf

CMD ["/usr/bin/supervisord", "-n"]
