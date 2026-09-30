"""Cliente Anthropic compartido y helpers para pedidos con salida estructurada."""
from __future__ import annotations

import json
from typing import Any, Type, TypeVar

import anthropic
from pydantic import BaseModel

from ..config import settings

T = TypeVar("T", bound=BaseModel)
FALLBACK_BETA = "server-side-fallback-2026-07-01"

_client: anthropic.Anthropic | None = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()  # ANTHROPIC_API_KEY / perfil `ant auth login`
    return _client


def _fallback_kwargs() -> dict[str, Any]:
    if settings.anthropic_fallbacks == "default":
        return {"betas": [FALLBACK_BETA], "fallbacks": "default"}
    return {}


class RefusedError(RuntimeError):
    pass


def structured_call(
    schema_model: Type[T],
    system: str,
    content: list[dict[str, Any]] | str,
    model: str,
    max_tokens: int = 4000,
    effort: str = "medium",
    stream: bool = False,
    client: anthropic.Anthropic | None = None,
) -> T:
    """Una llamada con `output_config.format` (JSON garantizado) validada contra un modelo Pydantic.

    El system prompt es estable y se cachea (cache_control); lo variable va en `content`.
    Con ANTHROPIC_FALLBACKS=default, un rechazo de seguridad se re-ejecuta server-side en
    otro modelo dentro del mismo pedido.
    """
    client = client or get_client()
    kwargs: dict[str, Any] = dict(
        model=model,
        max_tokens=max_tokens,
        system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": content}],
        output_config={"format": {"type": "json_schema", "schema": schema_model.model_json_schema()}, "effort": effort},
        **_fallback_kwargs(),
    )
    if stream:
        with client.beta.messages.stream(**kwargs) as s:
            response = s.get_final_message()
    else:
        response = client.beta.messages.create(**kwargs)
    if response.stop_reason == "refusal":
        details = getattr(response, "stop_details", None)
        raise RefusedError(f"El modelo rechazó el pedido: {getattr(details, 'category', None)} {getattr(details, 'explanation', '')}")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Respuesta truncada (max_tokens); subir el límite")
    text = next(b.text for b in response.content if b.type == "text")
    return schema_model.model_validate(json.loads(text))
