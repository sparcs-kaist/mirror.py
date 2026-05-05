FROM alpine:3.20
RUN apk add --no-cache rsync
RUN mkdir -p /srv/debian
COPY data/ /srv/debian/
COPY rsyncd.conf /etc/rsyncd.conf
EXPOSE 873
CMD ["rsync", "--daemon", "--no-detach", "--config=/etc/rsyncd.conf"]
