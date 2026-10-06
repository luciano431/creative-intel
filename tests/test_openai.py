"""Proveedor OpenAI: pedido real del SDK contra un servidor simulado (sin red ni gasto)."""
import json

import httpx
import pytest
from pydantic import BaseModel

from creative_intel.ai import client as ai


class Out(BaseModel):
    angle: str
    offer: str | None


def _fake_openai(monkeypatch, body: dict, seen: dict, status: int = 200):
    from openai import OpenAI

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["json"] = json.loads(request.content)
        return httpx.Response(status, json=body)

    fake = OpenAI(api_key="sk-test", http_client=httpx.Client(transport=httpx.MockTransport(handler)), max_retries=0)
    monkeypatch.setattr(ai, "_openai_client", fake)
    monkeypatch.setattr(ai.settings, "ai_provider", "openai")


def _response(text: str, status: str = "completed") -> dict:
    return {
        "id": "resp_1", "object": "response", "created_at": 0, "status": status, "model": "gpt-5.4-mini-2026-03-17",
        "output": [{"type": "message", "id": "m1", "role": "assistant", "status": "completed",
                    "content": [{"type": "output_text", "text": text, "annotations": []}]}],
        "parallel_tool_calls": False, "tool_choice": "auto", "tools": [],
        "usage": {"input_tokens": 1000, "input_tokens_details": {"cached_tokens": 400}, "output_tokens": 200,
                  "output_tokens_details": {"reasoning_tokens": 50}, "total_tokens": 1200},
    }


def test_openai_structured_call_sends_image_and_schema(monkeypatch):
    seen = {}
    _fake_openai(monkeypatch, _response('{"angle": "durabilidad", "offer": null}'), seen)
    ai.usage.reset()
    content = [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}},
               {"type": "text", "text": "Anuncio X"}]
    out = ai.structured_call(Out, "sistema", content, model="gpt-5.4-mini", max_tokens=2000, effort="low")
    assert out == Out(angle="durabilidad", offer=None)
    body = seen["json"]
    assert seen["url"].endswith("/responses") and body["instructions"] == "sistema" and body["reasoning"] == {"effort": "low"}
    parts = body["input"][0]["content"]
    assert parts[0] == {"type": "input_image", "image_url": "data:image/png;base64,AAAA", "detail": "low"}
    assert parts[1] == {"type": "input_text", "text": "Anuncio X"}
    assert body["text"]["format"]["type"] == "json_schema" and body["text"]["format"]["strict"] is True
    # 600 sin caché a 0,75 + 400 cacheados a 0,075 + 200 de salida a 4,50 (por millón)
    assert ai.usage.usd == pytest.approx((600 * 0.75 + 400 * 0.075 + 200 * 4.50) / 1e6)


def test_openai_incomplete_response_raises(monkeypatch):
    seen = {}
    body = _response('{"angle": "x"', status="incomplete")
    body["incomplete_details"] = {"reason": "max_output_tokens"}
    _fake_openai(monkeypatch, body, seen)
    with pytest.raises(RuntimeError, match="incompleta"):
        ai.structured_call(Out, "s", "t", model="gpt-5.4-mini")


def test_openai_no_quota_is_explained(monkeypatch):
    seen = {}
    err = {"error": {"message": "You exceeded your current quota, please check your plan and billing details.",
                     "type": "insufficient_quota", "code": "insufficient_quota"}}
    _fake_openai(monkeypatch, err, seen, status=429)
    with pytest.raises(ai.ApiUnavailable, match="OpenAI no tiene saldo"):
        ai.check_api("gpt-5.4-mini")


def test_price_uses_longest_model_prefix():
    t = ai.UsageTracker()

    class U:
        input_tokens, output_tokens = 1_000_000, 0

    assert t.add("gpt-5.4-mini-2026-03-17", U()) == pytest.approx(0.75)
    assert t.add("gpt-5.4-2026-03-05", U()) == pytest.approx(2.50)


def test_key_with_pasted_label_is_cleaned(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "api de ranker: sk-proj-ABCdef_123-456789012345678901234 ")
    monkeypatch.setattr(ai, "_openai_client", None)
    assert ai.get_openai_client().api_key == "sk-proj-ABCdef_123-456789012345678901234"
    monkeypatch.setattr(ai, "_openai_client", None)
