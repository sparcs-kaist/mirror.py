FROM ubuntu:22.04

RUN apt-get update && apt-get install -y --no-install-recommends vsftpd \
    && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /srv/ftp/data /var/run/vsftpd/empty
COPY data/ /srv/ftp/data/
COPY vsftpd.conf /etc/vsftpd.conf

EXPOSE 21
CMD ["vsftpd", "/etc/vsftpd.conf"]
