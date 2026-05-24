FROM ubuntu:22.04
RUN apt-get update && apt-get install -y --no-install-recommends rsync \
    && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /srv/debian
COPY data/ /srv/debian/
COPY rsyncd.conf /etc/rsyncd.conf
EXPOSE 873
CMD ["rsync", "--daemon", "--no-detach", "--config=/etc/rsyncd.conf"]
