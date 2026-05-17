FROM ubuntu:26.04

RUN apt-get update && apt-get install -y --no-install-recommends \
        systemd systemd-sysv \
        python3 python3-pip python-is-python3 \
        rsync lftp git ca-certificates curl procps \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --break-system-packages --no-cache-dir bandersnatch

COPY dist/mirror_py-*.whl /tmp/
RUN python3 -m pip install --break-system-packages --no-cache-dir /tmp/mirror_py-*.whl

RUN mirror setup

COPY config.json /etc/mirror/config.json

RUN systemctl enable mirror.service mirror-worker.service

STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
