"""Tests de la capa IA sin llamar a la API: esquema dinámico, heurística y llamada estructurada mockeada."""
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from creative_intel.ai.classify import build_classification_model, heuristic_classify
from creative_intel.ai.client import RefusedError, structured_call
from creative_intel.ai.synthesize import SynthesisOutput, mock_synthesis
from creative_intel.db import Creative, get_or_create_run
from creative_intel.normalize import upsert_creatives
from creative_intel.pipeline import analyze, classify, items_for


def test_dynamic_schema_enforces_taxonomy(taxonomy):
    Model = build_classification_model(taxonomy)
    schema = Model.model_json_schema()
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    ok = Model(product="celular", product_shown="celular", format_detected="video", placement_guess="reels", hook_type="pregunta", primary_angle="robo",
               secondary_angle=None, pain_point="robo", promise="reposición", offer=None, funnel_stage="mofu", cta_type="cotizar",
               creative_style="ugc", audience_hypothesis="jóvenes", tone="directo", key_claims=["48 hs"], summary="x", confidence="high")
    assert ok.primary_angle == "robo"
    with pytest.raises(ValidationError):
        Model(**{**ok.model_dump(), "primary_angle": "angulo_inventado"})


def test_heuristic_classifier_stays_inside_taxonomy(session, sample_raws, taxonomy, today):
    run = get_or_create_run(session, "2026-09")
    upsert_creatives(session, run, sample_raws, today)
    Model = build_classification_model(taxonomy)
    for c in session.query(Creative).all():
        data = heuristic_classify(c, taxonomy)
        fam = data.pop("angle_family")
        Model(**data)  # valida enums
        assert fam == taxonomy.family_of(data["primary_angle"])
    sura = session.query(Creative).filter_by(external_id="demo-sura-001").one()
    d = heuristic_classify(sura, taxonomy)
    assert d["product"] == "celular" and d["primary_angle"] == "robo" and d["hook_type"] == "pregunta"


class _FakeMessages:
    def __init__(self, text, stop_reason="end_turn"):
        self.text, self.stop_reason, self.calls = text, stop_reason, []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(stop_reason=self.stop_reason, stop_details=SimpleNamespace(category="cyber", explanation=""),
                               content=[SimpleNamespace(type="text", text=self.text)])


def _fake_client(text, stop_reason="end_turn"):
    msgs = _FakeMessages(text, stop_reason)
    return SimpleNamespace(beta=SimpleNamespace(messages=msgs)), msgs


def test_structured_call_sends_schema_and_fallbacks(taxonomy):
    Model = build_classification_model(taxonomy)
    payload = dict(product="auto", product_shown="auto", format_detected="texto", placement_guess="search", hook_type="precio", primary_angle="precio",
                   secondary_angle=None, pain_point="precio", promise="ahorro", offer="20%", funnel_stage="bofu", cta_type="cotizar",
                   creative_style="texto_plano", audience_hypothesis="x", tone="directo", key_claims=[], summary="s", confidence="medium")
    client, msgs = _fake_client(json.dumps(payload))
    out = structured_call(Model, "sys", "hola", model="claude-opus-5-5", client=client)
    assert out.primary_angle == "precio"
    kw = msgs.calls[0]
    assert kw["output_config"]["format"]["type"] == "json_schema"
    assert kw["fallbacks"] == "default" and "server-side-fallback-2026-07-01" in kw["betas"]
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}


def test_structured_call_raises_on_refusal(taxonomy):
    Model = build_classification_model(taxonomy)
    client, _ = _fake_client("{}", stop_reason="refusal")
    with pytest.raises(RefusedError):
        structured_call(Model, "sys", "hola", model="claude-opus-5-5", client=client)


def test_mock_synthesis_validates_against_output_schema(session, sample_raws, taxonomy, today):
    run = get_or_create_run(session, "2026-09")
    upsert_creatives(session, run, sample_raws, today)
    classify(session, "2026-09", mock=True)
    metrics = analyze(session, "2026-09")
    items = items_for(session, run, taxonomy)
    data = mock_synthesis(metrics, items, metrics["concepts"], "2026-09")
    SynthesisOutput.model_validate(data)
    assert len(data["executive_summary"]) == 3
    assert all(any(ch.isdigit() for ch in s["detail"] + s["headline"]) for s in data["executive_summary"]), "cada punto cita cifras"
    assert all(s["headline"] and s["action"] for s in data["executive_summary"])
    def texts(o):
        if isinstance(o, dict):
            return " ".join(texts(v) for k, v in o.items() if k not in ("hook_type", "angle", "format"))
        if isinstance(o, list):
            return " ".join(texts(v) for v in o)
        return str(o)

    joined = texts(data)
    for jerga in ("white space", "TOFU", "tofu", "hook"):
        assert jerga not in joined, f"jerga en la síntesis: {jerga}"


def test_classify_stops_on_credit_error_and_retries_only_failed(session, sample_raws, taxonomy, today, monkeypatch):
    import creative_intel.pipeline as pipeline

    run = get_or_create_run(session, "2026-09")
    upsert_creatives(session, run, sample_raws, today)
    total = len(sample_raws)
    calls = {"n": 0}

    def flaky(creative, tax):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("Error 500: overloaded")  # error puntual: cae a heurística y sigue
        if calls["n"] == 3:
            raise RuntimeError("Your credit balance is too low to access the Anthropic API.")
        return heuristic_classify(creative, tax)

    monkeypatch.setattr(pipeline, "classify_creative", flaky)
    stats = classify(session, "2026-09")
    assert stats["aborted"] and calls["n"] == 3, "frena en el primer error de crédito, no recorre todo"
    assert stats["classified"] == 2 and stats["fallback"] == 1

    monkeypatch.setattr(pipeline, "classify_creative", lambda c, t: heuristic_classify(c, t))
    stats = classify(session, "2026-09", retry_failed=True)
    assert stats["classified"] == total - 1, "reintenta el fallback y los que faltaban; no repite el que salió bien"
    assert stats["skipped"] == 1
    assert not any(pipeline._is_fallback(c.latest_classification) for c in pipeline.run_creatives(session, run))


def test_mock_ideas_are_ready_to_produce(session, sample_raws, taxonomy, today):
    run = get_or_create_run(session, "2026-09")
    upsert_creatives(session, run, sample_raws, today)
    classify(session, "2026-09", mock=True)
    metrics = analyze(session, "2026-09")
    data = mock_synthesis(metrics, items_for(session, run, taxonomy), metrics["concepts"], "2026-09")
    ideas = [c for o in data["white_space"] for c in o["concepts"]]
    assert ideas
    for c in ideas:
        assert c["title"] and c["hook_line"] and c["cta"]
        assert len(c["script"]) >= 4
        assert "[" not in c["copy_draft"] and "equipo creativo" not in c["copy_draft"].lower()


def test_heuristic_works_with_another_industry_taxonomy(taxonomy):
    from types import SimpleNamespace

    from creative_intel.config import load_taxonomy, ROOT

    t = load_taxonomy(ROOT / "config" / "clients" / "adeleste" / "taxonomy.yaml")
    ad = SimpleNamespace(headline="Juegos de plaza para tu patio", body_text="Hasta 12 cuotas sin recargo", description="",
                         cta_text="", format="imagen", placements=["feed"])
    d = heuristic_classify(ad, t)
    assert d["primary_angle"] in t.angles and d["product"] == "juegos_de_plaza"
    plain = heuristic_classify(SimpleNamespace(headline="Hola", body_text="", description="", cta_text="", format="imagen", placements=[]), t)
    assert plain["primary_angle"] in t.angles and plain["product"] in t.products
    assert t.label("diseno") == "Diseño"


def test_usage_tracker_prices_and_budget_stop(session, sample_raws, taxonomy, today, monkeypatch):
    from types import SimpleNamespace

    import creative_intel.pipeline as pipeline
    from creative_intel.ai.client import usage
    from creative_intel.config import settings

    usage.reset()
    cost = usage.add("claude-sonnet-5-5", SimpleNamespace(input_tokens=1_000_000, output_tokens=100_000))
    assert round(cost, 2) == 3.00 and "US$ 3.00" in usage.summary()

    run = get_or_create_run(session, "2026-09")
    upsert_creatives(session, run, sample_raws, today)

    def pricey(creative, tax):
        usage.add("claude-sonnet-5-5", SimpleNamespace(input_tokens=300_000, output_tokens=0))  # US$ 0.60 por anuncio
        return heuristic_classify(creative, tax)

    monkeypatch.setattr(pipeline, "classify_creative", pricey)
    monkeypatch.setattr(settings, "max_ai_usd_per_run", 1.00)
    stats = classify(session, "2026-09")
    assert stats["classified"] == 2 and "tope de gasto" in stats["aborted"], "frena al pasar US$ 1"
    assert stats["usd"] == 1.2


def test_meta_asks_for_the_most_seen_ads():
    from creative_intel.collectors.meta_web import MetaAdLibraryWebCollector
    from creative_intel.config import CompetitorConfig, settings

    c = CompetitorConfig(slug="x", name="X", country="UY", tier="local", meta_page_id="123")
    col = MetaAdLibraryWebCollector()
    assert "sort_data[mode]=total_impressions" in col.url_for(c, "UY") and col.max_cards == settings.top_ads_per_brand


def test_client_brief_reaches_synthesis_payload(tmp_path, monkeypatch):
    import importlib

    syn = importlib.import_module("creative_intel.ai.synthesize")
    from creative_intel.config import load_client

    cfg = tmp_path / "competitors.yaml"
    cfg.write_text("client:\n  slug: v\n  name: Verona\n  brief:\n    tono: Voseo B2B\n    promos: []\n    evitar: parecer barata\ncountries: []\n", encoding="utf-8")
    ci = load_client(cfg)
    assert ci.brief == {"tono": "Voseo B2B", "evitar": "parecer barata"}
    seen = {}

    def fake_call(model_cls, system, content, **kw):
        seen["system"], seen["text"] = system, content[0]["text"]
        raise RuntimeError("stop")

    monkeypatch.setattr(syn, "structured_call", fake_call)
    with pytest.raises(RuntimeError):
        syn.synthesize({}, [], [], "2026-10", client_info=ci)
    assert '"client_brief": {"tono": "Voseo B2B"' in seen["text"] and "client_brief" in seen["system"]


def test_api_problem_explains_credit_and_key_errors():
    from creative_intel.ai.client import api_problem

    assert "crédito" in api_problem(Exception("Error code: 400 - Your credit balance is too low to access the Anthropic API"))
    assert "clave" in api_problem(Exception("Could not resolve authentication method"))
    assert api_problem(Exception("overloaded")) is None


def test_run_cycle_stops_before_collecting_without_credit(monkeypatch):
    from creative_intel import pipeline
    from creative_intel.ai import client as ai_client

    def no_credit(model=None):
        raise ai_client.ApiUnavailable("sin crédito")

    monkeypatch.setattr(ai_client, "check_api", no_credit)
    monkeypatch.setattr(pipeline, "collect", lambda *a, **k: pytest.fail("no debería recolectar"))
    with pytest.raises(ai_client.ApiUnavailable):
        pipeline.run_cycle(None, "2026-10")


def test_no_synthesis_without_competitor_ads(tmp_path, monkeypatch):
    from creative_intel import pipeline
    from creative_intel.db import init_db, session_scope

    url = f"sqlite:///{tmp_path / 'x.db'}"
    init_db(url)
    called = []
    monkeypatch.setattr(pipeline, "synthesize", lambda *a, **k: called.append(1))
    with session_scope(url) as s:
        pipeline.sync(s)
        with pytest.raises(pipeline.NoData):
            pipeline.synthesize_run(s, "2026-10")
    assert not called


def test_previous_month_of_other_client_is_ignored():
    from creative_intel import pipeline
    from creative_intel.config import load_competitors

    name = load_competitors()[0].name
    assert pipeline._same_universe({"totals": {"by_competitor": {"Otra marca": 5}}}) is None
    ok = {"totals": {"by_competitor": {name: 5}}}
    assert pipeline._same_universe(ok) is ok


def test_gemini_concepts_replace_ideas_by_title(monkeypatch):
    from creative_intel import pipeline

    idea = dict(title="Asado en el fondo", format="video", hook_type="demostracion", angle="parrilla_y_reuniones",
                hook_line="Se ve la parrilla prendida", script=["0–3 s · Se ve: brasas"], copy_draft="Parrilla de hierro", cta="Comprá",
                visual_direction="Fondo de casa, tarde")
    monkeypatch.setattr("creative_intel.ai.creative.write_ideas", lambda ci, ctx, req: [{**idea, "for_what": "Parrillas a medida"}])
    data = {"white_space": [{"title": "Parrillas a medida", "concepts": []}], "recommendations": [{"title": "Otra", "concepts": [{"title": "x"}]}]}
    out = pipeline.gemini_concepts(data, {}, [], [])
    assert out["white_space"][0]["concepts"][0]["title"] == "Asado en el fondo" and "for_what" not in out["white_space"][0]["concepts"][0]
    assert out["recommendations"][0]["concepts"] == [{"title": "x"}] and out["ideas_by"] == "Gemini"


def test_gemini_disabled_without_key(monkeypatch):
    from creative_intel.ai import creative
    from creative_intel.config import settings

    monkeypatch.setattr(settings, "gemini_api_key", "")
    assert creative.write_ideas(None, {}, "x") is None
