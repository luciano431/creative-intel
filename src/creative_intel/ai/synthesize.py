"""Etapa 2 — Síntesis estratégica sobre creativos YA clasificados + métricas + histórico.

Devuelve JSON estructurado que luego un humano aprueba/edita/rechaza antes de Basecamp.
Regla del prompt: toda afirmación cita conteos (X anuncios / Y competidores / Z%).
"""
from __future__ import annotations

import json
from collections import Counter
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..config import settings
from .client import structured_call


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SummaryPoint(_Strict):
    headline: str = Field(description="Frase corta y clara (máx. 14 palabras), sin jerga: qué está pasando")
    detail: str = Field(description="Una o dos oraciones que lo explican con una cifra entendible (ej. '8 de cada 10 anuncios')")
    action: str = Field(description="Qué hacemos nosotros con esto, en una oración")


class Insight(_Strict):
    title: str = Field(description="Titular corto (máx. 10 palabras) con el dato")
    observation: str = Field(description="El dato, en una o dos oraciones: conteos, %, días al aire o texto literal. Sin interpretar")
    evidence: list[str] = Field(description="Cifras concretas: 'X anuncios de Y competidores'")
    implication_for_us: str = Field(description="Qué hacemos nosotros, en una oración")


class ReferenceCreative(_Strict):
    creative_id: int
    competitor: str
    why_relevant: str
    what_not_to_copy: str
    adaptation_ideas: list[str] = Field(description="Variaciones propias, no copias")


class ConceptIdea(_Strict):
    title: str = Field(description="Nombre corto de la idea (máx. 6 palabras)")
    format: str
    hook_type: str
    angle: str
    hook_line: str = Field(description="Lo que se dice o se ve en los primeros 3 segundos / la primera línea, textual")
    script: list[str] = Field(description="Guion listo para producir, 4 a 6 pasos con tiempos ('0–3 s · ...'); placa por placa si es carrusel")
    copy_draft: str = Field(description="Texto del anuncio listo para publicar, en el tono del cliente")
    cta: str = Field(description="Llamado a la acción, textual")
    visual_direction: str = Field(description="Cómo se ve: escena, casting, recursos visuales")


class Opportunity(_Strict):
    title: str
    priority: str = Field(description="alta | media | baja")
    rationale: str = Field(description="Por qué es una oportunidad, en lenguaje simple")
    who_does_it: str = Field(description="Quién lo hace hoy en el mercado (o 'nadie'), con cifras")
    evidence: list[str]
    concepts: list[ConceptIdea] = Field(description="1 a 3 ideas ejecutables")
    how_to_measure: str = Field(description="Cómo sabremos si funcionó (métrica y umbral)")


class Risk(_Strict):
    title: str
    severity: str = Field(description="alta | media | baja")
    description: str = Field(description="Qué está pasando")
    why_it_matters: str = Field(description="Por qué nos afecta")
    mitigation: str = Field(description="Cómo lo evitamos, concreto")
    evidence: list[str]


class ActionItem(_Strict):
    title: str
    description: str
    priority: str = Field(description="alta | media | baja")
    owner_role: str = Field(description="Ej: Líder creativo, Redactor, Media buyer, Diseño")
    due_in_days: int
    deliverable: str = Field(description="Qué se entrega concretamente")
    success_metric: str = Field(description="Cómo medimos el resultado")


class SynthesisOutput(_Strict):
    period_label: str
    executive_summary: list[SummaryPoint] = Field(description="Exactamente 3 puntos")
    top_insights: list[Insight] = Field(description="3 a 4 hallazgos, cortos, solo datos")
    local_vs_global: str
    reference_creatives: list[ReferenceCreative]
    unexploited_angles: list[str]
    white_space: list[Opportunity]
    recommendations: list[Opportunity]
    risks: list[Risk] = Field(description="Saturación y riesgos de mensajería")
    ours_vs_market: str | None
    action_items: list[ActionItem] = Field(description="4 a 8 acciones ordenadas por prioridad")


SYSTEM_PROMPT_TEMPLATE = """Sos el/la director/a de estrategia creativa de Ranker. Armás el plan creativo mensual para {client} ({industry}, {market}).
Recibís las métricas del monitoreo competitivo (Meta + Google), los creativos ya clasificados y, si existen, el mes anterior y los anuncios de {client}.

Cómo escribir (lo lee el cliente, no un analista):
1. Castellano claro y directo. Nada de jerga en inglés: no digas "hook", "white space", "TOFU", "insight", "branding" ni "awareness". Usá los nombres del diccionario `labels` que viene en los datos (ej. "Prueba social", "Descubrimiento").
2. Números que se entiendan: "8 de cada 10 anuncios", "6 de los 10 competidores", "lleva 120 días al aire". Evitá listas de porcentajes sueltos.
3. Solo hechos. Cada afirmación sale de los datos: un conteo, un porcentaje, días al aire, una fecha o un texto literal de un anuncio. Prohibido suponer intenciones, estrategias o resultados de la competencia ("apuesta a", "busca", "probablemente", "podría indicar", "sugiere", "parece"). Si no está en los datos, no se dice.
4. Nunca afirmes que un anuncio "funciona" o "es el mejor". Podés decir cuántos días lleva activo, cuántas variantes tiene o cuántos competidores lo usan.
5. Resumen ejecutivo: exactamente 3 puntos, cada uno con un titular corto, una explicación con una cifra y qué hacemos.
5b. Hallazgos: 3 o 4, cortos. Cada uno: titular con el dato, el dato en una o dos oraciones con sus cifras, y qué hacemos en una oración. Nada de interpretación.
6. Oportunidades: salen de los mensajes y ganchos que casi nadie usa y de las diferencias entre el mercado y {client}. Cada una trae prioridad, por qué, quién lo hace hoy, 1 a 3 ideas y cómo lo vamos a medir.
6b. Las ideas las escribís vos, completas y listas para producir: título, gancho textual (lo que se dice o se ve en los primeros 3 segundos), guion escena por escena con tiempos (placa por placa si es carrusel), copy listo para publicar, CTA y dirección visual. Apoyate en lo que viste en los datos (qué falta en el mercado, qué promesas y promociones usa la competencia) y en el tono de {client}. Nunca derives al equipo creativo ni dejes textos entre corchetes: el equipo creativo ajusta y produce, no arranca de cero.
7. Riesgos: saturación (si 8 de 10 competidores dicen lo mismo, no queremos ser el número 11 sin un giro), con severidad, por qué importa y cómo lo evitamos.
8. Plan de acción: 4 a 8 acciones ordenadas por prioridad, con responsable, plazo en días, entregable concreto y cómo medimos el resultado.
9. Cada competidor pesa por igual al hablar de "el mercado": un anunciante con 100 anuncios no define el mercado por sí solo. Usá `market_profile_weighted` y `saturation` para eso.
10. Cobertura (`coverage`): cuántos anuncios se analizaron de cada competidor en Meta y en Google, contra el objetivo (`target`). Si un competidor tiene menos que el objetivo en una plataforma, NO saques conclusiones de él en esa plataforma: solo informá el faltante como dato ("Belgotex: 2 anuncios en Meta, 0 en Google"). Las cifras del mercado se calculan con lo que sí hay y siempre decís sobre cuántos anuncios. No rellenes lo que falta.
10b. Señales de que algo funciona (`metrics.signals`): meses seguidos entre los más vistos, días al aire, versiones de la misma pieza, anuncios que entraron o salieron del top (`movement`) e ideas que una marca repite con varias versiones (`repeated_ideas`). Son la mejor evidencia que tenemos: usalas para elegir referencias y oportunidades, y citalas como hechos ("lleva 3 meses entre los más vistos y tiene 6 versiones"), nunca como prueba de resultados.
11. Si viene `client_brief` (la hoja del cliente), es ley: escribí con su tono, a su público, empujando lo que quieren vender más y apoyándote en su diferencial. Promociones: usá solo las que figuran ahí; si no hay, no inventes descuentos ni cuotas (podés proponer beneficios que no bajen el precio, como asesoramiento o visita técnica sin cargo, marcados como propuesta). Ninguna idea, oportunidad ni acción puede ir contra lo que dice `evitar`. Lo que hace la competencia es inspiración, no un molde: si su camino choca con la hoja, adaptalo a la marca o descartalo y decí por qué."""


def _system_prompt(client: str, industry: str, market: str) -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(client=client, industry=industry or "sin industria definida", market=market or "mercado local")


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
    client_info: Any = None,
    labels: dict[str, str] | None = None,
) -> dict[str, Any]:
    from ..config import load_client

    ci = client_info or load_client()
    payload = {
        "client": {"name": ci.name, "industry": ci.industry, "market": ci.market, "description": ci.description},
        "client_brief": ci.brief or None,
        "coverage": metrics.get("coverage"),
        "labels": labels or {},
        "period": period,
        "metrics": metrics,
        "previous_period_metrics": previous_metrics or None,
        "concepts_top": concepts[:40],
        "creatives": _compact_items([it for it in items if it.get("tier") != "own"]),
        "our_creatives": _compact_items(own_items or [], limit=100),
    }
    content = [{"type": "text", "text": "Datos del mes (JSON):\n" + json.dumps(payload, ensure_ascii=False, default=str) + "\n\nProducí la síntesis estratégica."}]
    result = structured_call(
        SynthesisOutput, _system_prompt(ci.name, ci.industry, ci.market), content, model=model or settings.synth_model, max_tokens=32000, effort="high", stream=True, client=client
    )
    return result.model_dump()


def _most_common(values: list[Any]) -> str | None:
    vals = [str(v).strip() for v in values if v and str(v).strip()]
    return Counter(v.lower() for v in vals).most_common(1)[0][0] if vals else None


_FAMILY_DEFAULTS = {
    "proteccion": ("un imprevisto", "protección"),
    "economico": ("pagar de más", "ahorro"),
    "oferta": ("pagar de más", "un mejor precio"),
    "emocional": ("perder algo que querés", "tranquilidad"),
    "conveniencia": ("trámites eternos", "todo resuelto en minutos"),
    "lifestyle": ("que algo te frene", "seguir con tu vida"),
    "social_proof": ("no saber en quién confiar", "respaldo"),
}


def _mock_concepts(family: str, hook: str, items: list[dict[str, Any]], client: str, label: Any) -> list[ConceptIdea]:
    """Ideas listas para producir sin IA: arma gancho, guion y copy con lo que el mercado dice del territorio."""
    from ..analysis.metrics import is_heuristic

    ai_items = [it for it in items if it.get("tier") != "own" and it.get("cls") and not is_heuristic(it["cls"])]
    in_fam = [it for it in ai_items if it["cls"].get("angle_family") == family]
    pain_d, promise_d = _FAMILY_DEFAULTS.get(family, ("un imprevisto", "tranquilidad"))
    pain = _most_common([it["cls"].get("pain_point") for it in in_fam]) or pain_d
    promise = _most_common([it["cls"].get("promise") for it in in_fam]) or promise_d
    cta = label(_most_common([(it.get("cls") or {}).get("cta_type") for it in items]) or "mas_info")
    fam = label(family)
    hook_line = {
        "pregunta": f"¿Qué hacés si mañana te pasa esto: {pain}?",
        "problema": f"Esto es lo que pasa cuando aparece esto: {pain}. Y no estás preparado.",
        "estadistica": f"La mayoría no sabe qué hacer frente a esto: {pain}.",
        "storytelling": f"«Me pasó: {pain}. Lo resolví desde el celular.»",
        "testimonial": f"«Me pasó: {pain}. Lo resolví desde el celular.»",
    }.get(hook, f"¿Qué hacés si mañana te pasa esto: {pain}?")
    return [
        ConceptIdea(
            title=f"{fam} en primera persona",
            format="video",
            hook_type=hook,
            angle=family,
            hook_line=hook_line,
            script=[
                f"0–3 s · Gancho: una persona real a cámara dice «{hook_line}». Texto en pantalla con la misma frase.",
                f"3–8 s · El problema ({pain}): se ve en su día a día, sin dramatizar.",
                f"8–15 s · La salida: lo resuelve con {client} desde el celular; mostrar la pantalla real, 2 o 3 toques.",
                f"15–20 s · El beneficio: «{promise[:1].upper() + promise[1:]}», dicho por la misma persona.",
                f"20–25 s · Cierre: «{cta}» + logo de {client}.",
            ],
            copy_draft=f"{hook_line} Con {client} tenés {promise} sin vueltas: lo resolvés online, en minutos. {cta}.",
            cta=cta,
            visual_direction="Formato vertical 9:16, estilo contenido de usuario: luz natural, celular en mano, subtítulos grandes. Nada de banco de imágenes.",
        ),
        ConceptIdea(
            title=f"{fam} en 4 placas",
            format="carousel",
            hook_type="pregunta",
            angle=family,
            hook_line=f"¿Sabés qué hacer frente a esto: {pain}?",
            script=[
                f"Placa 1 · Gancho: «¿Sabés qué hacer frente a esto: {pain}?»",
                "Placa 2 · El problema: lo que cuesta resolverlo solo, en una frase con una cifra concreta del negocio.",
                f"Placa 3 · La solución: cómo lo resuelve {client}, en 3 pasos simples.",
                f"Placa 4 · Cierre: «{promise[:1].upper() + promise[1:]}» + «{cta}» + logo.",
            ],
            copy_draft=f"Nadie piensa en esto ({pain}) hasta que pasa. Deslizá y mirá cómo {client} te lo resuelve en 3 pasos. {cta}.",
            cta=cta,
            visual_direction=f"Fondo con los colores de {client}, una idea por placa, tipografía grande y un ícono por paso.",
        ),
    ]


def _of10(pct: float) -> int:
    return max(1, round(float(pct or 0) / 10)) if pct else 0


def mock_synthesis(
    metrics: dict[str, Any],
    items: list[dict[str, Any]],
    concepts: list[dict[str, Any]],
    period: str,
    label: Any = None,
    client_name: str | None = None,
) -> dict[str, Any]:
    """Síntesis determinística a partir de las métricas, sin IA (demo/tests). Redactada para el cliente."""
    from ..config import load_client, load_taxonomy

    label = label or load_taxonomy().label
    client = client_name or load_client().name
    totals = metrics.get("totals", {})
    n_comp = totals.get("competitors", 0) or 0
    n_cls = totals.get("classified", 0) or 0
    weighted = metrics.get("market_profile_weighted") or {}
    fam = weighted.get("angle_family_dist") or metrics.get("angle_family_dist") or []
    fmt = weighted.get("format_dist") or metrics.get("format_dist") or []
    sat = metrics.get("saturation") or {}
    sat_fam = (sat.get("angle_family") or [])
    sat_angle = (sat.get("angle") or [])
    top_fam = fam[0] if fam else {"value": None, "pct": 0}
    top_fmt = fmt[0] if fmt else {"value": None, "pct": 0}
    fam_users = next((d for d in sat_fam if d["value"] == top_fam["value"]), {"competitors": 0, "ads": 0})
    ws = (metrics.get("white_space") or {}).get("angle_families") or []
    ws_hooks = (metrics.get("white_space") or {}).get("hooks") or []
    profiles = metrics.get("competitor_profiles") or []
    longest = max(profiles, key=lambda p: p["longevity"]["max_days"], default=None)
    tiers = metrics.get("tier_profiles") or {}
    loc_top = ((tiers.get("local") or {}).get("top_family") or {}).get("value")
    glo_top = ((tiers.get("global") or {}).get("top_family") or {}).get("value")
    by_id = {it["id"]: it for it in items}
    low_data = n_cls < 30
    conf = "low" if low_data else "medium"

    summary = [
        SummaryPoint(
            headline=f"El mensaje que más se repite en el mercado: {label(top_fam['value'])}",
            detail=f"En promedio, {_of10(top_fam['pct'])} de cada 10 anuncios de cada competidor usan ese mensaje, y lo usan {fam_users['competitors']} de los {n_comp} competidores.",
            action=f"Si {client} va por ese territorio, necesita un giro propio; si no, hay espacio en los mensajes que casi nadie usa.",
        ),
        SummaryPoint(
            headline=f"El formato que más se repite es {label(top_fmt['value']).lower()}",
            detail=f"Representa {_of10(top_fmt['pct'])} de cada 10 anuncios por competidor en Meta y Google.",
            action="Probar al menos una pieza en un formato distinto al dominante para diferenciarnos en el feed.",
        ),
        SummaryPoint(
            headline=(f"{longest['name']} tiene el anuncio que más dura: {longest['longevity']['max_days']} días al aire" if longest else "Todavía no hay historial de permanencia"),
            detail="Es el que más tiempo lleva al aire entre los anuncios analizados.",
            action="Revisar los creativos de referencia y adaptar la idea, no copiarla.",
        ),
    ]
    insights = [
        Insight(
            title=f"{label(top_fam['value'])}: el territorio más concurrido",
            observation=f"{fam_users['competitors']} de los {n_comp} competidores usan el mensaje «{label(top_fam['value'])}» ({fam_users['ads']} anuncios).",
            evidence=[f"{fam_users['ads']} anuncios de {fam_users['competitors']} competidores"],
            implication_for_us="No ser uno más con el mismo mensaje: si lo usamos, con una prueba concreta o un tono distinto.",
        ),
    ]
    if loc_top and glo_top:
        insights.append(
            Insight(
                title="Locales y globales no hablan igual" if loc_top != glo_top else "Locales y globales dicen lo mismo",
                observation=(f"Los locales se apoyan más en «{label(loc_top)}»; los referentes globales, en «{label(glo_top)}»." if loc_top != glo_top
                             else f"Tanto los competidores locales como los globales se apoyan sobre todo en «{label(loc_top)}»."),
                evidence=[f"Local: {label(loc_top)} · Global: {label(glo_top)}"],
                implication_for_us="Tomar de los globales lo que todavía nadie hace localmente.",
            )
        )
    if ws:
        insights.append(
            Insight(
                title="Mensajes que casi nadie usa",
                observation="Casi nadie habla de " + ", ".join(label(w["value"]).lower() for w in ws[:3]) + ".",
                evidence=[f"{label(w['value'])}: {round(w['pct'])}% de los anuncios" for w in ws[:3]],
                implication_for_us="Probar una pieza en el territorio libre más afín a la marca.",
            )
        )

    opportunities = []
    for w in ws[:3]:
        users = next((d for d in sat_fam if d["value"] == w["value"]), None)
        hook = ws_hooks[0]["value"] if ws_hooks else "pregunta"
        opportunities.append(
            Opportunity(
                title=f"Ocupar el territorio de {label(w['value']).lower()}",
                priority="alta" if w["pct"] < 2 else "media",
                rationale=(f"Solo {users['ads']} de los {n_cls} anuncios del mercado usan este mensaje" if users else "Ningún anuncio del mercado usa este mensaje") + f": hay espacio para que {client} lo haga propio.",
                who_does_it=(f"Lo usan {users['competitors']} de {n_comp} competidores ({', '.join(users['names'][:3])})" if users else "Ningún competidor lo usa hoy"),
                evidence=[f"{users['ads'] if users else 0} de {n_cls} anuncios clasificados"],
                concepts=_mock_concepts(w["value"], hook, items, client, label),
                how_to_measure="Comparar tasa de clics y costo por cotización contra el anuncio actual más usado, durante 14 días.",
            )
        )
    top_sat = [d for d in sat_angle if n_comp and d["competitors"] >= max(2, round(n_comp * 0.5))][:3]
    risks = [
        Risk(
            title=f"Ser uno más hablando de {label(d['value']).lower()}",
            severity="alta" if d["competitors"] >= round(n_comp * 0.7) else "media",
            description=f"{d['competitors']} de los {n_comp} competidores usan este mismo mensaje ({d['ads']} anuncios).",
            why_it_matters="Con tantos anuncios diciendo lo mismo, el nuestro se confunde con el resto y cuesta más que lo recuerden.",
            mitigation="Si lo usamos, cuantificarlo (cifras, casos reales) o contarlo con un formato que nadie usa.",
            evidence=[f"{d['ads']} anuncios de {d['competitors']} competidores"],
        )
        for d in top_sat
    ] or [
        Risk(
            title="Datos todavía parciales",
            severity="media",
            description="La clasificación de este mes es preliminar.",
            why_it_matters="Las conclusiones pueden cambiar con la clasificación completa.",
            mitigation="Validar con la clasificación con IA antes de producir.",
            evidence=[f"{n_cls} anuncios clasificados"],
        )
    ]
    refs = []
    for i in (metrics.get("longevity") or {}).get("top_ids", [])[:3]:
        if i in by_id:
            refs.append(
                ReferenceCreative(
                    creative_id=i,
                    competitor=by_id[i]["competitor"],
                    why_relevant=f"Lleva {by_id[i]['days_active']} días al aire: podría indicar que al anunciante le sirve.",
                    what_not_to_copy="El titular literal y la estética del competidor.",
                    adaptation_ideas=["La misma tensión, contada con un caso real de un cliente", "Versión corta de 15 segundos para Reels/Stories"],
                ).model_dump()
            )
    out = SynthesisOutput(
        period_label=period,
        executive_summary=summary,
        top_insights=insights[:3],
        local_vs_global=(
            (f"Los competidores locales se apoyan en «{label(loc_top)}» y los globales en «{label(glo_top)}»." if loc_top != glo_top
             else f"Locales y globales coinciden: el mensaje principal de ambos es «{label(loc_top)}».")
            if loc_top and glo_top else "Sin datos suficientes para comparar locales y globales."
        ),
        reference_creatives=refs,
        unexploited_angles=[label(w["value"]) for w in ws],
        white_space=opportunities,
        recommendations=[],
        risks=risks,
        ours_vs_market=("Comparación disponible" if (metrics.get("ours_vs_market") or {}).get("has_own") else None),
        action_items=[
            ActionItem(title="Validar el plan con el cliente", description="Revisar hallazgos, oportunidades y riesgos; aprobar o ajustar.", priority="alta", owner_role="Director/a de cuenta", due_in_days=2, deliverable="Plan aprobado en Basecamp", success_metric="Aprobación antes del día 3"),
            ActionItem(title="Producir las piezas de la oportunidad principal", description="Una imagen, un video corto UGC y un carrusel.", priority="alta", owner_role="Líder creativo", due_in_days=7, deliverable="3 piezas listas para pautar", success_metric="Piezas al aire en 7 días"),
            ActionItem(title="Lanzar prueba A/B contra el anuncio actual", description="Mismo presupuesto y audiencia, 14 días.", priority="media", owner_role="Media buyer", due_in_days=10, deliverable="Campaña de prueba activa", success_metric="Costo por cotización vs. control"),
            ActionItem(title="Revisar resultados y decidir", description="Escalar la ganadora, cortar la perdedora.", priority="media", owner_role="Director/a de cuenta", due_in_days=24, deliverable="Informe de resultados", success_metric="Decisión documentada"),
        ],
    )
    return out.model_dump()
