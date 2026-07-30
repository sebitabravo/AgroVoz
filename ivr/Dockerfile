FROM ubuntu:24.04

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        asterisk \
    && rm -rf /var/lib/apt/lists/* \
    && install -d -o asterisk -g asterisk \
        /var/run/asterisk \
        /var/log/asterisk \
        /var/spool/asterisk

COPY extensions.conf /etc/asterisk/extensions.conf

USER asterisk

CMD ["asterisk", "-f", "-U", "asterisk", "-G", "asterisk", "-vvv"]
