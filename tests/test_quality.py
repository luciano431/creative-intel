"""Muestra que ve la IA y recuadro de cobertura de datos."""
from creative_intel.ai.synthesize import sample_items, sample_note
from creative_intel.analysis.data_coverage import event_coverage, plan_coverage


def _ad(i, brand, days, first, tier="local", spend=0):
    return {"id": i, "competitor": brand, "tier": tier, "days_active": days, "first_seen": first,
            "engagement": {"performance": {"spend": spend}} if spend else {}}


def test_sample_spreads_brands_and_keeps_new_ads():
    big = [_ad(i, "Grande", 300 - i, f"2026-01-{i % 28 + 1:02d}") for i in range(100)]
    new = _ad(500, "Grande", 1, "2026-10-01")
    small = [_ad(600 + i, "Chica", 10, "2026-09-01") for i in range(3)]
    own = [_ad(900, "Cliente", 5, "2026-09-01", "own", spend=50), _ad(901, "Cliente", 90, "2026-05-01", "own", spend=500)]
    picked = sample_items(big + [new] + small + own, 12)
    ids = [it["id"] for it in picked]
    assert ids[:2] == [901, 900]  # propios primero, por inversión
    assert 500 in ids and all(600 + i in ids for i in range(3))  # el más nuevo y la marca chica entran
    note = sample_note(big + [new] + small + own, 12)
    assert note["total"] == 106 and note["sent"] == 12 and note["by_brand"]["Chica"] == {"total": 3, "sent": 3}
    assert sample_items(small, 10) == small


def test_event_coverage_levels():
    full = {"own_results": [{"spend": 1000}] * 5, "own_daily": {"phases": {"durante": {}}}, "ads": 12, "currency": "$",
            "organic": [{"own": True, "posts": 9}, {"own": False, "posts": 4}, {"own": False, "posts": 2}]}
    c = event_coverage(full)
    assert c["level"] == "alta" and c["rows"][0]["detail"] == "5 anuncios, $ 5.000 invertidos" and not c["missing"]
    thin = {"own_results": [], "ads": 0, "organic": [{"own": True, "posts": 3}, {"own": False, "posts": 0, "source": "perfil_publico"}],
            "collect_errors": []}
    c = event_coverage(thin)
    assert c["level"] == "baja" and c["rows"][0]["detail"] == "sin pauta en esas fechas"
    assert "solo se ven las últimas" in c["rows"][-1]["detail"]
    assert plan_coverage({"own_audit": {"active": 4}, "own_performance": {"total": {"ads": 10}}}, 40, 3)["level"] == "alta"


def test_chain_and_nolabel_filters():
    from creative_intel.report.build import chain, nolabel

    html = str(chain("DATO: bici 25% dio 12 compras. LECTURA: oferta simple. HIPÓTESIS: Creemos que <sí>."))
    assert html.count("<p><b>") == 3 and "<b>Hipótesis:</b>" in html and "&lt;sí&gt;" in html
    assert chain("sin etiquetas") == "sin etiquetas"
    assert nolabel("PRUEBA: correr 5 días") == "correr 5 días" and nolabel("Creemos que sí") == "Creemos que sí"


def test_anthropic_call_with_web_search_resumes_pause_turn(monkeypatch):
    from types import SimpleNamespace

    from pydantic import BaseModel

    from creative_intel.ai import client as ai
    from creative_intel.config import settings

    monkeypatch.setattr(settings, "ai_provider", "anthropic")

    class Out(BaseModel):
        ok: bool

    calls = []

    def create(**kw):
        calls.append(kw)
        use = SimpleNamespace(input_tokens=10, output_tokens=5, server_tool_use=SimpleNamespace(web_search_requests=2))
        if len(calls) == 1:
            return SimpleNamespace(stop_reason="pause_turn", model="claude-opus-5-5", usage=use,
                                   content=[SimpleNamespace(type="text", text="buscando…")])
        return SimpleNamespace(stop_reason="end_turn", model="claude-opus-5-5", usage=use,
                               content=[SimpleNamespace(type="text", text="encontré esto"), SimpleNamespace(type="text", text='{"ok": true}')])

    fake = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)))
    out = ai.structured_call(Out, "sys", "hola", model="claude-opus-5-5", client=fake, tools=[{"type": "web_search"}])
    assert out.ok and len(calls) == 2
    assert calls[0]["tools"] == [{"type": "web_search_20260209", "name": "web_search", "max_uses": 8}]
    assert calls[1]["messages"][-1]["role"] == "assistant"
