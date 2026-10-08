#!/bin/sh
# Arranque en el servidor: datos en el disco persistente y la web en el puerto que da la plataforma.
set -e
VOL="${RADAR_VOLUME:-/var/radar}"
mkdir -p "$VOL/data" "$VOL/reports" "$VOL/config"

# Configuración: la primera vez se copia la del repositorio; después se conserva lo editado desde la web.
# Lo que no se edita desde la web (calendario de eventos, marca, logos) siempre viene de la versión nueva.
SRC=/app/config.dist
cp -rn "$SRC/." "$VOL/config/" 2>/dev/null || true
cp -f "$SRC/events.yaml" "$SRC/brand.yaml" "$VOL/config/" 2>/dev/null || true
[ -d "$SRC/assets" ] && cp -rf "$SRC/assets/." "$VOL/config/assets/"

for d in data reports config; do
  if [ ! -L "/app/$d" ]; then rm -rf "/app/$d"; ln -s "$VOL/$d" "/app/$d"; fi
done

cd /app
radar init-db >/dev/null 2>&1 || true
exec radar web --host 0.0.0.0 --port "${PORT:-8080}" --no-open
