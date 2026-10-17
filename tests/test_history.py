"""Informe «Qué hicimos»: la pauta propia por año, sin red (Meta simulada) y sin IA."""
import datetime as dt
import json

import httpx

from creative_intel import history
from creative_intel.config import CompetitorConfig


def test_commercial_dates_are_computed_for_any_year():
    assert history._black_friday(2024) == dt.date(2024, 11, 29)
    assert history._black_friday(2025) == dt.date(2025, 11, 28)
    assert history._nth_sunday(2025, 5, 2) == dt.date(2025, 5, 11)  # Día de la Madre en Uruguay
    wins = history.event_windows([2024, 2025], dt.date(2026, 10, 17))
    bf = [w for w in wins if w["key"] == "black-friday"]
    assert [w["start"] for w in bf] == [dt.date(2024, 11, 29), dt.date(2025, 11, 28)]
    assert bf[0]["from"] == dt.date(2024, 11, 19)
    assert all(w["end"] < dt.date(2026, 10, 17) for w in wins)


def test_totals_never_mix_result_types():
    t = history.totals([
        {"spend": "100", "impressions": "10000", "clicks": "100", "actions": [{"action_type": "purchase", "value": "4"},
                                                                              {"action_type": "link_click", "value": "90"}],
         "action_values": [{"action_type": "purchase", "value": "500"}], "account_currency": "USD"},
        {"spend": "50", "impressions": "5000", "clicks": "50", "actions": [{"action_type": "purchase", "value": "1"}]}])
    assert t["spend"] == 150 and t["ctr"] == 1.0 and t["currency"] == "USD"
    assert t["by_type"]["compras"] == {"total": 5, "cost": 30.0} and t["by_type"]["clics al sitio"]["total"] == 90
    assert t["roas"] == 3.33


def _fake_meta(req: httpx.Request) -> httpx.Response:
    p = dict(req.url.params)
    if req.url.path.endswith("/insights"):
        year = json.loads(p["time_range"])["since"][:4]
        if p.get("level") == "ad":
            rows = [
                {"ad_id": f"{year}1", "ad_name": "Bici hasta 25%", "campaign_name": "Ventas", "spend": "300", "impressions": "30000",
                 "clicks": "600", "ctr": "2", "objective": "OUTCOME_SALES", "optimization_goal": "OFFSITE_CONVERSIONS",
                 "actions": [{"action_type": "purchase", "value": "12"}], "account_currency": "USD"},
                {"ad_id": f"{year}2", "ad_name": "Carrusel Ciberlunes", "campaign_name": "Ventas", "spend": "200", "impressions": "40000",
                 "clicks": "180", "ctr": "0.45", "objective": "OUTCOME_SALES", "optimization_goal": "OFFSITE_CONVERSIONS",
                 "actions": [], "account_currency": "USD"},
                {"ad_id": f"{year}3", "ad_name": "Celular 10%", "campaign_name": "Ventas", "spend": "60", "impressions": "6000",
                 "clicks": "90", "ctr": "1.5", "objective": "OUTCOME_SALES", "optimization_goal": "OFFSITE_CONVERSIONS",
                 "actions": [{"action_type": "purchase", "value": "3"}], "account_currency": "USD"},
            ]
        elif p.get("level") == "campaign":
            rows = [{"campaign_name": "Ventas", "objective": "OUTCOME_SALES", "spend": "560", "impressions": "76000", "clicks": "870",
                     "actions": [{"action_type": "purchase", "value": "15"}], "account_currency": "USD"}]
        elif p.get("time_increment") == "1":
            rows = [{"date_start": f"{year}-{d}", "spend": "25", "impressions": "3000", "clicks": "35",
                     "actions": [{"action_type": "purchase", "value": "1"}], "account_currency": "USD"}
                    for d in ("01-10", "06-01", "11-03", "11-28", "12-24") if f"{year}-{d}" <= "2026-10-17"]
        else:
            rows = [{"spend": "90", "impressions": "11000", "clicks": "130", "actions": [{"action_type": "purchase", "value": "2"}],
                     "account_currency": "USD"}]
        return httpx.Response(200, json={"data": rows})
    return httpx.Response(200, json={"id": req.url.path.rsplit("/", 1)[-1], "effective_status": "PAUSED", "created_time": "2025-11-01T10:00:00-0300",
                                     "creative": {"title": "Asegurá tu bici", "body": "Hasta 25% off", "object_type": "SHARE"}})


def test_history_report_end_to_end_without_ai(tmp_path, monkeypatch):
    from creative_intel.collectors import meta_insights
    from creative_intel.config import settings

    real = meta_insights.MetaInsightsCollector
    monkeypatch.setattr(meta_insights, "MetaInsightsCollector",
                        lambda *a, **k: real(token="t", client=httpx.Client(transport=httpx.MockTransport(_fake_meta))))
    monkeypatch.setattr(settings, "assets_dir", tmp_path / "assets")
    monkeypatch.setattr("creative_intel.meta_token.token_problem", lambda token: "")
    own = CompetitorConfig(slug="miseguro", name="miSeguroOk", country="UY", tier="own", meta_ad_account_id="461")
    out = tmp_path / "rep"
    s = history.run_history(own, [2024, 2025, 2026], mock_ai=True, out_dir=out, today=dt.date(2026, 10, 17))
    html = (out / "report.html").read_text(encoding="utf-8")
    assert set(s["years"]) == {"2024", "2025", "2026"} and s["events"] > 0
    assert "Año contra año" in html and "Mismo tramo de cada año" in html and "Fechas comerciales" in html
    assert "Mejor costo por compra" in html and "Gastaron sin traer lo que buscaban" in html and "Carrusel Ciberlunes" in html
    assert "orgánico no entra" in html
    stored = json.loads((out / "datos.json").read_text(encoding="utf-8"))
    yd = stored["data"]["years"]["2025"]
    assert [a["ad_name"] for a in yd["picks"]["best_purchase"]] == ["Celular 10%", "Bici hasta 25%"]  # US$ 20 vs US$ 25 por compra
    assert [a["ad_name"] for a in yd["picks"]["no_results"]] == ["Carrusel Ciberlunes"]
    assert stored["data"]["details"]["20251"]["title"] == "Asegurá tu bici"
    assert yd["total"]["spend"] == 125 and yd["ytd"]["spend"] == 50  # mismo tramo: hasta el 17/10
    bf = next(e for e in stored["data"]["events"] if e["key"] == "black-friday" and e["year"] == 2025)
    assert bf["spend"] == 25 and bf["top"]
    # Segunda corrida con --reusar: no consulta a Meta
    monkeypatch.setattr(meta_insights, "MetaInsightsCollector", lambda *a, **k: (_ for _ in ()).throw(AssertionError("consultó a Meta")))
    history.run_history(own, [2024, 2025, 2026], mock_ai=True, reuse=True, out_dir=out, today=dt.date(2026, 10, 17))
