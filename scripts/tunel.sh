#!/bin/sh
# Supervisor del tunel publico.
#
# Realidad que no se puede ocultar: sin una cuenta de Cloudflare, el subdominio
# *.trycloudflare.com es ALEATORIO y cambia cada vez que el tunel se reinicia.
# Este supervisor lo revive si muere y, lo importante, SIEMPRE deja la URL
# vigente en tunel_url.txt para que no haya que adivinarla.
cd "$(dirname "$0")/.." || exit 1
CF="${CF_BIN:-/tmp/cloudflared}"

while true; do
  echo "[$(date -u +%FT%TZ)] arrancando tunel..." >> tunel.log
  "$CF" tunnel --url "http://127.0.0.1:${PORT:-8000}" --no-autoupdate 2>&1 | while read -r linea; do
    echo "$linea" >> tunel.log
    url=$(printf '%s' "$linea" | grep -o "https://[a-z0-9-]*\.trycloudflare\.com" | head -1)
    if [ -n "$url" ]; then
      echo "$url" > tunel_url.txt
      echo "[$(date -u +%FT%TZ)] URL vigente: $url" >> tunel.log
    fi
  done
  echo "[$(date -u +%FT%TZ)] el tunel cayo; reinicio en 3 s" >> tunel.log
  sleep 3
done
