FROM ubuntu:22.04

RUN apt-get update && apt-get install -y --no-install-recommends \
        systemd systemd-sysv \
        python3 python3-pip python-is-python3 \
        rsync lftp debmirror gnupg jigdo-file wget gzip grep git ca-certificates curl procps \
    && rm -rf /var/lib/apt/lists/*

COPY dist/mirror_py-*.whl /tmp/
RUN for wheel in /tmp/mirror_py-*.whl; do python3 -m pip install --no-cache-dir "$wheel[apt-mirror2]"; done

RUN mirror setup

COPY config.json /etc/mirror/config.json

RUN systemctl enable mirror.service mirror-worker.service

STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
