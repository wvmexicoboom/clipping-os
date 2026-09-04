#!/bin/sh
# Tunel publico por SSH (localhost.run) — sin Cloudflare.
#
# localhost.run es un servidor distinto: expone tu puerto via un tunel SSH
# reverso, sin cuenta y sin instalar nada. Devuelve una URL https propia.
#
# El supervisor lo revive si cae y SIEMPRE deja la URL vigente en tunel_url.txt.
cd "$(dirname "$0")/.." || exit 1

while true; do
  echo "[$(date -u +%FT%TZ)] abriendo tunel SSH..." >> tunel.log
  rm -f tunel_ssh.log
  ssh -R 80:localhost:8000 \
      -o StrictHostKeyChecking=no \
      -o ServerAliveInterval=15 \
      -o ServerAliveCountMax=3 \
      -o ExitOnForwardFailure=yes \
      -N -T nokey@localhost.run >> tunel_ssh.log 2>&1 &
  SSH_PID=$!

  # Esperar a que aparezca la URL (localhost.run la imprime en la salida).
  i=0
  while [ $i -lt 40 ]; do
    url=$(grep -oE "https://[a-zA-Z0-9.-]+" tunel_ssh.log 2>/dev/null | grep -v localhost.run | head -1)
    if [ -n "$url" ]; then
      echo "$url" > tunel_url.txt
      echo "[$(date -u +%FT%TZ)] URL vigente: $url" >> tunel.log
      break
    fi
    sleep 1; i=$((i+1))
  done

  wait $SSH_PID
  echo "[$(date -u +%FT%TZ)] el tunel SSH cayo; reinicio en 3 s" >> tunel.log
  sleep 3
done
