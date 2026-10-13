"""Guía de un evento comercial para un cliente: calendario de piezas, oferta e ideas con guion.

Se apoya en lo que hizo el mercado en la edición anterior (analysis/event.py) y en la hoja del cliente.
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import Field

from ..config import settings
from .client import structured_call
from .synthesize import _compact_items, _Strict


class EventFact(_Strict):
    headline: str = Field(description="Titular corto con el dato (máx. 12 palabras)")
    detail: str = Field(description="El dato con sus números, en una o dos oraciones. Sin interpretar")


class CalendarItem(_Strict):
    when: str = Field(description="Cuándo, relativo al evento: '10 días antes', 'el día', 'último día', 'extensión'")
    goal: str = Field(description="Para qué: anticipar, lanzar, empujar, cerrar")
    format: str = Field(description="Formato de la pieza (video, carrusel, imagen, historia, orgánico)")
    intent: str = Field(description="Qué tiene que comunicar la pieza y con qué producto u oferta (indicación para el redactor, no el texto final)")


class IdeaBrief(_Strict):
    """Lo que decide el análisis de cada pieza. La pieza en sí (gancho, guion, copy, visual) la escribe el redactor."""
    title: str = Field(description="Nombre de trabajo de la idea (máx. 6 palabras)")
    moment: str = Field(description="Momento del calendario al que corresponde, copiado tal cual")
    format: str = Field(description="video | carrusel | imagen | historia | orgánico (publicación de Instagram)")
    product: str = Field(description="Producto o categoría del cliente que muestra")
    angle: str = Field(description="Ángulo del mensaje en pocas palabras")
    brief: str = Field(description="Qué tiene que lograr la pieza y qué tiene que decir o mostrar, en 2-3 oraciones. Sin escribir la pieza")
    evidence: str = Field(description="El dato que la origina, con números y de dónde sale (anuncio, cuenta, fecha)")
    reading: str = Field(description="Qué significa ese dato para el cliente, en una oración")
    hypothesis: str = Field(description="Qué creemos que va a pasar si se hace, empezando con 'Creemos que'")
    test: str = Field(description="Cómo se comprueba (contra qué, cuánto tiempo, inversión mínima) y el umbral para seguir o cortar")


class EventPlan(_Strict):
    summary: list[EventFact] = Field(description="3 a 4 hechos de cómo lo encaró el mercado en la edición anterior")
    offer_recommendation: str = Field(description="Qué oferta conviene comunicar y por qué, en párrafos que empiezan con 'Dato:', 'Lectura:' e 'Hipótesis:', respetando la hoja del cliente")
    calendar: list[CalendarItem] = Field(description="4 a 8 piezas en orden de salida")
    ideas: list[IdeaBrief] = Field(description="3 a 5 briefs de piezas, cada uno con dato, lectura, hipótesis y prueba")
    avoid: list[str] = Field(description="2 a 4 cosas a no hacer, basadas en datos o en la hoja del cliente")


EVENT_PROMPT = """Sos el/la director/a de estrategia creativa de Ranker. Armás la guía de {event} para {client} ({industry}, {market}).
Recibís cómo lo encaró el mercado en la edición anterior (`event_metrics`: cuándo arrancaron los anuncios respecto del día del evento, qué ofertas y qué tan profundas, cuánta urgencia, formatos, quién pautó), los anuncios de esa edición, la hoja del cliente y las fechas de la próxima edición si están.

Reglas:
1. Castellano claro, voseo, sin jerga en inglés. Separá siempre tres cosas: el DATO (conteos, porcentajes, días, montos de los datos, sin adjetivos), la LECTURA (qué significa para {client}) y la HIPÓTESIS (qué creemos que va a pasar, con "Creemos que..."). Nunca presentes una lectura o una hipótesis como si fuera un dato. El resumen (`summary`) lleva solo datos.
2. El calendario sale del timing real del mercado (si 6 de cada 10 anuncios arrancaron en la previa, la previa importa) y se escribe relativo al evento.
3. La oferta respeta la hoja del cliente: si no tiene promociones, no inventes descuentos; proponé beneficios que no bajen el precio y marcalos como propuesta.
4. Vos hacés el análisis y los briefs; las piezas (gancho, guion, copy, CTA, dirección visual y el texto del calendario) las escribe después un redactor. No escribas copy ni guiones: en cada brief decí qué tiene que lograr la pieza, qué producto muestra y qué tiene que decir o mostrar.
5. Si los datos son pocos (menos de 10 anuncios), decilo en el resumen y no generalices.
6. `event_metrics.own_results` y `own_daily` son los resultados reales de {client} en esa edición (inversión, resultados, costo por resultado, antes/durante/después). Son lo más importante: el resumen, la oferta y el calendario arrancan de ahí (qué anuncio y qué oferta le dieron el mejor costo por resultado, en qué momento rindió más). Citá los números.
7. Solo productos de las categorías de {client}; los anuncios de otro rubro ya fueron descartados.
8. Nunca escribas "resultados" a secas: decí siempre qué son (compras, conversaciones por WhatsApp/Messenger, leads o clics al sitio) y no sumes tipos distintos. Compará costos solo dentro del mismo tipo. No nombres campos ni variables (own_daily, own_results, event_metrics).
9. `event_metrics` (ofertas, urgencia, timing, quién pautó) es SOLO de la competencia. Si tiene 0 anuncios, decí que no hay datos de la competencia de esa edición y no saques conclusiones del mercado.
10. `event_metrics.organic`: publicaciones orgánicas de Instagram de {client} y de cada competidor en esa ventana (cantidad, likes y comentarios promedio, interacción sobre seguidores, cuántas nombraron el evento y las de más interacción con su texto). Es el dato de la competencia de esa edición: usalo para comparar qué temas, formatos y mensajes generaron más interacción. Interacción no es venta: decilo así. Si una cuenta tiene `source: perfil_publico`, solo se ven sus últimas publicaciones (desde `visible_since`): con 0 publicaciones en la ventana no digas que no publicó, decí que no se ve.
11. Cada anuncio propio trae `stage` (tofu = que nos conozcan, mofu = consideración, bofu = conversión) y el resultado que buscaba su campaña. Un anuncio de tráfico o alcance (tofu) no se juzga por leads ni ventas: se compara solo con los de su misma etapa y tipo de resultado.
12. Los briefs (`ideas`) son obligatorios: 3 a 5, uno por momento clave del calendario. Cada uno con su cadena: `evidence` (el dato que lo origina, citando de dónde sale), `reading`, `hypothesis` y `test` (cómo se prueba y con qué umbral se decide seguir o cortar). Si el dato es débil (pocos anuncios, una sola cuenta), decilo en `reading`.
13. Los montos están en la moneda de la cuenta (`currency`: "$" = pesos uruguayos, "US$" = dólares). Escribilos siempre con ese símbolo. Si no hay anuncios de la competencia de esa edición, decilo en una línea y trabajá con los resultados propios y el orgánico.
14. `event_metrics.data_coverage` dice con qué datos se armó la guía y qué faltó. Si la confianza es media o baja, el primer hecho del resumen dice qué faltó y las ideas se plantean como pruebas chicas, no como apuestas grandes.
15. Los campos `evidence`, `reading`, `hypothesis` y `test` van sin la etiqueta adelante (nada de "DATO:" o "PRUEBA:"; el informe ya la pone).
16. No inventes códigos de cupón, precios ni beneficios: si una pieza necesita uno que no está en los datos ni en la hoja, decilo en el brief ("con el código que defina el equipo").
17. En el calendario, `intent` dice qué tiene que comunicar cada pieza. Nunca uses datos internos de la cuenta (costos, ROAS) como argumento hacia el cliente final.
18. Al menos uno de los briefs es para Instagram orgánico (`format` = "orgánico") y se apoya en lo que mejor funcionó en las publicaciones del evento o del período (`event_metrics.organic`), citando la publicación en `evidence`. Interacción no es venta: la prueba se mide en interacción, mensajes o visitas, no en compras.
19. Separá siempre EL PERÍODO de EL EVENTO EN SÍ. `own_results`, `own_daily` y `organic` (promedios y `top`) son de toda la ventana (semanas antes y después). Lo del evento en sí está en `own_event_results` (anuncios propios con sus números de los días del evento, con `names_event` si nombran la fecha) y en `organic[].event` (publicaciones que nombran el evento o salieron en sus días, con `vs_period_pct` = cuánto más o menos interacción tuvieron que el promedio de la cuenta). El resumen tiene que decir qué hizo cada marca para el evento en sí (o que no publicó nada sobre la fecha) y cómo le fue contra su propio promedio. No presentes como "del evento" algo que solo es del período.
20. Las ofertas y cupones que aparecen en los datos son de la edición pasada y ya no están vigentes: usalos como aprendizaje (qué tamaño y qué forma de oferta rindió), no como oferta a repetir tal cual. En `offer_recommendation` proponé la mecánica (tipo y tamaño de oferta, en qué producto, con qué condición) y aclarás que la oferta concreta la confirma el equipo.
21. La guía está enfocada en el evento: la ventana son los días del evento y, si `event_metrics.previa` es mayor que 0, esa cantidad de días antes (la previa, cuando arranca la campaña). No hables del mes ni de "después" del evento. Si `event_only` es true, son solo los días del evento.
22. `event_metrics.compare` trae la pauta propia de {client} en los días de otras ediciones del evento (inversión, cada tipo de resultado con su costo y los anuncios con más inversión). `own_paid_total` es lo mismo para la edición base. Usalo para decir qué se repite entre ediciones y qué fue de un solo año; las recomendaciones y los briefs se apoyan en lo que se repite. Citá la edición de cada dato.
23. `event_metrics.organic_edition` dice de qué edición es el Instagram (puede ser otra que la de la pauta, la que ya estaba bajada) y si de la competencia se usó solo lo guardado. Si es otra edición, decilo al citar esos datos."""


def synthesize_event(event_name: str, metrics: dict[str, Any], items: list[dict[str, Any]], client_info: Any, next_dates: dict[str, Any],
                     labels: dict[str, str] | None = None) -> dict[str, Any]:
    payload = {
        "client": {"name": client_info.name, "industry": client_info.industry, "market": client_info.market},
        "client_brief": client_info.brief or None,
        "event": event_name,
        "next_edition": next_dates,
        "event_metrics": metrics,
        "labels": labels or {},
        "creatives": _compact_items(items, limit=150),
    }
    ask = "Armá la guía: el análisis, el calendario y los briefs de las piezas."
    content = [{"type": "text", "text": "Datos del evento (JSON):\n" + json.dumps(payload, ensure_ascii=False, default=str) + "\n\n" + ask}]
    system = EVENT_PROMPT.format(event=event_name, client=client_info.name, industry=client_info.industry or "sin industria definida",
                                 market=client_info.market or "mercado local")
    return structured_call(EventPlan, system, content, model=settings.synth_model, max_tokens=16000, effort="high", stream=True).model_dump()


def mock_event_plan(event_name: str, metrics: dict[str, Any], client_name: str) -> dict[str, Any]:
    """Versión sin IA (para probar el circuito): solo arma el esqueleto con los números."""
    n = metrics.get("ads", 0)
    t = metrics.get("timing") or {}
    ph = t.get("phases") or {}
    offers = (metrics.get("offers") or {}).get("dist") or []
    top_offer = offers[0]["value"].replace("_", " ") if offers else "sin ofertas detectadas"
    summary = [
        {"headline": f"{n} anuncios en la edición anterior", "detail": f"Arrancaron hasta {t.get('earliest_days_before', 0)} días antes del evento."},
        {"headline": "Cuándo salieron", "detail": f"Anticipo {ph.get('anticipo', 0)}, previa {ph.get('previa', 0)}, durante {ph.get('durante', 0)}, después {ph.get('despues', 0)}."},
        {"headline": f"La oferta más usada: {top_offer}", "detail": f"Descuento máximo visto: {(metrics.get('offers') or {}).get('pct_max') or '—'}%."},
    ]
    calendar = [
        {"when": w, "goal": g, "format": f, "intent": m, "message": m, "cta": c}
        for w, g, f, m, c in (
            ("10 días antes", "anticipar", "historia", f"Se viene {event_name} en {client_name}", "Activá el recordatorio"),
            ("2 días antes", "calentar", "carrusel", "Lo que va a estar en oferta", "Mirá la selección"),
            ("el día", "lanzar", "video", f"Arrancó {event_name}", "Comprá ahora"),
            ("último día", "cerrar", "imagen", "Últimas horas", "Aprovechá hoy"),
        )
    ]
    return {"summary": summary, "offer_recommendation": f"(Sin IA) La oferta más usada fue {top_offer}.", "calendar": calendar,
            "ideas": [], "avoid": ["(Sin IA) Correr con IA para obtener ideas con guion."]}
