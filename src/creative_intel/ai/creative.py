"""Ideas y guiones con Gemini (el análisis de datos sigue con el modelo del plan).

Se activa con GEMINI_API_KEY en .env. Recibe los hechos ya calculados (no analiza de nuevo) y devuelve ideas
con el mismo formato que el resto del informe. Si Gemini falla, quedan las ideas del modelo del plan.
"""
from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import Any

from pydantic import BaseModel, Field

from ..config import settings
from .client import usage

log = logging.getLogger(__name__)

WRITER_PROMPT = """Sos redactor/a creativo/a senior de una agencia de Montevideo. Escribís ideas de anuncios para {client} ({industry}) que se van a producir tal cual.

Cómo escribís:
- Como habla la gente en Uruguay: voseo natural, frases cortas, palabras de todos los días. Nada de tono de folleto ni de asistente.
- Cada idea parte de una situación concreta y creíble (quién, dónde, qué pasa) y de un producto concreto de {client}.
- El gancho es algo que se VE o se DICE en los primeros 3 segundos, no un eslogan.
- Usá los detalles reales que te paso (productos, materiales, medidas, plazos, ofertas de la hoja o de lo que ya hizo la marca). Nunca inventes precios, descuentos, cuotas ni plazos.
- Los anuncios propios que mejor funcionaron (con sus resultados) marcan el registro: tomá su forma de hablar, no los copies.
- Guion escena por escena con tiempos; en cada escena separá lo que se ve de lo que se escucha o se lee ("Se ve: ... / Se escucha: ...").
- Copy listo para publicar como lo escribiría el community manager de la marca: 1 a 3 líneas, con el dato concreto.

Prohibido (suena a texto generado):
- Verbos de manual: descubrí, transformá, elevá, potenciá, sumergite, desbloqueá, viví la experiencia, llevá tu ___ al siguiente nivel.
- Fórmulas: "no es solo X, es Y", "¿Sabías que...?", "Imaginá...", "¿Buscás...?" para abrir, "porque vos te lo merecés", "tu mejor versión".
- Adjetivos vacíos: increíble, espectacular, único, ideal, perfecto, premium, de calidad, inigualable.
- Raya larga (—), rimas, emojis en fila, más de un signo de exclamación, mayúsculas para gritar.
- Corchetes o textos para completar.
{brief_rules}"""


class _Idea(BaseModel):
    for_what: str = Field(description="Para qué pedido es (título de la oportunidad o momento del calendario), copiado tal cual")
    title: str = Field(description="Nombre corto de la idea (máx. 6 palabras)")
    format: str = Field(description="video | carrusel | imagen | historia")
    hook_type: str
    angle: str
    hook_line: str = Field(description="Lo que se ve o se dice en los primeros 3 segundos, textual")
    script: list[str] = Field(description="4 a 6 escenas con tiempos: '0–3 s · Se ve: ... / Se escucha: ...'")
    copy_draft: str = Field(description="Texto del anuncio listo para publicar, 1 a 3 líneas")
    cta: str
    visual_direction: str = Field(description="Escena, casting, luz, encuadre; concreto")


class _Ideas(BaseModel):
    ideas: list[_Idea]


_CLIENT = None


def _client():
    """Un solo cliente de Gemini para toda la corrida (si se crea y se descarta, la librería cierra la conexión
    antes de mandar el pedido: 'Cannot send a request, as the client has been closed')."""
    global _CLIENT
    if _CLIENT is None:
        from google import genai

        _CLIENT = genai.Client(api_key=gemini_key())
    return _CLIENT


def gemini_key() -> str:
    """La clave tal cual o, si se pegó con algo más alrededor (comillas, una etiqueta), solo la clave."""
    import re

    raw = (settings.gemini_api_key or "").strip().strip("'\"")
    m = re.search(r"AIza[0-9A-Za-z_\-]{30,}", raw)
    return m.group(0) if m else raw


def enabled() -> bool:
    return settings.creative_provider == "gemini" and bool(settings.gemini_api_key)


def _brief_rules(brief: dict[str, Any] | None) -> str:
    if not brief:
        return ""
    lines = ["\nHoja del cliente (es ley):"]
    for k, v in brief.items():
        lines.append(f"- {k}: {', '.join(v) if isinstance(v, list) else v}")
    return "\n".join(lines)


def write_ideas(client_info: Any, context: dict[str, Any], request: str) -> list[dict[str, Any]] | None:
    """Pide las ideas a Gemini. Devuelve None si no está configurado o si falla (se usan las del plan)."""
    if not enabled():
        return None
    try:
        from google.genai import types
    except ImportError:
        log.warning("Falta google-genai (pip install -e .): las ideas quedan con el modelo del plan")
        return None
    system = WRITER_PROMPT.format(client=client_info.name, industry=client_info.industry or client_info.description or "",
                                  brief_rules=_brief_rules(client_info.brief))
    prompt = f"Datos (JSON):\n{json.dumps(context, ensure_ascii=False, default=str)}\n\nPedido: {request}"
    try:
        resp = _client().models.generate_content(
            model=settings.gemini_creative_model,
            contents=prompt,
            config=types.GenerateContentConfig(system_instruction=system, response_mime_type="application/json",
                                               response_schema=_Ideas, temperature=1.0, max_output_tokens=16000),
        )
    except Exception as exc:
        log.warning("Gemini no respondió (%s): las ideas quedan con el modelo del plan", str(exc)[:300])
        return None
    meta = getattr(resp, "usage_metadata", None)
    if meta is not None:
        usage.add(settings.gemini_creative_model, SimpleNamespace(
            input_tokens=meta.prompt_token_count or 0,
            output_tokens=(meta.candidates_token_count or 0) + (meta.thoughts_token_count or 0)))
    parsed = resp.parsed if isinstance(getattr(resp, "parsed", None), _Ideas) else None
    if parsed is None:
        try:
            parsed = _Ideas.model_validate_json(resp.text or "")
        except Exception as exc:
            log.warning("Gemini devolvió algo que no se pudo leer (%s)", str(exc)[:200])
            return None
    return [i.model_dump() for i in parsed.ideas]


def strip_for(idea: dict[str, Any]) -> dict[str, Any]:
    """Una idea de Gemini con el formato de ConceptIdea (sin el campo for_what)."""
    return {k: v for k, v in idea.items() if k != "for_what"}


def check_gemini() -> str:
    """Prueba mínima de la clave de Gemini (una fracción de centavo). Devuelve '' si anda o el problema."""
    if not settings.gemini_api_key:
        return "No hay GEMINI_API_KEY en .env"
    try:
        resp = _client().models.generate_content(model=settings.gemini_creative_model, contents="Respondé solo: ok")
        return "" if (resp.text or "").strip() else "Gemini respondió vacío"
    except ImportError:
        return "Falta instalar google-genai (pip install -e .)"
    except Exception as exc:
        return str(exc)[:300]
