# Radar en un servidor (web pública para el equipo)

La app corre en un contenedor (Dockerfile en la raíz) con Chromium incluido para leer las bibliotecas
de anuncios. Todo lo que se escribe (bases de datos, imágenes, informes y la configuración que se edita
desde la web) vive en un disco persistente montado en `/var/radar`.

## Railway (recomendado: se conecta al repo de GitHub y se actualiza solo con cada push)

1. railway.com → New Project → Deploy from GitHub repo → `luciano431/creative-intel`.
2. En el servicio: **Settings → Volumes → Add volume** con mount path `/var/radar` (5 GB alcanzan).
3. **Variables** (las mismas del `.env` de la Mac, sin comillas):
   - `WEB_PASSWORD` (mínimo 10 caracteres; el usuario puede ser cualquiera)
   - `AI_PROVIDER=openai`, `OPENAI_API_KEY`
   - `META_ACCESS_TOKEN`
   - opcionales: `GEMINI_API_KEY`, `GEMINI_CREATIVE_MODEL`, `INSTAGRAM_ACCESS_TOKEN`, `MAX_AI_USD_PER_RUN`
4. **Settings → Resources**: al menos 2 GB de RAM (Chromium).
5. **Settings → Networking → Generate Domain**: esa es la dirección fija para el equipo
   (se puede cambiar por un subdominio propio, ej. radar.rankerstudio.com).

Cada `git push` a `main` vuelve a desplegar; los datos del disco se conservan.

## Cualquier otro servidor con Docker

```bash
docker build -t radar .
docker run -d --name radar -p 8080:8080 -v radar-data:/var/radar --env-file .env --restart unless-stopped radar
```

## Pasar los datos de la Mac al servidor (una sola vez, opcional)

Los informes y bases ya generados están en `data/` y `reports/` de la Mac. Se pueden subir al disco del
servidor (Railway: `railway volume` / `railway ssh`); si no, el servidor arranca vacío y se generan de nuevo.
