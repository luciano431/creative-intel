# Radar creativo de Ranker

Herramienta de Ranker para armar el **plan creativo mensual de cualquier cliente** a partir de lo que pauta su competencia. Sistema semi-automatizado que monitorea los creativos activos de la competencia en **Meta** (Facebook/Instagram) y **Google** (Search/Display/YouTube), los clasifica con IA dentro de una taxonomía cerrada, compara contra el histórico y contra nuestros propios anuncios, y produce un **reporte mensual accionable** que un humano valida antes de publicarlo en **Basecamp**.

> Regla del sistema: **la máquina encuentra patrones, el humano decide cuáles se convierten en estrategia.**
> Todo lo que se reporta distingue **DATO** (conteos, fechas, días activo) de **INFERENCIA** (hipótesis). Nunca se estima el rendimiento real de un competidor.

## Clientes

Cada cliente tiene su propia carpeta de configuración, base de datos e informes:

```bash
radar new-client banco-x --name "Banco X" --industry Banca --market Uruguay   # crea config/clients/banco-x/
# completar config/clients/banco-x/competitors.yaml (IDs de Meta, dominios de Google)
export RADAR_CLIENT=banco-x
radar init-db && radar competitors sync
radar run-cycle --period 2026-10          # informe en reports/banco-x/2026-10/
```

### Web interna

```bash
radar web        # abre http://127.0.0.1:8080
```

Lista los clientes; en cada uno se genera el informe del mes (con o sin IA), se abren los anteriores, se ven sus competidores con sus IDs, se buscan los que faltan, se proponen competidores nuevos y se edita la configuración. Cada botón corre el mismo comando que en la terminal y muestra su avance. Con `--host 0.0.0.0` la pueden abrir otras compus de la misma red.

### Buscar IDs y competidores

```bash
radar find-ids "miSeguroOk" --country UY                       # página de Meta + dominio verificado en Google
radar fill-ids --write                                          # completa los IDs que faltan del cliente actual
radar discover -k "seguros,seguro de auto" -c UY --top 5 --add local        # quién más pauta en el rubro
radar discover -k "seguros,seguro de hogar" -c ES -c AR -c MX --top 5 --add global
```

`discover` propone los anunciantes con más anuncios activos para esas palabras; conviene revisar la lista (pueden aparecer comparadores o medios). Todo lo que se escribe en `competitors.yaml` deja una copia `.bak`.

Sin `RADAR_CLIENT` se usa `config/` (el cliente por defecto). La marca del informe (nombre, logo, paleta) está en `config/brand.yaml`; un cliente puede tener su propio `brand.yaml` en su carpeta. Los nombres en castellano de cada categoría están en la sección `labels` de `taxonomy.yaml`.

## Arquitectura

```
config/competitors.yaml ──► COLECTORES ──► BASE DE CREATIVOS ──► IA etapa 1 ──► MOTOR COMPARATIVO ──► IA etapa 2 ──► QA HUMANA ──► REPORTE ──► BASECAMP
(países > local/global/own)  meta_api        creatives           clasificación    mes vs mes            síntesis       approve /     HTML + MD    documento
                             meta_web        sightings           JSON cerrado     local vs global       estratégica    edit /        + capturas   + to-dos
                             google_serpapi  classifications     (taxonomy.yaml)  white space           (GPT-5.4)      reject
                             google_web      concepts                             nosotros vs mercado
                             manual          syntheses                            longevidad / familias
```

| Capa | Módulo | Qué hace |
|---|---|---|
| Universo | `config/competitors.yaml` | País → tier (`local` / `global` / `own`) → competidor con `meta_page_id` y `google_advertiser_id`. Agregar uno = agregar un bloque. |
| Taxonomía | `config/taxonomy.yaml` | Familias de ángulo, hooks, formatos, funnel, CTA, estilos, productos. La IA solo clasifica dentro de estos valores. |
| Colectores | `src/creative_intel/collectors/` | `meta_api` (Graph API oficial), `meta_web` (Playwright), `google_serpapi` (SerpApi), `google_web` (Playwright), `manual` (CSV/JSON). Contrato común `RawCreative`. |
| Normalización | `normalize.py` | Fingerprint estable, dedupe, upsert, **longevidad** (first_seen / last_seen / días activo), sightings por ciclo, flags de Data QA. |
| IA etapa 1 | `ai/classify.py` | Un creativo → JSON validado (imagen + copy). Modelo Pydantic **generado desde la taxonomía**. Fallback heurístico sin API. |
| Motor comparativo | `analysis/metrics.py`, `analysis/concepts.py` | Distribuciones, matriz competidor × ángulo, local vs global, mes vs mes, white space, mercado vs miSeguroOk, familias creativas. Funciones puras. |
| IA etapa 2 | `ai/synthesize.py` | Síntesis estratégica estructurada: executive summary con cifras, top 3 insights (dato + inferencia), creativos de referencia con adaptación, oportunidades con conceptos ejecutables, riesgos de saturación, action items. |
| QA humana | `review.py` | Checkpoint 1 (Data QA, cola de creativos dudosos) y Checkpoint 2 (Strategic QA: approve / edit / reject). `publish` exige aprobación. |
| Reporte | `report/` | `report.html` (visual, con capturas), `report.md`, `basecamp.html` (subconjunto HTML que acepta Basecamp). |
| Publicación | `publish/basecamp.py`, `publish/notify.py` | Documento en el Vault del proyecto + capturas como attachments + un to-do por action item. Alertas a Slack / webhook de Make. |
| Orquestación | `pipeline.py`, `cli.py`, `api.py`, `.github/workflows/monthly-cycle.yml` | CLI, API HTTP para Make y cron mensual (día 23). |

## Quickstart (5 minutos, sin claves)

Requiere Python 3.11 o superior (`python3 --version`; en macOS: `brew install python@3.12` y usar `python3.12` abajo).

```bash
python3 -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"
creative-intel demo            # dos meses de datos ficticios, IA en modo heurístico
open reports/demo/report.html  # reporte completo con mes vs mes y mercado vs miSeguroOk
pytest                         # 24 tests
```

En cada terminal nueva, activar el entorno de nuevo con `source .venv/bin/activate`.

El comando `demo` recorre exactamente el mismo pipeline que producción; solo cambia la fuente (`data/samples/demo_creatives.json`, textos ficticios) y el clasificador (`--mock-ai`).

## Puesta en producción

1. **Configurar el universo.** Completar `meta_page_id` y `google_advertiser_id` en `config/competitors.yaml` (los IDs salen de la URL de la Ad Library y del Transparency Center). Correr `creative-intel competitors list` para ver qué falta.
2. **Claves.** Copiar `.env.example` a `.env`:
   - `ANTHROPIC_API_KEY` (clasificación y síntesis; modelos configurables con `CLASSIFY_MODEL` / `SYNTH_MODEL`).
   - `META_ACCESS_TOKEN` para la Graph API. **Limitación real:** la Ad Library API solo devuelve *todos* los anuncios para países de la UE/UK; fuera de la UE solo devuelve anuncios políticos. Para Uruguay se usa `meta_web` (Playwright) o importación manual. Los globales se observan en ES/FR/DE/IT con la API.
   - `SERPAPI_KEY` para Google (no hay API oficial del Transparency Center; SerpApi es la vía estable). Alternativa gratis y frágil: `google_web`.
   - Basecamp: `BASECAMP_ACCOUNT_ID`, `BASECAMP_PROJECT_ID`, `BASECAMP_VAULT_ID` (Docs & Files), `BASECAMP_TODOLIST_ID` y token OAuth (`BASECAMP_ACCESS_TOKEN` o refresh token + client id/secret).
   - `SLACK_WEBHOOK_URL` y/o `MAKE_WEBHOOK_URL` para las alertas de los checkpoints.
3. **Scraping (opcional).** `pip install -e ".[scraping]" && playwright install chromium`.
4. **Nuestros anuncios.** Exportar los creativos activos de miSeguroOk a `data/inbox/miseguro/*.csv` (formato en `data/samples/manual_import_example.csv`; admite columnas de resultados reales en `engagement`). Habilita la sección *Mercado vs miSeguroOk*.

## Flujo mensual

| Día | Paso | Comando |
|---|---|---|
| 20-21 | Recolección | `creative-intel collect -s meta_api -s meta_web -s google_serpapi -s manual` |
| 21 | Data QA (checkpoint 1, perfil junior, ~15 min) | `creative-intel review data` → `creative-intel review qa --ok 12,13 --reject 14` |
| 21-22 | Clasificación | `creative-intel classify` |
| 22 | Métricas + histórico | `creative-intel analyze` |
| 23 | Síntesis estratégica | `creative-intel synthesize` |
| 24 | Borrador | `creative-intel report` |
| 24-25 | Strategic QA (checkpoint 2, responsable, ~30 min) | `creative-intel review show --export s.json` → editar → `review edit s.json -r "Ana"` (o `review approve -r "Ana"`) |
| 25 | Publicación | `creative-intel publish` |
| 26+ | Equipo creativo produce | to-dos en Basecamp |

`creative-intel run-cycle` encadena collect → classify → analyze → synthesize → report y **se detiene antes de publicar**, avisando por Slack/Make que el borrador está listo. El cron de GitHub Actions (`.github/workflows/monthly-cycle.yml`) lo corre el día 23 de cada mes a las 09:00 de Montevideo; también se puede disparar a mano con `workflow_dispatch`.

### Con Make como orquestador

Levantar la API (`uvicorn creative_intel.api:app --port 8000`, header `X-API-Token`) y desde Make:

```
POST /runs/{YYYY-MM}/cycle            → 202, corre en background
GET  /runs/{YYYY-MM}                  → estado, totales, pendientes de QA
GET  /runs/{YYYY-MM}/qa               → cola de Data QA   |  POST /runs/{p}/qa {ids, status}
GET  /runs/{YYYY-MM}/synthesis        → borrador           |  POST /runs/{p}/review {decision, reviewer, notes, edited}
POST /runs/{YYYY-MM}/publish          → Basecamp (409 si no está aprobado)
```

Un escenario típico: Scheduler (día 23) → `cycle` → esperar → `GET /runs/{p}` → mensaje a Slack con link al borrador → formulario de aprobación → `review` → `publish`.

## Modelo de datos

- **creatives**: un registro por creativo único (fingerprint = ID externo o copy normalizado + primer medio). Guarda copy, CTA, landing, medios, captura, países, señales de engagement visibles, `first_seen`, `last_seen`, `status`, `qa_status`.
- **sightings**: cada ciclo que observó al creativo. Base del histórico: permite decir "SURA lanzó este concepto el 14/08, lo mantuvo 45 días y sumó 7 variantes".
- **classifications**: salida de la etapa 1, versionada por ciclo (se puede reclasificar sin perder lo anterior).
- **concepts** / familias creativas: mismo competidor + producto + ángulo + hook = variantes de un concepto.
- **runs**: un ciclo mensual con `metrics` (todo el JSON comparativo) y `stats`.
- **syntheses**: borradores y versiones aprobadas/editadas por humanos, con revisor y notas.
- **publications**: qué se publicó, dónde y cuándo.

SQLite por defecto; para escalar, `DATABASE_URL=postgresql://...` y nada más.

## Qué mide y qué no

**Observable (se reporta como dato):** anuncio activo, fecha de inicio, última vez visto, días activo, plataforma, formato, placement, copy, CTA, landing, cantidad de variantes, países, engagement visible (likes/comentarios/shares cuando la fuente los muestra).

**Inferido (se reporta como hipótesis, con nivel de confianza):** ángulo, hook, pain point, promesa, funnel, audiencia, estilo, "podría indicar que…".

No existe "engagement estimado 8.4/10" ni "este anuncio funciona". Existe "activo 47 días, 7 variantes, presente en 3 de 10 competidores".

## Escalabilidad

- **Nuevo competidor o país**: bloque en `competitors.yaml` + `creative-intel competitors sync`. El próximo ciclo lo incorpora.
- **Nueva fuente** (TikTok, YouTube orgánico, landing pages): una clase que devuelva `RawCreative` registrada en `collectors/__init__.py`.
- **Nueva dimensión de análisis**: agregar valores a `taxonomy.yaml`; el esquema de la IA y el reporte se regeneran solos.
- **Otro proveedor de IA** (por ejemplo Gemini para video): implementar la misma firma que `classify_creative` y cambiar la llamada en `pipeline.classify`. La transcripción de audio de videos es la extensión natural de la etapa 1.

## Roadmap

- **Fase 1 (este repo)**: 10 competidores, Meta + Google, base de creativos, clasificación, métricas, síntesis, QA, reporte, Basecamp, cron.
- **Fase 2**: transcripción de video, detección automática de conceptos con IA, dashboard histórico, alertas intra-mes cuando un competidor lanza una familia nueva.
- **Fase 3**: generación automática de briefs creativos por oportunidad y carga directa a Basecamp como tareas con adjuntos; comparación con resultados reales de miSeguroOk por concepto.
