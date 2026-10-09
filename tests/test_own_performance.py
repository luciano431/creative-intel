"""Resultados propios del cliente (Marketing API) sin red: parseo y agregación por mensaje."""
from creative_intel.analysis.own_performance import own_performance
from creative_intel.collectors.meta_insights import performance_row, pick_result


def test_result_preference_and_row():
    assert pick_result([{"action_type": "link_click", "value": "40"}, {"action_type": "lead", "value": "4"}]) == ("leads", 4.0)
    row = performance_row({"spend": "100", "impressions": "10000", "clicks": "150", "ctr": "1.5",
                           "actions": [{"action_type": "purchase", "value": "5"}], "action_values": [{"action_type": "purchase", "value": "400"}]})
    assert row["result_type"] == "compras" and row["cost_per_result"] == 20.0 and row["roas"] == 4.0


def _own(i, fam, spend, clicks, results, fmt="imagen"):
    perf = {"spend": spend, "impressions": 1000, "clicks": clicks, "ctr": clicks / 10, "result_type": "leads", "results": results,
            "cost_per_result": round(spend / results, 2) if results else None, "roas": None, "period": {"since": "2026-09-01", "until": "2026-10-01"}}
    return {"id": i, "tier": "own", "format": fmt, "headline": f"ad {i}", "cls": {"angle_family": fam}, "engagement": {"performance": perf}}


def test_own_performance_groups_by_message_and_ranks_efficiency():
    out = own_performance([_own(1, "proteccion", 100, 20, 10), _own(2, "economico", 50, 5, 1), _own(3, "proteccion", 50, 10, 5, fmt="video"),
                           {"id": 9, "tier": "local", "engagement": {}}])
    fam = {r["value"]: r for r in out["by_family"]}
    assert fam["proteccion"]["ads"] == 2 and fam["proteccion"]["cost_per_result"] == 10.0 and fam["proteccion"]["ctr"] == 1.5
    assert out["best"][0]["id"] in (1, 3) and out["total"]["results"] == 16 and out["result_type"] == "leads"
    assert own_performance([{"id": 1, "tier": "own", "engagement": {}}]) is None


def test_insights_collector_with_fake_graph_api():
    import httpx

    from creative_intel.collectors.meta_insights import MetaInsightsCollector
    from creative_intel.config import CompetitorConfig

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/insights"):
            return httpx.Response(200, json={"data": [
                {"ad_id": "11", "ad_name": "Pisos A", "spend": "80", "impressions": "8000", "clicks": "120", "ctr": "1.5",
                 "actions": [{"action_type": "lead", "value": "8"}]},
                {"ad_id": "22", "ad_name": "Pisos B", "spend": "20", "impressions": "3000", "clicks": "15", "ctr": "0.5", "actions": []}]})
        return httpx.Response(200, json={"id": req.url.path.rsplit("/", 1)[-1], "effective_status": "ACTIVE", "created_time": "2026-09-10T10:00:00-0300",
                                         "creative": {"title": "Pisos técnicos", "body": "Instalación propia", "object_type": "VIDEO"}})

    col = MetaInsightsCollector(token="t", client=httpx.Client(transport=httpx.MockTransport(handler)))
    own = CompetitorConfig(slug="verona", name="Verona", country="UY", tier="own", meta_ad_account_id="123")
    assert col.supports(own) and not col.supports(CompetitorConfig(slug="x", name="X", country="UY", tier="local"))
    raws = col.collect(own)
    assert [r.external_id for r in raws] == ["own-11", "own-22"]
    assert raws[0].engagement["performance"]["cost_per_result"] == 10.0 and raws[0].format == "video"


def test_several_ad_accounts_are_summed():
    import httpx

    from creative_intel.collectors.meta_insights import MetaInsightsCollector
    from creative_intel.config import CompetitorConfig

    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/insights"):
            acct = req.url.path.split("/")[-2]
            seen.append(acct)
            rows = [{"ad_id": acct[-1], "ad_name": "x", "spend": "10", "impressions": "100", "clicks": "1", "ctr": "1", "actions": []}] if acct == "act_2" else []
            return httpx.Response(200, json={"data": rows})
        return httpx.Response(200, json={"id": "2", "creative": {}})

    assert MetaInsightsCollector.accounts("act_1, 2") == ["act_1", "act_2"]
    col = MetaInsightsCollector(token="t", client=httpx.Client(transport=httpx.MockTransport(handler)))
    raws = col.collect(CompetitorConfig(slug="c", name="C", country="UY", tier="own", meta_ad_account_id="act_1,act_2"))
    assert seen == ["act_1", "act_2"] and len(raws) == 1


def test_execution_audit_and_gaps():
    from creative_intel.analysis.execution import execution_gaps, execution_profile, own_audit

    own = [{"id": i, "platform": "meta", "source": "meta_web", "headline": "h",
            "cls": {"primary_angle": "familia", "angle_family": "emocional", "people": "sin_personas", "shows_price": False,
                    "cta_channel": "web", "execution": "placa_grafica", "talks_to": "padre", "copy_quote": "Su primer celular"}} for i in range(4)]
    own.append({"id": 9, "platform": "meta", "source": "meta_insights", "cls": {}})
    refs = [{"id": 20 + i, "cls": {"people": "persona_a_camara" if i < 2 else "sin_personas", "shows_price": i == 0,
                                   "cta_channel": "whatsapp", "execution": "video_persona"}} for i in range(4)]
    a = own_audit(own)
    assert a["active"] == 4 and a["angles"][0]["example"] == "Su primer celular" and a["profile"]["talks_to"][0] == {"value": "padre", "count": 4}
    gaps = execution_gaps(a["profile"], execution_profile(refs))
    by = {g["dimension"]: g for g in gaps}
    assert by["CTA a WhatsApp"]["own"] == "0 de 4" and by["CTA a WhatsApp"]["refs"] == "4 de 4" and gaps[0]["dimension"] == "CTA a WhatsApp"
    assert "Resolución dominante: placa gráfica" in by


def test_deleted_ad_and_rejected_fields_do_not_drop_the_account():
    import httpx

    from creative_intel.collectors.meta_insights import MetaInsightsCollector
    from creative_intel.config import CompetitorConfig

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/insights"):
            if "objective" in req.url.params.get("fields", ""):
                return httpx.Response(400, json={"error": {"message": "campo no válido"}})
            return httpx.Response(200, json={"data": [
                {"ad_id": "11", "ad_name": "Notebook", "spend": "500", "impressions": "8000", "clicks": "120", "ctr": "1.5",
                 "account_currency": "UYU", "actions": [{"action_type": "link_click", "value": "100"}]}]})
        if req.url.path.endswith("/previews"):
            return httpx.Response(200, json={"data": []})
        return httpx.Response(400, json={"error": {"message": "Object with ID '11' does not exist"}})

    col = MetaInsightsCollector(token="t", client=httpx.Client(transport=httpx.MockTransport(handler)))
    import datetime as dt

    raws = col.collect_window(CompetitorConfig(slug="dw", name="DW", country="UY", tier="own", meta_ad_account_id="1"), "UY",
                              dt.date(2026, 5, 11), dt.date(2026, 6, 10), 15)
    assert len(raws) == 1 and raws[0].headline == "Notebook"
    assert raws[0].engagement["performance"]["currency"] == "UYU" and raws[0].engagement["performance"]["results"] == 100


def test_currency_symbol():
    from creative_intel.events import currency_symbol

    assert currency_symbol("UYU") == "$" and currency_symbol("USD") == "US$" and currency_symbol(None) == "US$"


def test_write_env_replaces_only_given_keys(tmp_path):
    from creative_intel.meta_token import write_env

    env = tmp_path / ".env"
    env.write_text("OPENAI_API_KEY=x\n# META_ACCESS_TOKEN=viejo comentado\nMETA_ACCESS_TOKEN=viejo\n")
    write_env({"META_ACCESS_TOKEN": "nuevo", "INSTAGRAM_ACCESS_TOKEN": "nuevo"}, env)
    assert env.read_text() == ("OPENAI_API_KEY=x\n# META_ACCESS_TOKEN=viejo comentado\nMETA_ACCESS_TOKEN=nuevo\n"
                               "INSTAGRAM_ACCESS_TOKEN=nuevo\n")
