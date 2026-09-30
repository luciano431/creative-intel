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
    ok = Model(product="celular", format_detected="video", placement_guess="reels", hook_type="pregunta", primary_angle="robo",
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
    payload = dict(product="auto", format_detected="texto", placement_guess="search", hook_type="precio", primary_angle="precio",
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
    assert all(any(ch.isdigit() for ch in s) for s in data["executive_summary"]), "cada afirmación cita cifras"
