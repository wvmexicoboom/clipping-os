#!/bin/sh
# Supervisor del panel: si el proceso muere por cualquier razon, lo vuelve a
# levantar en 2 segundos y deja la causa en panel.log. El tunel de Cloudflare
# sigue apuntando a :8000, asi que el enlace publico no cambia.
cd "$(dirname "$0")/.." || exit 1

export CLIPPER_DB="${CLIPPER_DB:-/home/user/clipping-os/demo/demo.db}"
export CLIPPER_SALIDA="${CLIPPER_SALIDA:-/home/user/clipping-os/demo/salida}"
export CLIPPER_SECRETS_DIR="${CLIPPER_SECRETS_DIR:-/home/user/clipping-os/demo}"
export CLIPPER_PANEL_TOKEN="${CLIPPER_PANEL_TOKEN:?falta CLIPPER_PANEL_TOKEN}"

while true; do
  echo "[$(date -u +%FT%TZ)] arrancando panel..." >> panel.log
  python3 main.py panel --puerto "${PORT:-8000}" >> panel.log 2>&1
  codigo=$?
  echo "[$(date -u +%FT%TZ)] el panel murio con codigo $codigo; reinicio en 2 s" >> panel.log
  sleep 2
done
