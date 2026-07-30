#!/bin/sh
# Sustituye IVR_ARI_PASSWORD en ari.conf en el arranque: la contraseña nunca
# queda commiteada en texto plano (C10, #172). Falla cerrado si falta.
set -eu

if [ -z "${IVR_ARI_PASSWORD:-}" ]; then
    echo "IVR_ARI_PASSWORD no está configurada — abortando arranque del IVR" >&2
    exit 1
fi

envsubst '${IVR_ARI_PASSWORD}' < /etc/asterisk/ari.conf.template > /etc/asterisk/ari.conf

exec "$@"
