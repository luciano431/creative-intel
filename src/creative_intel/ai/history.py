"""Análisis del informe "Qué hicimos": la pauta propia año por año, qué funcionó y el plan que sale de ahí."""
from __future__ import annotations

import json
from typing import Any

from pydantic import Field

from ..config import settings
from .client import structured_call
from .synthesize import _Strict


class HFact(_Strict):
    headline: str = Field(description="Titular corto con el dato (máx. 12 palabras)")
    detail: str = Field(description="El dato con sus números y el año, en una o dos oraciones. Sin interpretar")


class YearRead(_Strict):
    year: str = Field(description="El año, tal cual (ej. '2025')")
    what_was_done: str = Field(description="Qué se hizo ese año: cuánto se invirtió, en qué campañas, productos, fechas y formatos. Solo datos")
    what_worked: str = Field(description="Qué dio mejor resultado, con el anuncio, el tipo de resultado y su costo")
    what_didnt: str = Field(description="Qué gastó sin traer lo que buscaba o salió más caro, con números")


class Learning(_Strict):
    title: str = Field(description="El aprendizaje en pocas palabras (máx. 10)")
    verdict: str = Field(description="repetir | iterar | cortar | probar")
    evidence: str = Field(description="El dato que lo sostiene, con números, año y anuncio o campaña de donde sale")
    reading: str = Field(description="Qué significa para el cliente, en una oración. Si el dato es débil, decirlo")
    hypothesis: str = Field(description="Qué creemos que va a pasar si se aplica, empezando con 'Creemos que'")


class CompetitorRead(_Strict):
    brand: str = Field(description="La marca, tal cual viene en los datos")
    what_they_run: str = Field(description="Qué tiene corriendo hoy: productos, ofertas, mensajes y formatos, con cantidades. Solo datos")
    what_seems_to_work: str = Field(description="Qué parece estar rindiéndole y por qué señal (días al aire, varias versiones, lo que repite). Decir que es una señal, no un resultado")
    for_us: str = Field(description="Qué significa para el cliente: qué tomar como aprendizaje, qué evitar o qué hueco deja, en una o dos oraciones")


class PlanItem(_Strict):
    title: str = Field(description="Nombre de trabajo de la pieza o acción (máx. 6 palabras)")
    when: str = Field(description="Cuándo: mes o fecha comercial con su previa (ej. 'CyberLunes, 10 días antes')")
    goal: str = Field(description="Para qué: vender, juntar conversaciones, que nos conozcan")
    format: str = Field(description="imagen | carrusel | catálogo | video")
    product: str = Field(description="Producto o categoría del cliente")
    angle: str = Field(description="Ángulo del mensaje en pocas palabras")
    brief: str = Field(description="Qué tiene que lograr y qué tiene que decir o mostrar, en 2-3 oraciones. Sin escribir la pieza")
    based_on: str = Field(description="repetir | iterar | probar: si repite algo que ya funcionó, lo mejora o prueba algo nuevo")
    evidence: str = Field(description="El dato de años anteriores que la origina, citando año y anuncio")
    reading: str = Field(description="Qué significa ese dato, en una oración")
    hypothesis: str = Field(description="Qué creemos que va a pasar, empezando con 'Creemos que'")
    test: str = Field(description="Cómo se mide (contra qué, cuánto tiempo, inversión mínima) y el umbral para seguir o cortar")


class HistoryPlan(_Strict):
    summary: list[HFact] = Field(description="4 a 6 hechos de la pauta de esos años, solo datos")
    years: list[YearRead] = Field(description="Un bloque por año, del más viejo al más nuevo")
    learnings: list[Learning] = Field(description="4 a 8 aprendizajes, cada uno con su veredicto")
    competition_summary: str = Field(description="Qué está haciendo la competencia hoy, en 2 a 4 oraciones con datos (vacío si no hay datos de la competencia)")
    competition: list[CompetitorRead] = Field(description="Una lectura por marca con anuncios activos hoy (vacía si no hay)")
    plan: list[PlanItem] = Field(description="5 a 8 piezas o acciones para los próximos meses, en orden de salida")
    avoid: list[str] = Field(description="2 a 4 cosas a no repetir, basadas en datos")


HISTORY_PROMPT = """Sos el/la director/a de estrategia de Ranker. Analizás la pauta de Meta de {client} ({industry}, {market}) de {years} para decidir qué hacer ahora: qué repetir, qué iterar, qué cortar y qué probar.
Recibís, por año: el total del año (`total`), el mismo tramo del año (`ytd`: del 1 de enero al día de hoy, para comparar parejo con el año en curso), mes por mes (`months`), las campañas con más inversión, la inversión por etapa (`stage_spend`), y los anuncios que vale la pena mirar (`picks`: más inversión, mejor costo por compra, mejor costo por conversación o lead, y los que gastaron sin traer resultados), con su texto y formato. También cada fecha comercial (`events`) con su previa de 10 días, sus números y sus anuncios con más inversión, los nombres de anuncio que se repitieron de un año a otro (`repeated`) y las fechas comerciales que vienen (`next_dates`).

Reglas:
1. Castellano claro, voseo, sin jerga en inglés. Separá siempre el DATO (montos, cantidades, costos, sin adjetivos), la LECTURA (qué significa) y la HIPÓTESIS ("Creemos que..."). El resumen y `what_was_done` llevan solo datos.
2. Solo pauta. No hay datos orgánicos en este informe: no hables de Instagram orgánico.
3. Para comparar años usá `ytd` (mismo tramo); el `total` del año en curso está incompleto. Para estacionalidad y fechas comerciales usá `months` y `events`. Citá siempre el año de cada dato.
4. Nunca escribas "resultados" a secas: decí qué son (compras, conversaciones, leads, clics al sitio) y no sumes tipos distintos. Compará costos solo dentro del mismo tipo.
5. Cada anuncio trae `stage` (tofu = que nos conozcan, mofu = consideración, bofu = conversión) y el resultado que buscaba su campaña. Uno de tráfico o alcance no se juzga por compras.
6. Los montos van con el símbolo de la moneda de la cuenta (`currency`: USD → "US$", UYU → "$").
7. Lo que funcionó una sola vez es una pista, no una regla: marcalo como "probar" o "iterar". Lo que funcionó en más de un año o en más de una fecha es "repetir". Lo que gastó sin traer lo que buscaba, más de una vez, es "cortar".
8. Las ofertas y cupones de los anuncios viejos ya no están vigentes: usalos como aprendizaje (qué tamaño y forma de oferta rindió), no como oferta a repetir. No inventes cupones, precios ni beneficios: si una pieza necesita uno, decí "con la oferta que defina el equipo".
9. El plan cubre las fechas de `next_dates` y los meses que vienen. Cada pieza sale de un aprendizaje y cita su dato. Formato: imagen, carrusel o catálogo; video solo si los datos muestran que el video rindió claramente mejor (y decilo).
10. Si un año no tiene inversión o tiene pocos anuncios, decilo y no generalices. `data_coverage` dice qué faltó.
11. Vos hacés el análisis y los briefs; las piezas (texto, copy) las escribe después un redactor. No escribas copy.
12. Los campos `evidence`, `reading`, `hypothesis` y `test` van sin la etiqueta adelante. No nombres campos ni variables.
13. `competition` son los anuncios que cada competidor tiene activos HOY en la Biblioteca de Meta (`competition_date`): texto, formato, desde cuándo están al aire (`days_running`) y si tienen varias versiones. Meta no muestra inversión ni resultados de otras marcas: nunca digas que a un competidor "le funcionó" algo como dato; decí que es una señal (un anuncio que lleva 30 días o más prendido, o con varias versiones, probablemente le rinde). Si una marca no tiene anuncios activos, decilo en una línea.
14. Cruzá la competencia con lo propio: si algo que la competencia sostiene hace semanas coincide con lo que a {client} le funcionó, eso refuerza el aprendizaje; si la competencia satura un mensaje, es un hueco para diferenciarse. El plan puede apoyarse en esto citando la marca y el anuncio."""


def synthesize_history(metrics: dict[str, Any], client_info: Any, next_dates: list[dict[str, str]]) -> dict[str, Any]:
    years = sorted(metrics.get("years") or {})
    payload = {"client": {"name": client_info.name, "industry": client_info.industry, "market": client_info.market},
               "client_brief": client_info.brief or None, "next_dates": next_dates, **metrics}
    content = [{"type": "text", "text": "Pauta de la cuenta (JSON):\n" + json.dumps(payload, ensure_ascii=False, default=str)
                + "\n\nArmá el análisis año por año, los aprendizajes y el plan."}]
    system = HISTORY_PROMPT.format(client=client_info.name, industry=client_info.industry or "sin industria definida",
                                   market=client_info.market or "mercado local", years=", ".join(years))
    return structured_call(HistoryPlan, system, content, model=settings.synth_model, max_tokens=40000, effort="high", stream=True).model_dump()


def mock_history_plan(metrics: dict[str, Any], client_name: str) -> dict[str, Any]:
    """Sin IA (para probar el circuito): solo los números."""
    summary, years = [], []
    for y, yd in sorted((metrics.get("years") or {}).items()):
        t = yd["total"]
        buy = (t.get("by_type") or {}).get("compras") or {}
        summary.append({"headline": f"{y}: {t['spend']} invertidos", "detail": f"{buy.get('total', 0)} compras, costo {buy.get('cost') or '—'}."})
        years.append({"year": y, "what_was_done": f"{yd['ads_with_spend']} anuncios en {yd['campaign_count']} campañas.",
                      "what_worked": "(Sin IA)", "what_didnt": "(Sin IA)"})
    comp = [{"brand": c["name"], "what_they_run": f"{len(c['ads'])} anuncios activos hoy.", "what_seems_to_work": f"{c['long_running']} llevan 30 días o más.",
             "for_us": "(Sin IA)"} for c in metrics.get("competition") or []]
    return {"summary": summary, "years": years, "learnings": [], "plan": [], "competition_summary": "", "competition": comp,
            "avoid": [f"(Sin IA) Correr sin --mock-ai para el análisis de {client_name}."]}
