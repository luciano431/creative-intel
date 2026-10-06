"""Etapa 1 — Clasificación de un creativo dentro de la taxonomía cerrada.

Cada creativo -> una llamada -> JSON validado. Se envía la imagen (thumbnail/screenshot)
cuando existe, así la clasificación es visual + textual. Para video se usa el frame/thumbnail
y el copy; la transcripción de audio es una extensión (ver README).
"""
from __future__ import annotations

import base64
import mimetypes
import re
from pathlib import Path
from typing import Any, Literal, Type

from pydantic import BaseModel, ConfigDict, Field, create_model

from ..config import Taxonomy, settings
from .client import structured_call

SYSTEM_PROMPT = """Sos un analista senior de publicidad ({industry}, {market}).
Clasificás UN anuncio de un competidor dentro de una taxonomía cerrada. Reglas:
- Usá solo los valores permitidos por el esquema; si algo no aplica devolvé null donde el esquema lo permita.
- Distinguí siempre dato observable (lo que el anuncio dice y muestra) de inferencia (audiencia, etapa del funnel). Marcá la inferencia bajando `confidence` cuando el material sea pobre.
- `hook_type` es el mecanismo de los primeros 2 segundos / primera línea, no el tema.
- `primary_angle` es el argumento de venta dominante; `secondary_angle` solo si hay uno claro.
- `pain_point` y `promise` en 2-6 palabras, en español.
- `offer`: cualquier promoción concreta que comunique el anuncio (descuento, % off, cuotas sin recargo, meses bonificados, regalo, sorteo, precio especial), aunque el mensaje principal sea otro. Copiala tal cual en pocas palabras ("20% off el primer año"). Si no hay promoción, null. "Cotizá gratis" no es una promoción.
- `key_claims`: afirmaciones concretas y verificables del anuncio (precios, plazos, % , "sin franquicia"), máximo 5.
- `summary`: una oración neutral que describa el anuncio sin juzgar su rendimiento.
- Si el texto del anuncio está dentro de la imagen (anuncios de búsqueda archivados como imagen, piezas gráficas), leelo de la imagen y usalo como si fuera el copy.
- No inventes engagement ni rendimiento; no están en tu alcance."""


def _classify_prompt() -> str:
    from ..config import load_client

    ci = load_client()
    return SYSTEM_PROMPT.format(industry=ci.industry or "publicidad digital", market=ci.market or "LATAM")


def build_classification_model(taxonomy: Taxonomy) -> Type[BaseModel]:
    """Modelo Pydantic dinámico: los enums salen de config/taxonomy.yaml."""

    def lit(values: list[str]):
        return Literal[tuple(values)]  # type: ignore[valid-type]

    fields: dict[str, Any] = {
        "product": (lit(taxonomy.products), Field(description="Producto o línea principal que promociona")),
        "format_detected": (lit(taxonomy.formats), Field(description="Formato real del creativo")),
        "placement_guess": (lit(taxonomy.placements), Field(description="Ubicación probable")),
        "hook_type": (lit(taxonomy.hook_types), Field(description="Mecanismo de apertura")),
        "primary_angle": (lit(taxonomy.angles), Field(description="Ángulo de venta dominante")),
        "secondary_angle": (lit(taxonomy.angles) | None, Field(description="Ángulo secundario o null")),
        "pain_point": (str, Field(description="Problema que ataca, 2-6 palabras")),
        "promise": (str, Field(description="Promesa central, 2-6 palabras")),
        "offer": (str | None, Field(description="Oferta concreta (descuento, cuotas, beneficio) o null")),
        "funnel_stage": (lit(taxonomy.funnel_stages), Field(description="tofu | mofu | bofu")),
        "cta_type": (lit(taxonomy.cta_types), Field(description="Tipo de llamada a la acción")),
        "creative_style": (lit(taxonomy.creative_styles), Field(description="Estilo de producción")),
        "audience_hypothesis": (str, Field(description="Audiencia probable (inferencia)")),
        "tone": (str, Field(description="Tono en 1-3 palabras")),
        "key_claims": (list[str], Field(description="Claims concretos, máx 5")),
        "summary": (str, Field(description="Una oración descriptiva")),
        "confidence": (lit(taxonomy.confidence_levels), Field(description="Confianza global de la clasificación")),
    }
    return create_model("CreativeClassification", __config__=ConfigDict(extra="forbid"), **fields)  # type: ignore[call-overload]


def _image_block(path: str) -> dict[str, Any] | None:
    p = Path(path)
    if not p.is_file():
        return None
    mime = mimetypes.guess_type(p.name)[0] or ""
    if mime not in ("image/jpeg", "image/png", "image/gif", "image/webp"):
        return None
    if p.stat().st_size > 4_500_000:
        return None
    return {"type": "image", "source": {"type": "base64", "media_type": mime, "data": base64.standard_b64encode(p.read_bytes()).decode()}}


def creative_prompt(creative: Any) -> str:
    return (
        f"Competidor: {creative.competitor.name} ({creative.competitor.tier}, {creative.competitor.country})\n"
        f"Plataforma: {creative.platform} · Fuente: {creative.source} · Formato declarado: {creative.format}\n"
        f"Placements observados: {', '.join(creative.placements or []) or 'desconocido'}\n"
        f"Activo desde: {creative.first_seen} · Última vez visto: {creative.last_seen} · Días activo: {creative.days_active}\n"
        f"Países: {', '.join(creative.countries or [])}\n"
        f"Titular: {creative.headline or '-'}\n"
        f"Texto principal:\n{creative.body_text or '-'}\n"
        f"Descripción: {creative.description or '-'}\n"
        f"CTA visible: {creative.cta_text or '-'}\n"
        f"Landing: {creative.landing_url or '-'}\n"
        f"Productos que vende este competidor: {', '.join(creative.competitor.products or [])}\n\n"
        "Clasificá este anuncio."
    )


def classify_creative(creative: Any, taxonomy: Taxonomy, model: str | None = None, client=None) -> dict[str, Any]:
    schema = build_classification_model(taxonomy)
    content: list[dict[str, Any]] = []
    if creative.thumbnail_path:
        img = _image_block(creative.thumbnail_path)
        if img:
            content.append(img)
    shot = (creative.raw or {}).get("screenshot")
    if shot and not content:
        img = _image_block(shot)
        if img:
            content.append(img)
    frames_note = ""
    if (creative.format or "") == "video":
        from ..media.video import FRAME_AT, extract_frames, video_url

        frames = extract_frames(creative.id, video_url(creative.media_urls))
        for f in frames:
            img = _image_block(str(f))
            if img:
                content.append(img)
        if frames:
            frames_note = ("\nLas imágenes después de la portada son cuadros del video en los segundos "
                           + ", ".join(str(t) for t in FRAME_AT[: len(frames)])
                           + ": usalos para el gancho (qué se ve y se lee en los primeros 3 segundos).")
    content.append({"type": "text", "text": creative_prompt(creative) + frames_note})
    result = structured_call(
        schema, _classify_prompt(), content, model=model or settings.classify_model, max_tokens=2000, effort="low", client=client
    )
    data = result.model_dump()
    data["angle_family"] = taxonomy.family_of(data["primary_angle"])
    return data


# ----------------------------------------------------------------------------- mock / fallback
_ANGLE_KEYWORDS: list[tuple[str, str]] = [
    ("robo", "robo"), ("roban", "robo"), ("choque", "accidente"), ("accidente", "accidente"),
    ("rotura", "danio"), ("daño", "danio"), ("pantalla", "danio"), ("imprevisto", "imprevistos"),
    ("descuento", "descuento"), ("% off", "descuento"), ("promo", "promocion"), ("cuotas", "cuotas"),
    ("desde $", "precio"), ("desde us$", "precio"), ("precio", "precio"), ("ahorr", "ahorro"),
    ("tranquil", "tranquilidad"), ("familia", "familia"), ("hijos", "familia"), ("protegé", "seguridad"), ("protege", "seguridad"),
    ("online", "contratacion_online"), ("en minutos", "rapidez"), ("rápido", "rapidez"), ("fácil", "facilidad"),
    ("app", "digital"), ("asistencia", "asistencia"), ("viaj", "viajes"), ("moto", "movilidad"),
    ("clientes", "cantidad_clientes"), ("años", "trayectoria"), ("confianza", "confianza"),
]
_PRODUCT_KEYWORDS = [("celular", "celular"), ("teléfono", "celular"), ("auto", "auto"), ("vehículo", "auto"),
                     ("moto", "moto"), ("hogar", "hogar"), ("casa", "hogar"), ("vida", "vida"), ("salud", "salud"),
                     ("viaje", "viaje"), ("mascota", "mascotas"), ("empresa", "empresa"), ("pyme", "empresa")]


def _heuristic_offer(creative: Any) -> str | None:
    from ..analysis.metrics import promo_of

    return promo_of({"headline": creative.headline, "body_text": " ".join(t for t in (creative.body_text, creative.description) if t)})


def heuristic_classify(creative: Any, taxonomy: Taxonomy) -> dict[str, Any]:
    """Clasificador por reglas: para demo/tests y como fallback si la IA no está disponible."""
    text = " ".join([creative.headline or "", creative.body_text or "", creative.description or "", creative.cta_text or ""]).lower()
    # Primero, palabras de la propia taxonomía del cliente ("juegos_infantiles" -> "juegos infantiles");
    # después, las palabras de seguros; si nada coincide, un valor que exista en esta taxonomía.
    own_angles = [(a.replace("_", " "), a) for a in taxonomy.angles]
    own_products = [(x.replace("_", " "), x) for x in taxonomy.products if x not in ("multiproducto", "marca")]
    angle = next((a for k, a in own_angles + _ANGLE_KEYWORDS if k in text and a in taxonomy.angles), None)
    angle = angle or ("seguridad" if "seguridad" in taxonomy.angles else (taxonomy.angle_families.get("emocional") or taxonomy.angles)[0])
    product = next((x for k, x in own_products + _PRODUCT_KEYWORDS if k in text and x in taxonomy.products), None)
    product = product or ("multiproducto" if "multiproducto" in taxonomy.products else taxonomy.products[-1])
    first_line = (creative.headline or creative.body_text or "").strip().split("\n")[0]
    if "?" in first_line or "¿" in first_line:
        hook = "pregunta"
    elif re.search(r"\d+\s?%", first_line):
        hook = "estadistica" if "clientes" in first_line.lower() else "oferta"
    elif "$" in first_line or "desde" in first_line.lower():
        hook = "precio"
    elif any(w in text for w in ("imaginá", "imagina", "historia", "un día")):
        hook = "storytelling"
    elif any(w in text for w in ("qué pasaría", "te roban", "sin seguro")):
        hook = "problema"
    elif "cotiz" in text:
        hook = "direct_response"
    else:
        hook = "branding"
    fam = taxonomy.family_of(angle)
    funnel = "bofu" if any(w in text for w in ("cotiz", "contrat", "desde $", "%")) else "mofu" if fam in ("proteccion", "economico", "conveniencia") else "tofu"
    cta = "cotizar" if "cotiz" in text else "contratar" if "contrat" in text else "descargar_app" if "app" in text else "mas_info" if text else "sin_cta"
    fmt = creative.format if creative.format in taxonomy.formats else "desconocido"
    return {
        "product": product,
        "format_detected": fmt,
        "placement_guess": (creative.placements or ["desconocido"])[0] if (creative.placements or ["desconocido"])[0] in taxonomy.placements else "desconocido",
        "hook_type": hook,
        "primary_angle": angle,
        "secondary_angle": None,
        "pain_point": angle.replace("_", " "),
        "promise": "protección" if fam == "proteccion" else "ahorro" if fam == "economico" else "tranquilidad",
        "offer": _heuristic_offer(creative),
        "funnel_stage": funnel,
        "cta_type": cta,
        "creative_style": "ugc" if "ugc" in text else "produccion" if fmt == "video" else "texto_plano" if fmt == "texto" else "foto_stock",
        "audience_hypothesis": "adultos urbanos con producto asegurable",
        "tone": "directo",
        "key_claims": [],
        "summary": f"Anuncio de {product} con ángulo {angle} y hook {hook} (clasificación heurística).",
        "confidence": "low",
        "angle_family": fam,
    }
