"""Investigación web para las ideas que van más allá del anuncio (OpenAI con búsqueda web).

Con los huecos del cliente ya calculados, busca afuera: empresas del mismo rubro en otros países que
resolvieron eso (canales, alianzas, productos, campañas), datos públicos del país que sirvan para
contenido, y prensa del mercado. Cada hallazgo trae su link: sin link no entra.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import Field

from ..config import settings
from .client import structured_call
from .synthesize import _Strict

log = logging.getLogger(__name__)


class Finding(_Strict):
    kind: str = Field(description="caso | dato_publico | prensa | anuncio")
    who: str = Field(description="Empresa u organismo y país (ej. 'Laka · Europa', 'Ministerio del Interior · Uruguay')")
    fact: str = Field(description="El hecho concreto, con números si los hay, en 1-2 oraciones. Sin adjetivos")
    relevance: str = Field(description="Qué hueco del cliente ayuda a resolver y cómo, en 1 oración")
    url: str = Field(description="Link exacto de la fuente consultada")
    date: str = Field(description="Fecha de la fuente (AAAA-MM o AAAA) o '' si no figura")


class Research(_Strict):
    findings: list[Finding] = Field(description="4 a 10 hallazgos verificados")


RESEARCH_PROMPT = """Sos investigador/a de estrategia en una agencia de Montevideo. Buscás en la web casos y datos reales que
sirvan para el plan de {client} ({industry}, {market}).

Qué buscar (usá la búsqueda web):
1. Empresas del mismo rubro en otros países que resolvieron los huecos del cliente: canales de venta (alianzas, puntos de venta, apps),
   productos (por uso, por días, para un segmento), formatos de campaña o comunidad. Con números si los publicaron.
2. Datos públicos u oficiales del país del cliente que sirvan para contenido propio (datos abiertos, estadísticas, informes).
3. Notas de prensa recientes del mercado local del cliente que muestren una oportunidad.

Reglas: solo hallazgos con link que hayas consultado; nada inventado ni supuesto; preferí 2025-2026; castellano;
cada hallazgo tiene que poder usarse para una idea concreta del cliente."""


def research(client_info: Any, gaps: list[dict[str, Any]], audit: dict[str, Any], competitors: list[str]) -> list[dict[str, Any]]:
    """Hallazgos con link. Vacío si el proveedor no es OpenAI o si falla (el plan se arma igual)."""
    if settings.ai_provider != "openai":
        return []
    payload = {
        "cliente": {"nombre": client_info.name, "rubro": client_info.industry, "descripcion": client_info.description,
                    "mercado": client_info.market, "hoja": client_info.brief or None},
        "lo_que_ya_hace": [a.get("angle") for a in (audit or {}).get("angles", [])][:12],
        "a_quien_le_habla": (audit or {}).get("profile", {}).get("talks_to"),
        "huecos_de_ejecucion": gaps,
        "competidores_monitoreados": competitors,
    }
    content = [{"type": "text", "text": "Contexto (JSON):\n" + json.dumps(payload, ensure_ascii=False, default=str)
                + "\n\nBuscá y devolvé los hallazgos."}]
    system = RESEARCH_PROMPT.format(client=client_info.name, industry=client_info.industry or "", market=client_info.market or "")
    try:
        out = structured_call(Research, system, content, model=settings.synth_model, max_tokens=8000, effort="medium",
                              tools=[{"type": "web_search"}])
    except Exception as exc:
        log.warning("investigación web no disponible: %s", str(exc)[:300])
        return []
    return [f.model_dump() for f in out.findings if f.url.startswith("http")]
