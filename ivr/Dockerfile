FROM ubuntu:24.04

RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        asterisk \
        gettext-base \
    && rm -rf /var/lib/apt/lists/* \
    && install -d -o asterisk -g asterisk \
        /var/run/asterisk \
        /var/log/asterisk \
        /var/spool/asterisk

COPY extensions.conf /etc/asterisk/extensions.conf
COPY http.conf /etc/asterisk/http.conf
COPY ari.conf.template /etc/asterisk/ari.conf.template
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# entrypoint.sh corre como root para poder escribir /etc/asterisk/ari.conf
# con la contraseña real (envsubst); asterisk -U/-G abajo dropea a un
# usuario sin privilegios apenas arranca el proceso.
ENTRYPOINT ["/entrypoint.sh"]
CMD ["asterisk", "-f", "-U", "asterisk", "-G", "asterisk", "-vvv"]
