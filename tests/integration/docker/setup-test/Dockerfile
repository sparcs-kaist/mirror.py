FROM ubuntu:22.04

RUN apt-get update && apt-get install -y --no-install-recommends \
        systemd systemd-sysv \
        python3 python3-pip python-is-python3 \
        rsync lftp git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m pip install --no-cache-dir bandersnatch==6.5.0

COPY dist/mirror_py-*.whl /tmp/
RUN python3 -m pip install --no-cache-dir /tmp/mirror_py-*.whl

STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
