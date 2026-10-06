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


class BudgetExceeded(RuntimeError):
    pass


# US$ por millón de tokens: (entrada, salida, lectura de caché, escritura de caché)
PRICES = {
    "claude-opus-5-5": (4.0, 20.0, 0.20, 5.0),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20, 2.5),
    "claude-haiku-4-5": (1.0, 5.0, 0.10, 1.25),
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
        price = next((v for k, v in PRICES.items() if model.startswith(k)), PRICES["claude-opus-5-5"])
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
