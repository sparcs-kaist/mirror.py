FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        rsync git supervisor ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

COPY dist/mirror_py-*.whl /tmp/
RUN pip install --no-cache-dir /tmp/mirror_py-*.whl

RUN mkdir -p /etc/mirror /var/run/mirror /var/lib/mirror /var/log/mirror /var/log/mirror/packages /srv/publish /var/www/mirror

COPY config.json /etc/mirror/config.json
COPY supervisord.conf /etc/supervisor/conf.d/mirror.conf

CMD ["/usr/bin/supervisord", "-n"]
