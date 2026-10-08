# Radar creativo de Ranker: web pública para el equipo (scraping con Chromium incluido)
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PLAYWRIGHT_BROWSERS_PATH=/ms-playwright TZ=America/Montevideo
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install -e ".[scraping]" && python -m playwright install --with-deps chromium

# La configuración del repositorio queda aparte; el arranque la copia al disco persistente
COPY config ./config.dist
COPY deploy/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && mkdir -p /var/radar

# Todo lo que se escribe (bases de datos, imágenes, informes y la configuración que se edita desde la web)
# vive en /var/radar: montar ahí un disco persistente.
VOLUME ["/var/radar"]
EXPOSE 8080
ENTRYPOINT ["/entrypoint.sh"]
