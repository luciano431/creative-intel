"""Clientes de IA (OpenAI o Anthropic, según AI_PROVIDER) y pedidos con salida estructurada."""
from __future__ import annotations

import json
from typing import Any, Type, TypeVar

import anthropic
from pydantic import BaseModel

from ..config import settings

T = TypeVar("T", bound=BaseModel)
FALLBACK_BETA = "server-side-fallback-2026-07-01"

_client: anthropic.Anthropic | None = None
_openai_client: Any = None


def get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic()  # ANTHROPIC_API_KEY / perfil `ant auth login`
    return _client


def get_openai_client() -> Any:
    global _openai_client
    if _openai_client is None:
        from openai import OpenAI

        _openai_client = OpenAI(timeout=900, max_retries=3)  # OPENAI_API_KEY
    return _openai_client


def _fallback_kwargs() -> dict[str, Any]:
    if settings.anthropic_fallbacks == "default":
        return {"betas": [FALLBACK_BETA], "fallbacks": "default"}
    return {}


class RefusedError(RuntimeError):
    pass


class BudgetExceeded(RuntimeError):
    pass


class ApiUnavailable(RuntimeError):
    """Sin crédito o sin clave válida: fallaría cada pedido, así que se avisa y se frena."""


def api_problem(exc: Exception) -> str | None:
    """Explicación en castellano si el error es de crédito o de clave (se repetiría en cada pedido)."""
    msg = str(exc).lower()
    if "credit balance is too low" in msg:
        return ("La cuenta de Claude no tiene crédito disponible. Revisá en console.anthropic.com → Settings → Billing "
                "que el saldo esté cargado en la MISMA organización de la clave que está en .env (si recién pagaste, esperá unos minutos).")
    if "insufficient_quota" in msg or "exceeded your current quota" in msg:
        return ("La cuenta de OpenAI no tiene saldo. Cargalo en platform.openai.com → Settings → Billing, en el MISMO proyecto "
                "y organización de la clave que está en .env (el plan de ChatGPT no sirve para la API).")
    if any(k in msg for k in ("could not resolve authentication", "invalid x-api-key", "authentication_error",
                              "incorrect api key", "invalid_api_key", "api_key client option must be set")):
        where = "platform.openai.com → API keys" if settings.ai_provider == "openai" else "console.anthropic.com → API Keys"
        var = "OPENAI_API_KEY" if settings.ai_provider == "openai" else "ANTHROPIC_API_KEY"
        return f"La clave de API ({var} en .env) no es válida o está vacía. Creá una nueva en {where} y cargala en .env."
    if "permission_error" in msg or "model_not_found" in msg or "does not have access to model" in msg:
        return "La clave de API no tiene acceso a este modelo. Revisá la clave, el proyecto y el modelo configurado."
    return None


def check_api(model: str | None = None) -> None:
    """Pedido mínimo (una fracción de centavo) para confirmar clave y saldo antes de una corrida larga."""
    try:
        if settings.ai_provider == "openai":
            get_openai_client().responses.create(model=model or settings.classify_model, input="Respondé solo: ok", max_output_tokens=16,
                                                 reasoning={"effort": "low"})
        else:
            get_client().messages.create(model=model or settings.classify_model, max_tokens=1, messages=[{"role": "user", "content": "ok"}])
    except Exception as exc:
        problem = api_problem(exc)
        if problem:
            raise ApiUnavailable(problem) from exc
        raise


# US$ por millón de tokens: (entrada, salida, lectura de caché, escritura de caché)
PRICES = {
    "claude-opus-5-5": (4.0, 20.0, 0.20, 5.0),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20, 2.5),
    "claude-haiku-4-5": (1.0, 5.0, 0.10, 1.25),
    # OpenAI (precios públicos a oct-2026; la caché de OpenAI no cobra escritura)
    "gpt-5.4-mini": (0.75, 4.50, 0.075, 0.0),
    "gpt-5.4-nano": (0.20, 1.25, 0.02, 0.0),
    "gpt-5.4": (2.50, 15.0, 0.25, 0.0),
    "gpt-5-mini": (0.25, 2.0, 0.025, 0.0),
    "gpt-5": (1.25, 10.0, 0.125, 0.0),
}


class UsageTracker:
    """Suma los tokens de cada llamada para mostrar cuánto costó la corrida."""

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.calls = 0
        self.usd = 0.0
        self.by_model: dict[str, dict[str, int]] = {}

    def add(self, model: str, usage: Any) -> float:
        get = lambda k: int(getattr(usage, k, 0) or 0)  # noqa: E731
        tok = {"input": get("input_tokens"), "output": get("output_tokens"),
               "cache_read": get("cache_read_input_tokens"), "cache_write": get("cache_creation_input_tokens")}
        details = getattr(usage, "input_tokens_details", None)  # OpenAI: input_tokens ya incluye los cacheados
        cached = int(getattr(details, "cached_tokens", 0) or 0)
        if cached:
            tok["input"], tok["cache_read"] = max(tok["input"] - cached, 0), cached
        # el prefijo más largo gana (gpt-5.4-mini antes que gpt-5.4 y gpt-5)
        price = next((PRICES[k] for k in sorted(PRICES, key=len, reverse=True) if model.startswith(k)), PRICES["claude-opus-5-5"])
        cost = (tok["input"] * price[0] + tok["output"] * price[1] + tok["cache_read"] * price[2] + tok["cache_write"] * price[3]) / 1e6
        agg = self.by_model.setdefault(model, {"calls": 0, "input": 0, "output": 0})
        agg["calls"] += 1
        agg["input"] += tok["input"] + tok["cache_read"] + tok["cache_write"]
        agg["output"] += tok["output"]
        self.calls += 1
        self.usd += cost
        return cost

    def summary(self) -> str:
        if not self.calls:
            return "Sin llamadas a la IA."
        parts = [f"{m}: {a['calls']} llamadas, {a['input']:,} tokens de entrada, {a['output']:,} de salida".replace(",", ".")
                 for m, a in self.by_model.items()]
        return f"Gasto de IA de esta corrida: ≈ US$ {self.usd:.2f}  ({'; '.join(parts)})"


usage = UsageTracker()


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
    if settings.ai_provider == "openai" and client is None:
        return _openai_structured_call(schema_model, system, content, model, max_tokens, effort)
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
    usage.add(getattr(response, "model", model) or model, getattr(response, "usage", None))
    if response.stop_reason == "refusal":
        details = getattr(response, "stop_details", None)
        raise RefusedError(f"El modelo rechazó el pedido: {getattr(details, 'category', None)} {getattr(details, 'explanation', '')}")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Respuesta truncada (max_tokens); subir el límite")
    text = next(b.text for b in response.content if b.type == "text")
    return schema_model.model_validate(json.loads(text))


def _openai_content(content: list[dict[str, Any]] | str) -> list[dict[str, Any]]:
    """Bloques en formato Anthropic (texto / imagen base64) → formato de la Responses API de OpenAI."""
    if isinstance(content, str):
        return [{"type": "input_text", "text": content}]
    out: list[dict[str, Any]] = []
    for block in content:
        if block.get("type") == "image":
            src = block["source"]
            out.append({"type": "input_image", "image_url": f"data:{src['media_type']};base64,{src['data']}", "detail": "low"})
        elif block.get("type") == "text":
            out.append({"type": "input_text", "text": block["text"]})
    return out


def _openai_structured_call(schema_model: Type[T], system: str, content: list[dict[str, Any]] | str, model: str,
                            max_tokens: int, effort: str) -> T:
    """Responses API con salida estructurada estricta (el SDK arma el JSON schema desde el modelo Pydantic)."""
    from pydantic import ValidationError

    try:
        response = get_openai_client().responses.parse(
            model=model,
            instructions=system,
            input=[{"role": "user", "content": _openai_content(content)}],
            text_format=schema_model,
            reasoning={"effort": effort},
            # en OpenAI el razonamiento cuenta dentro del máximo de salida: se deja margen
            max_output_tokens=max(max_tokens * 2, 8000),
        )
    except ValidationError as exc:  # JSON cortado: la respuesta quedó incompleta
        raise RuntimeError(f"Respuesta incompleta o mal formada; subir el límite ({exc.errors()[0].get('type')})") from exc
    usage.add(getattr(response, "model", model) or model, getattr(response, "usage", None))
    if getattr(response, "status", "") == "incomplete":
        reason = getattr(getattr(response, "incomplete_details", None), "reason", "")
        if reason == "content_filter":
            raise RefusedError("El modelo rechazó el pedido (filtro de contenido)")
        raise RuntimeError(f"Respuesta incompleta ({reason}); subir el límite")
    for item in getattr(response, "output", []) or []:
        for part in getattr(item, "content", None) or []:
            if getattr(part, "type", "") == "refusal":
                raise RefusedError(f"El modelo rechazó el pedido: {getattr(part, 'refusal', '')}")
    parsed = response.output_parsed
    if parsed is None:
        raise RuntimeError("La respuesta no trajo el JSON esperado")
    return parsed
