"""Etapa 2 — Síntesis estratégica sobre creativos YA clasificados + métricas + histórico.

Devuelve JSON estructurado que luego un humano aprueba/edita/rechaza antes de Basecamp.
Regla del prompt: toda afirmación cita conteos (X anuncios / Y competidores / Z%).
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..config import settings
from .client import structured_call


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Insight(_Strict):
    title: str
    observation: str = Field(description="Dato observable con números")
    interpretation: str = Field(description="Inferencia estratégica, redactada como hipótesis")
    evidence: list[str] = Field(description="Cifras concretas: 'X anuncios / Y competidores / Z%'")
    implication_for_us: str
    confidence: str = Field(description="low | medium | high")


class ReferenceCreative(_Strict):
    creative_id: int
    competitor: str
    why_relevant: str
    what_not_to_copy: str
    adaptation_ideas: list[str] = Field(description="Variaciones propias, no copias")


class ConceptIdea(_Strict):
    format: str
    hook_type: str
    angle: str
    copy_draft: str = Field(description="Copy o guion breve en español rioplatense")


class Opportunity(_Strict):
    title: str
    rationale: str
    evidence: list[str]
    concepts: list[ConceptIdea]


class Risk(_Strict):
    title: str
    description: str
    evidence: list[str]


class ActionItem(_Strict):
    title: str
    description: str
    owner_role: str = Field(description="Ej: Creative lead, Copywriter, Media buyer")
    due_in_days: int


class SynthesisOutput(_Strict):
    period_label: str
    executive_summary: list[str] = Field(description="Exactamente 3 cambios del mes, cada uno con cifras")
    top_insights: list[Insight] = Field(description="Top 3")
    local_vs_global: str
    reference_creatives: list[ReferenceCreative]
    unexploited_angles: list[str]
    white_space: list[Opportunity]
    recommendations: list[Opportunity]
    risks: list[Risk] = Field(description="Saturación y riesgos de mensajería")
    ours_vs_market: str | None
    action_items: list[ActionItem]


SYSTEM_PROMPT = """Sos el/la director/a de estrategia de un equipo creativo de performance para miSeguro (aseguradora digital en Uruguay).
Recibís las métricas mensuales del monitoreo competitivo (Meta + Google), la lista de creativos ya clasificados y, si existe, el mes anterior y nuestros propios anuncios.

Principios no negociables:
1. Separá siempre DATO de INFERENCIA. El dato es un conteo o una fecha. La inferencia es una hipótesis y se redacta como tal ("podría indicar", "sugiere").
2. Nunca afirmes que un anuncio "funciona" o "es el mejor". Podés decir que lleva N días activo, que tiene N variantes o que aparece en N competidores.
3. Cada afirmación del executive summary y de cada insight cita cifras: X anuncios / Y competidores / Z%.
4. Los creativos de referencia se eligen por longevidad, cantidad de variantes o novedad de ángulo, y siempre con adaptación propia: no copiar, desarrollar.
5. Las oportunidades salen del white space y de los gaps entre mercado y miSeguro. Cada oportunidad trae conceptos ejecutables (formato + hook + ángulo + copy en español rioplatense).
6. Los riesgos son de saturación: si 8 de 10 competidores dicen lo mismo, no queremos ser el número 11 sin un giro.
7. Escribí en español, claro, para un equipo creativo que lo va a ejecutar la semana siguiente.
8. Las distribuciones `*_weighted` ponderan cada competidor por igual; usalas para hablar de 'cuántos competidores' y las crudas para 'cuántos anuncios'. Un anunciante con 100 creativos no define el mercado por sí solo.
9. Si los datos son escasos (pocos creativos, un solo mes), decilo explícitamente y bajá la confianza; no rellenes."""


def _compact_items(items: list[dict[str, Any]], limit: int = 400) -> list[dict[str, Any]]:
    out = []
    for it in sorted(items, key=lambda x: -x["days_active"])[:limit]:
        cls = it.get("cls") or {}
        out.append(
            {
                "id": it["id"],
                "competitor": it["competitor"],
                "tier": it["tier"],
                "platform": it["platform"],
                "format": it["format"],
                "days_active": it["days_active"],
                "status": it["status"],
                "headline": (it.get("headline") or "")[:160],
                "body": (it.get("body_text") or "")[:280],
                "product": cls.get("product"),
                "hook": cls.get("hook_type"),
                "angle": cls.get("primary_angle"),
                "family": cls.get("angle_family"),
                "funnel": cls.get("funnel_stage"),
                "style": cls.get("creative_style"),
                "offer": cls.get("offer"),
                "claims": cls.get("key_claims"),
                "summary": cls.get("summary"),
            }
        )
    return out


def synthesize(
    metrics: dict[str, Any],
    items: list[dict[str, Any]],
    concepts: list[dict[str, Any]],
    period: str,
    previous_metrics: dict[str, Any] | None = None,
    own_items: list[dict[str, Any]] | None = None,
    model: str | None = None,
    client=None,
) -> dict[str, Any]:
    payload = {
        "period": period,
        "metrics": metrics,
        "previous_period_metrics": previous_metrics or None,
        "concepts_top": concepts[:40],
        "creatives": _compact_items([it for it in items if it.get("tier") != "own"]),
        "our_creatives": _compact_items(own_items or [], limit=100),
    }
    content = [{"type": "text", "text": "Datos del mes (JSON):\n" + json.dumps(payload, ensure_ascii=False, default=str) + "\n\nProducí la síntesis estratégica."}]
    result = structured_call(
        SynthesisOutput, SYSTEM_PROMPT, content, model=model or settings.synth_model, max_tokens=32000, effort="high", stream=True, client=client
    )
    return result.model_dump()


def mock_synthesis(metrics: dict[str, Any], items: list[dict[str, Any]], concepts: list[dict[str, Any]], period: str) -> dict[str, Any]:
    """Síntesis determinística a partir de métricas: para demo/tests, sin IA. Mantiene la regla dato/inferencia."""
    fam = metrics.get("angle_family_dist") or []
    hooks = metrics.get("hook_dist") or []
    totals = metrics.get("totals", {})
    top_fam = fam[0] if fam else {"value": "n/d", "pct": 0, "count": 0}
    top_hook = hooks[0] if hooks else {"value": "n/d", "pct": 0, "count": 0}
    ws = (metrics.get("white_space") or {}).get("angle_families") or []
    long_ids = (metrics.get("longevity") or {}).get("top_ids", [])[:3]
    by_id = {it["id"]: it for it in items}
    refs = [
        ReferenceCreative(
            creative_id=i,
            competitor=by_id[i]["competitor"],
            why_relevant=f"Lleva {by_id[i]['days_active']} días activo (dato). Permanencia alta podría indicar que el anunciante lo considera útil (inferencia).",
            what_not_to_copy="El hook literal y la estética del competidor.",
            adaptation_ideas=["Misma tensión, cuantificar el costo del riesgo en pesos", "Versión UGC 15s con la misma promesa"],
        ).model_dump()
        for i in long_ids
        if i in by_id
    ]
    n_comp = totals.get("competitors", 0)
    out = SynthesisOutput(
        period_label=period,
        executive_summary=[
            f"La familia de ángulos dominante fue '{top_fam['value']}': {top_fam['count']} anuncios / {n_comp} competidores / {top_fam['pct']}%.",
            f"El hook más usado fue '{top_hook['value']}' ({top_hook['pct']}% de {totals.get('classified', 0)} creativos clasificados).",
            f"Se observaron {totals.get('creatives', 0)} creativos ({totals.get('active', 0)} activos) en {', '.join(f'{k}: {v}' for k, v in (totals.get('platforms') or {}).items())}.",
        ],
        top_insights=[
            Insight(
                title=f"Concentración en {top_fam['value']}",
                observation=f"{top_fam['pct']}% de los creativos clasificados usan la familia {top_fam['value']}.",
                interpretation="Podría indicar saturación del argumento; diferenciarse requiere un giro o cuantificación.",
                evidence=[f"{top_fam['count']} anuncios / {n_comp} competidores / {top_fam['pct']}%"],
                implication_for_us="Evitar ser el anuncio N+1 con el mismo mensaje; probar cuantificación del riesgo.",
                confidence="medium" if totals.get("classified", 0) >= 20 else "low",
            )
        ],
        local_vs_global="Comparación local vs global disponible en la matriz de ángulos por tier (ver métricas).",
        reference_creatives=refs,
        unexploited_angles=[w["value"] for w in ws],
        white_space=[
            Opportunity(
                title=f"White space: {w['value']}",
                rationale=f"Solo {w['pct']}% del mercado usa esta familia de ángulos (dato).",
                evidence=[f"{w['pct']}% de {totals.get('classified', 0)} creativos"],
                concepts=[ConceptIdea(format="video", hook_type="pregunta", angle=w["value"], copy_draft="[Borrador a desarrollar por el equipo creativo]")],
            )
            for w in ws[:3]
        ],
        recommendations=[],
        risks=[
            Risk(
                title=f"Saturación en {top_fam['value']}",
                description="Mensaje repetido por la mayoría del mercado.",
                evidence=[f"{top_fam['pct']}% de los creativos"],
            )
        ],
        ours_vs_market=("Comparación disponible" if (metrics.get("ours_vs_market") or {}).get("has_own") else None),
        action_items=[
            ActionItem(title="Validar insights del mes", description="Revisar top 3 insights y oportunidades; aprobar/editar.", owner_role="Head of Growth", due_in_days=2),
            ActionItem(title="Producir 3 conceptos de white space", description="Un static, un UGC y un reel según oportunidades.", owner_role="Creative lead", due_in_days=7),
        ],
    )
    return out.model_dump()
