"""Guía de un evento comercial para un cliente: calendario de piezas, oferta e ideas con guion.

Se apoya en lo que hizo el mercado en la edición anterior (analysis/event.py) y en la hoja del cliente.
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import Field

from ..config import settings
from .client import structured_call
from .synthesize import ReasonedIdea, _compact_items, _Strict


class EventFact(_Strict):
    headline: str = Field(description="Titular corto con el dato (máx. 12 palabras)")
    detail: str = Field(description="El dato con sus números, en una o dos oraciones. Sin interpretar")


class CalendarItem(_Strict):
    when: str = Field(description="Cuándo, relativo al evento: '10 días antes', 'el día', 'último día', 'extensión'")
    goal: str = Field(description="Para qué: anticipar, lanzar, empujar, cerrar")
    format: str = Field(description="Formato de la pieza (video, carrusel, imagen, historia)")
    message: str = Field(description="Lo que lee el cliente final en la pieza, textual (no una indicación interna para el equipo)")
    cta: str


class EventPlan(_Strict):
    summary: list[EventFact] = Field(description="3 a 4 hechos de cómo lo encaró el mercado en la edición anterior")
    offer_recommendation: str = Field(description="Qué oferta conviene comunicar y por qué, en párrafos que empiezan con 'Dato:', 'Lectura:' e 'Hipótesis:', respetando la hoja del cliente")
    calendar: list[CalendarItem] = Field(description="4 a 8 piezas en orden de salida")
    ideas: list[ReasonedIdea] = Field(description="3 a 5 ideas completas y listas para producir, cada una con dato, lectura, hipótesis y prueba")
    avoid: list[str] = Field(description="2 a 4 cosas a no hacer, basadas en datos o en la hoja del cliente")


EVENT_PROMPT = """Sos el/la director/a de estrategia creativa de Ranker. Armás la guía de {event} para {client} ({industry}, {market}).
Recibís cómo lo encaró el mercado en la edición anterior (`event_metrics`: cuándo arrancaron los anuncios respecto del día del evento, qué ofertas y qué tan profundas, cuánta urgencia, formatos, quién pautó), los anuncios de esa edición, la hoja del cliente y las fechas de la próxima edición si están.

Reglas:
1. Castellano claro, voseo, sin jerga en inglés. Separá siempre tres cosas: el DATO (conteos, porcentajes, días, montos de los datos, sin adjetivos), la LECTURA (qué significa para {client}) y la HIPÓTESIS (qué creemos que va a pasar, con "Creemos que..."). Nunca presentes una lectura o una hipótesis como si fuera un dato. El resumen (`summary`) lleva solo datos.
2. El calendario sale del timing real del mercado (si 6 de cada 10 anuncios arrancaron en la previa, la previa importa) y se escribe relativo al evento.
3. La oferta respeta la hoja del cliente: si no tiene promociones, no inventes descuentos; proponé beneficios que no bajen el precio y marcalos como propuesta.
4. Las ideas las escribís completas: título, gancho textual, guion por escenas con tiempos (o placa por placa), copy listo, CTA y dirección visual, con el tono de la hoja.
5. Si los datos son pocos (menos de 10 anuncios), decilo en el resumen y no generalices.
6. `event_metrics.own_results` y `own_daily` son los resultados reales de {client} en esa edición (inversión, resultados, costo por resultado, antes/durante/después). Son lo más importante: el resumen, la oferta y el calendario arrancan de ahí (qué anuncio y qué oferta le dieron el mejor costo por resultado, en qué momento rindió más). Citá los números.
7. Solo productos de las categorías de {client}; los anuncios de otro rubro ya fueron descartados.
8. Nunca escribas "resultados" a secas: decí siempre qué son (compras, conversaciones por WhatsApp/Messenger, leads o clics al sitio) y no sumes tipos distintos. Compará costos solo dentro del mismo tipo. No nombres campos ni variables (own_daily, own_results, event_metrics).
9. `event_metrics` (ofertas, urgencia, timing, quién pautó) es SOLO de la competencia. Si tiene 0 anuncios, decí que no hay datos de la competencia de esa edición y no saques conclusiones del mercado.
10. `event_metrics.organic`: publicaciones orgánicas de Instagram de {client} y de cada competidor en esa ventana (cantidad, likes y comentarios promedio, interacción sobre seguidores, cuántas nombraron el evento y las de más interacción con su texto). Es el dato de la competencia de esa edición: usalo para comparar qué temas, formatos y mensajes generaron más interacción. Interacción no es venta: decilo así. Si una cuenta tiene `source: perfil_publico`, solo se ven sus últimas publicaciones (desde `visible_since`): con 0 publicaciones en la ventana no digas que no publicó, decí que no se ve.
11. Cada anuncio propio trae `stage` (tofu = que nos conozcan, mofu = consideración, bofu = conversión) y el resultado que buscaba su campaña. Un anuncio de tráfico o alcance (tofu) no se juzga por leads ni ventas: se compara solo con los de su misma etapa y tipo de resultado.
12. Las ideas (`ideas`) son obligatorias: 3 a 5, completas, con guion escena por escena, copy y dirección visual, una por momento clave del calendario. Cada una con su cadena: `evidence` (el dato que la origina, citando de dónde sale), `reading`, `hypothesis` y `test` (cómo se prueba y con qué umbral se decide seguir o cortar). Si el dato es débil (pocos anuncios, una sola cuenta), decilo en `reading`.
13. Los montos están en la moneda de la cuenta (`currency`: "$" = pesos uruguayos, "US$" = dólares). Escribilos siempre con ese símbolo. Si no hay anuncios de la competencia de esa edición, decilo en una línea y trabajá con los resultados propios y el orgánico.
14. `event_metrics.data_coverage` dice con qué datos se armó la guía y qué faltó. Si la confianza es media o baja, el primer hecho del resumen dice qué faltó y las ideas se plantean como pruebas chicas, no como apuestas grandes.
15. Los campos `evidence`, `reading`, `hypothesis` y `test` van sin la etiqueta adelante (nada de "DATO:" o "PRUEBA:"; el informe ya la pone).
16. No inventes códigos de cupón, precios ni beneficios: si una pieza necesita un código, usá uno que ya haya usado {client} en los datos o escribí "con el código del evento" y aclará en `reading` que lo define el equipo. Lo mismo para cualquier beneficio que no esté en los datos ni en la hoja.
17. En el calendario, `message` es lo que lee la gente en la pieza. Nunca uses datos internos de la cuenta (costos, ROAS, "salió más caro") como argumento hacia el cliente final.
18. Al menos una de las ideas es para Instagram orgánico (`format` empieza con "orgánico") y se apoya en lo que más interacción generó en `event_metrics.organic` (de la competencia o propio), citando la publicación en `evidence`. Interacción no es venta: la prueba se mide en interacción, mensajes o visitas, no en compras."""


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
    ask = "Armá la guía, con las ideas completas."
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
        {"when": w, "goal": g, "format": f, "message": m, "cta": c}
        for w, g, f, m, c in (
            ("10 días antes", "anticipar", "historia", f"Se viene {event_name} en {client_name}", "Activá el recordatorio"),
            ("2 días antes", "calentar", "carrusel", "Lo que va a estar en oferta", "Mirá la selección"),
            ("el día", "lanzar", "video", f"Arrancó {event_name}", "Comprá ahora"),
            ("último día", "cerrar", "imagen", "Últimas horas", "Aprovechá hoy"),
        )
    ]
    return {"summary": summary, "offer_recommendation": f"(Sin IA) La oferta más usada fue {top_offer}.", "calendar": calendar,
            "ideas": [], "avoid": ["(Sin IA) Correr con IA para obtener ideas con guion."]}
