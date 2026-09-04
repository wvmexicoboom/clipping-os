#!/bin/sh
# Tunel publico via localtunnel (loca.lt) — sin cuenta, sin Cloudflare.
# Deja siempre la URL vigente en tunel_url.txt y se revive si cae.
cd "$(dirname "$0")/.." || exit 1

while true; do
  echo "[$(date -u +%FT%TZ)] abriendo localtunnel..." >> tunel.log
  npx -y localtunnel --port 8000 2>&1 | while read -r linea; do
    echo "$linea" >> tunel.log
    url=$(printf '%s' "$linea" | grep -oE "https://[a-z0-9.-]+\.loca\.lt" | head -1)
    if [ -n "$url" ]; then
      echo "$url" > tunel_url.txt
      echo "[$(date -u +%FT%TZ)] URL vigente: $url" >> tunel.log
    fi
  done
  echo "[$(date -u +%FT%TZ)] localtunnel cayo; reinicio en 3 s" >> tunel.log
  sleep 3
done
