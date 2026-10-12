"""Plan creativo (formato editorial): auditoría propia → diagnóstico de ejecución → huecos → ideas con
referencia real → por dónde empezar.

Primero se mira qué ya tiene el cliente en el aire (para no repetir), después cómo está hecho comparado con
las referencias, y recién ahí se proponen ideas; cada idea se apoya en un anuncio real del monitoreo o en un
hallazgo de la investigación web, con su link.
"""
from __future__ import annotations

import datetime as dt
import json
from typing import Any, Literal

from pydantic import Field

from ..config import settings
from .client import structured_call
from .synthesize import _compact_items, _Strict


class GalleryItem(_Strict):
    creative_id: int = Field(description="id de un anuncio PROPIO de los datos")
    caption: str = Field(description="Qué es y qué muestra de la ejecución, 1 oración (ej. 'Le habla al comprador, no a quien lo usa')")


class Gap(_Strict):
    title: str = Field(description="El hueco en pocas palabras (ej. 'Nadie le habla a quien compra para su empresa')")
    detail: str = Field(description="1-2 oraciones con el dato que lo prueba")


class Idea(_Strict):
    title: str = Field(description="La idea en una frase concreta")
    start_now: bool = Field(description="True si se puede arrancar ya con lo que hay")
    tags: list[Literal["venta", "marca", "meta", "google", "organico", "accion", "alianzas", "producto", "contenido", "prensa"]]
    effort: Literal["bajo", "medio", "alto"]
    what_to_do: list[str] = Field(description="1 a 3 párrafos cortos: qué se hace, cómo y por qué funciona para este cliente")
    copy_lines: list[str] = Field(description="0 a 3 líneas de copy listas, en el tono del cliente (vacío si la idea no es un anuncio)")
    reference_creative_id: int | None = Field(description="id de un anuncio de los datos que respalda la idea, o null")
    reference_url: str = Field(description="link de un hallazgo de la investigación que respalda la idea, o ''")
    reference_note: str = Field(description="Qué hace la referencia, citando su copy textual entre comillas y su fecha si la hay")
    evidence: str = Field(description="DATO: el hecho de los datos que origina la idea, con números y de dónde sale")
    reading: str = Field(description="LECTURA: qué significa ese dato para el cliente, en una oración")
    hypothesis: str = Field(description="HIPÓTESIS: qué creemos que va a pasar si se hace, empezando con 'Creemos que'")
    how_to_measure: str = Field(description="PRUEBA: cómo se comprueba (contra qué se compara, cuánto tiempo, inversión mínima) y el umbral para seguir o cortar")


class Step(_Strict):
    when: str = Field(description="Mes (ej. 'Noviembre')")
    what: str = Field(description="Qué ideas (por número) o acciones")
    why: str = Field(description="Por qué en ese momento (fecha comercial, estacionalidad, dependencia)")


class PlanOutput(_Strict):
    thesis: str = Field(description="Titular del informe con la tesis, máx. 10 palabras (ej. 'Lo que <cliente> todavía no probó')")
    standfirst: str = Field(description="2-3 oraciones: qué se revisó (con cantidades) y qué sale de ahí")
    audit_ads: list[str] = Field(description="Ángulos y piezas que el cliente YA tiene en el aire, cada uno con su ejemplo textual")
    audit_other: list[str] = Field(description="Otras cosas que ya hace (resultados destacados de su cuenta, formatos), o vacío")
    gallery: list[GalleryItem] = Field(description="2 a 4 anuncios propios que ilustran el diagnóstico")
    diagnosis: str = Field(description="Un párrafo: qué rasgos de ejecución comparten sus anuncios, con números, y cuál es el problema real")
    gaps: list[Gap] = Field(description="4 a 6 huecos")
    ideas: list[Idea] = Field(description="6 a 9 ideas, primero las de arrancar ya")
    roadmap: list[Step] = Field(description="4 a 6 pasos en los próximos 3 meses")
    method_note: str = Field(description="Cómo leer esto: qué muestran y qué no muestran las fuentes usadas, en 3-4 oraciones")


PLAN_PROMPT = """Sos el/la director/a de estrategia creativa de Ranker (Montevideo). Escribís el plan creativo de {client} ({industry}, {market}).

Orden de trabajo (es el orden del informe):
1. Auditoría: primero mirás qué ya tiene {client} en el aire (`own_audit`, `own_ads`), qué publica en su Instagram (`own_instagram`: publicaciones de los últimos 90 días, temas, formatos, interacción) y sus resultados reales (`own_performance`). No proponés nada que ya esté hecho.
2. Diagnóstico: cómo está HECHO lo que publica (`execution.own` contra `execution.refs` y `execution.gaps`: persona a cámara, precio, canal del CTA, a quién le habla, resolución visual). Si los ángulos están cubiertos y el problema es de ejecución, decilo así.
3. Huecos: lo que nadie ocupa o lo que {client} no hace y las referencias sí. Cada hueco con el dato que lo prueba.
4. Ideas: cada una resuelve un hueco y se apoya en una referencia real: un anuncio de los datos (`reference_creative_id`, citando su copy textual y su fecha) o un hallazgo de `research` (`reference_url`). Sin referencia no hay idea. Pueden ser anuncios, acciones, alianzas, producto o contenido, siempre que el cliente pueda hacerlo.
5. Por dónde empezar: los próximos 3 meses desde {next_month}, cruzados con las fechas comerciales de `calendar`.

Reglas:
- Castellano de Uruguay, voseo, frases cortas. Separá siempre el DATO (números de los datos, sin adjetivos), la LECTURA (qué significa) y la HIPÓTESIS ("Creemos que..."). Auditoría y diagnóstico van con datos; las ideas llevan su cadena completa (`evidence`, `reading`, `hypothesis`, `how_to_measure`). Nunca presentes una hipótesis como hecho.
- `data_coverage` dice con qué datos se armó el plan y qué faltó. Si la confianza es media o baja, decilo en `method_note` y planteá las ideas como pruebas chicas.
- La hoja del cliente (`client_brief`) es ley: tono, qué vender, qué evitar, promos posibles. No inventes precios ni descuentos.
- El copy suena a una persona, no a un folleto: nada de "descubrí", "elevá", "no es solo X, es Y", "¿Sabías que...?", adjetivos vacíos.
- Solo productos y categorías que vende el cliente.
- La Biblioteca de anuncios muestra qué se publica, no qué rinde: lo que sostiene una marca en el aire y replica en versiones es una apuesta, no una prueba. Decilo en la nota de método."""


MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "setiembre", "octubre", "noviembre", "diciembre"]


def _calendar(today: dt.date) -> list[dict[str, Any]]:
    from ..config import load_events

    out = []
    for ev in load_events().values():
        if ev.next_start and 0 <= (ev.next_start - today).days <= 120:
            out.append({"evento": ev.name, "fecha": ev.next_start.isoformat()})
    return sorted(out, key=lambda e: e["fecha"])


def synthesize_plan(metrics: dict[str, Any], items: list[dict[str, Any]], client_info: Any, findings: list[dict[str, Any]],
                    period: str) -> dict[str, Any]:
    today = dt.date.today()
    own = [it for it in items if it.get("tier") == "own"]
    market = [it for it in items if it.get("tier") != "own"]
    from ..analysis.data_coverage import plan_coverage
    from .synthesize import sample_note

    metrics["data_coverage"] = plan_coverage(metrics, len(market), len(findings))
    metrics["sample"] = sample_note(market, 120)
    payload = {
        "data_coverage": metrics["data_coverage"],
        "client": {"name": client_info.name, "industry": client_info.industry, "market": client_info.market, "description": client_info.description},
        "client_brief": client_info.brief or None,
        "period": period,
        "own_audit": metrics.get("own_audit"),
        "own_performance": metrics.get("own_performance"),
        "own_instagram": metrics.get("own_instagram") or None,
        "execution": metrics.get("execution"),
        "coverage": metrics.get("coverage"),
        "signals": metrics.get("signals"),
        "own_ads": _compact_items(own, limit=60),
        "reference_ads": _compact_items(market, limit=120),
        "research": findings,
        "calendar": _calendar(today),
    }
    nm = today.replace(day=1) + dt.timedelta(days=32)
    next_month = f"{MESES[nm.month - 1]} {nm.year}"
    system = PLAN_PROMPT.format(client=client_info.name, industry=client_info.industry or "", market=client_info.market or "",
                                next_month=next_month)
    content = [{"type": "text", "text": "Datos (JSON):\n" + json.dumps(payload, ensure_ascii=False, default=str) + "\n\nEscribí el plan."}]
    out = structured_call(PlanOutput, system, content, model=settings.synth_model, max_tokens=20000, effort="high", stream=True)
    return {"version": 2, **out.model_dump(), "research": findings, "data_coverage": metrics["data_coverage"], "sample": metrics["sample"]}


def mock_plan(metrics: dict[str, Any], items: list[dict[str, Any]], client_name: str) -> dict[str, Any]:
    """Versión sin IA: el esqueleto con los datos calculados (para probar el circuito)."""
    from ..analysis.data_coverage import plan_coverage

    audit = metrics.get("own_audit") or {}
    ex = metrics.get("execution") or {}
    own = [it for it in items if it.get("tier") == "own" and it.get("source") != "meta_insights"]
    market = [it for it in items if it.get("tier") != "own"]
    gaps = [{"title": g["dimension"], "detail": f"{client_name}: {g['own']} · referencias: {g['refs']}."} for g in ex.get("gaps") or []]
    ideas = [{"title": f"(Sin IA) Resolver: {g['title']}", "start_now": True, "tags": ["venta", "meta"], "effort": "bajo",
              "what_to_do": ["Correr con IA para obtener la idea completa."], "copy_lines": [],
              "reference_creative_id": market[i]["id"] if i < len(market) else None, "reference_url": "",
              "reference_note": (market[i].get("headline") or "") if i < len(market) else "", "how_to_measure": "—",
              "evidence": g["detail"], "reading": "", "hypothesis": ""}
             for i, g in enumerate(gaps[:4])]
    return {
        "version": 2,
        "thesis": f"Lo que {client_name} todavía no probó",
        "standfirst": f"Se revisaron {audit.get('active', 0)} anuncios activos de {client_name} y {len(market)} de referencia. (Versión sin IA.)",
        "audit_ads": [f"{a['angle'].replace('_', ' ')} ({a['count']}): «{a['example']}»" for a in audit.get("angles", [])][:8],
        "audit_other": [],
        "gallery": [{"creative_id": it["id"], "caption": (it.get("cls") or {}).get("talks_to") or ""} for it in own[:4]],
        "diagnosis": "(Sin IA) Ver la tabla de ejecución: persona a cámara, precio y canal del CTA contra las referencias.",
        "gaps": gaps,
        "ideas": ideas,
        "roadmap": [],
        "method_note": "La Biblioteca de anuncios muestra qué se publica, no qué rinde.",
        "research": [],
        "data_coverage": plan_coverage(metrics, len(market), 0),
    }
